# 银河麒麟（Kylin V10）裸机 vLLM 部署手册（从零开始 · Qwen3.5-9B 主线）

> 与《模型部署.md》（AutoDL 版）配套的**生产向**手册：目标机器是一台**完全空白**的银河麒麟
> 服务器操作系统 V10（无驱动、无 Python 环境、无任何 AI 组件），从装机状态一步步到三个 vLLM 服务可用。
>
> **前置条件（不满足请先停下来）：**
>
> | 条件 | 检查命令 | 说明 |
> |---|---|---|
> | CPU 架构 x86_64 | `uname -m` | **ARM 版麒麟（飞腾/鲲鹏）没有 vLLM 官方 wheel**，NVIDIA 生态也不同，本文不覆盖 |
> | NVIDIA 显卡（主线 1× RTX 4090 24G） | `lspci \| grep -i nvidia` | 昇腾/海光 DCU 等国产卡不走 vLLM 标准路线（昇腾用 MindIE），本文不覆盖 |
> | root 或 sudo 权限 | `whoami` | 装驱动、systemd、防火墙都要 |
> | 数据盘 ≥ 60G | `df -h` | 三个模型 BF16 权重 ~50G + 下载余量 |
>
> 模型与显存方案同 AutoDL 版 §0：主线 Qwen3.5-9B BF16 单卡直跑（~18G）；Embedding/Reranker
> 8B 各 ~16G，有独立卡或多卡时上，单卡可先用 0.6B 小杯分时验证。

---

## 0. 全流程一览

```
① 系统准备        架构/内核源确认、数据盘挂到 /data         （§1，~10 分钟）
② NVIDIA 驱动     屏蔽 nouveau → .run 安装 → nvidia-smi     （§2，需重启一次）
③ Python + vLLM   Miniconda(py3.12) → pip vllm ≥0.17       （§3，~10 分钟）
④ 下载模型        ModelScope 三模型 → /data/models          （§4，~50G）
⑤~⑦ 三个服务      vllm serve → 8000 / 8100 / 8200          （§5~§7，同 AutoDL 版）
⑧ 生产化编排      systemd 托管 + firewalld 放行端口          （§8）
⑨ 接入平台        backend/.env 指向 vLLM 端点                （§9，同 AutoDL 版 §8）
```

**与 AutoDL 版的关键差异（AutoDL 镜像已替你做掉的事，本手册全部要自己做）：**

| 事项 | AutoDL 版 | 麒麟裸机版 |
|---|---|---|
| NVIDIA 驱动 | 镜像自带 | **自己装**（§2）：禁 nouveau、内核头文件、`.run` |
| CUDA | 镜像自带 | **不用装 CUDA Toolkit**——pip 装的 torch wheel 自带运行时，只需驱动 ≥570 |
| Python | 镜像 3.12 | 麒麟自带 3.7 太老，**Miniconda 建 3.12**（§3），不动系统 Python |
| 网络加速 | `source /etc/network_turbo` | **没这回事**；pip 走清华源，模型走 ModelScope 国内直连 |
| HF 兜底镜像 | `HF_ENDPOINT=hf-mirror.com` | 同样可用（transformers 兜底下载时才需要，`HF_HUB_DISABLE_XET=1` 同样要设） |
| 模型目录 | `/root/autodl-tmp/models` | `/data/models`（独立数据盘；路径本身随意，统一即可） |
| 服务保活 | `nohup` 脚本 | **systemd 托管**：开机自启、崩溃自拉、`journalctl` 看日志 |
| 对外端口 | 只映射一个端口、SSH 隧道 | 内网直通，**防火墙放行即可**（§8.2） |
| 计费/释放 | 按量计费、15 天释放 | 不存在，机器常驻 |

---

## 1. 系统准备

### 1.1 确认系统与编译依赖

```bash
cat /etc/kylin-release          # 预期：Kylin Linux Advanced Server release V10 (…)
uname -m                        # 必须是 x86_64（ARM 版到此为止，换机器）
uname -r                        # 记下内核版本，装驱动编译依赖要用
```

> 银河麒麟**服务器版** V10 是 rpm 系（`dnf`/`yum`）；**桌面版** V10 是 deb 系（`apt`）。
> 本手册主线按服务器版写，桌面版差异在注释标注。
> 若 `dnf install` 报找不到包（服务器常配内网源或未接源），可把系统 ISO 挂成本地源：
> `mount -o loop Kylin-Server-V10-xxx.iso /mnt/cdrom` 后将 `/mnt/cdrom` 配成 `dnf` 的 `file://` 源。

```bash
# 编译驱动必需：gcc + 与当前内核严格匹配的 kernel-devel/headers（服务器版）
dnf install -y gcc gcc-c++ make tar bzip2 \
  kernel-devel-$(uname -r) kernel-headers-$(uname -r)

# 验证 devel 与运行内核完全一致（不一致 .run 编译必失败）
ls /lib/modules/$(uname -r)/build >/dev/null && echo "kernel build dir OK"

# 桌面版（apt 系）等价命令：
# apt update && apt install -y gcc make build-essential linux-headers-$(uname -r)
```

> 坑：内核若被 `dnf update` 升级过，源里可能没有旧版本 devel 包。要么补装匹配版本，
> 要么把内核一起升到源里最新（`dnf install -y kernel kernel-devel` 后**重启**再装驱动）。
> 原则：`uname -r` 与 `/lib/modules/` 下的 devel 目录一字不差。

### 1.2 数据盘挂载到 /data（已有大分区可跳过）

```bash
lsblk                           # 找到未挂载的大盘，如 /dev/sdb
mkfs.xfs /dev/sdb               # 格式化（盘上有数据则跳过！）
mkdir -p /data
mount /dev/sdb /data
echo '/dev/sdb /data xfs defaults 0 0' >> /etc/fstab   # 重启自动挂载
df -h /data                     # 预期 ≥ 60G
```

### 1.3 BIOS 关闭 Secure Boot

驱动以内核模块方式加载，Secure Boot 开着会拒绝未签名模块。开机进 BIOS/UEFI 关闭；
能用 `mokutil --sb-state` 查询的机器显示 `SecureBoot disabled` 即可。

---

## 2. NVIDIA 驱动安装

### 2.1 屏蔽开源驱动 nouveau（麒麟与 Ubuntu 一样默认加载）

```bash
cat >/etc/modprobe.d/blacklist-nouveau.conf <<'EOF'
blacklist nouveau
options nouveau modeset=0
EOF

dracut --force                  # 重建 initramfs（服务器版 rpm 系）
# 桌面版（deb 系）改用：update-initramfs -u

reboot
```

重启后验证（**无输出**才是干净）：

```bash
lsmod | grep nouveau
```

### 2.2 安装官方驱动

到 https://www.nvidia.com/drivers 选卡下载 **Production Branch（570/580 系列）** 的
`NVIDIA-Linux-x86_64-*.run`，scp/U 盘传到机器，然后：

```bash
chmod +x NVIDIA-Linux-x86_64-*.run
sh NVIDIA-Linux-x86_64-*.run
# 交互项：DKMS 注册选 Yes（以后升内核自动重编译模块）；32-bit 兼容库选 No（服务器不需要）
```

> **驱动版本下限**：vLLM 0.17 依赖的 torch cu128 构建要求驱动 **≥ 570.26**。
> 装太旧（如 535）会在 torch 初始化时报 `CUDA driver version is insufficient`。
> 有桌面环境报 X server 运行中：`init 3` 切到字符界面再装。

### 2.3 验证

```bash
nvidia-smi
# 预期：Driver Version: 570.xx+ ，CUDA Version: 12.8
```

> 注意 nvidia-smi 右上角的 "CUDA Version" 只是**驱动支持的最高 CUDA 运行时版本**，
> 不代表装了 CUDA Toolkit——本方案从头到尾不需要装 Toolkit（torch wheel 自带）。

---

## 3. Miniconda + vLLM 环境

麒麟 V10 自带 Python 3.7，不满足 vLLM 要求（≥3.9），用 Miniconda 独立建 3.12 环境，
**不动系统 Python**（麒麟部分系统工具依赖自带解释器，乱动会伤系统）。

```bash
# 清华镜像下载（官方源国内慢）
wget https://mirrors.tuna.tsinghua.edu.cn/anaconda/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash Miniconda3-latest-Linux-x86_64.sh -b -p /opt/miniconda3
/opt/miniconda3/bin/conda init bash && source ~/.bashrc

conda create -n vllm python=3.12 -y
conda activate vllm

# pip 换清华源（AutoDL 的 network_turbo 在生产机上不存在）
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

# 装 uv（vLLM 官方推荐安装器）：--torch-backend 是 uv 的参数，pip 没有此选项（写了必报 no such option）
pip install -U uv

# 装 vLLM：--torch-backend=auto 按本机 CUDA 自动选 torch cu128 构建
# 两个必带参数：--python 指向当前 conda 环境解释器（uv 默认只认 venv/.venv，conda 环境必须显式指）；
# --index-url 显式给清华源（uv 不读 pip.conf）
uv pip install vllm --torch-backend=auto --python $(which python) \
  --index-url https://pypi.tuna.tsinghua.edu.cn/simple

# 验证：版本 + GPU 可见
vllm --version
python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.device_count())"
# 预期：2.x+cu128 True <显卡数>；False 说明 torch 是 CPU 版或驱动太旧
```

> 坑：若报 `undefined symbol` / `libcudart.so` 类错，按
> `python -c "import torch; print(torch.version.cuda)"` 的实际版本到
> https://download.pytorch.org/whl 重装对应构建（与 AutoDL 版 §2 同一处理）。
>
> **完全离线内网**的做法：外网机 `pip download vllm -d pkgs/`（同平台同 Python 版本），
> 内网机 `pip install --no-index --find-links=pkgs/ vllm`。

---

## 4. 下载模型（ModelScope，国内直连）

```bash
pip install -U modelscope

mkdir -p /data/models && cd /data/models
# 主模型（~18G BF16；显存紧张可改下 GPTQ-Int4 量化版 Qwen3.5-9B-GPTQ-Int4，仅 ~6G）
modelscope download --model Qwen/Qwen3.5-9B --local_dir Qwen3.5-9B

# 嵌入 + 精排（各 ~16G；单卡方案可先下 0.6B 小杯：Qwen3-Embedding-0.6B / Qwen3-Reranker-0.6B）
modelscope download --model Qwen/Qwen3-Embedding-8B --local_dir Qwen3-Embedding-8B
modelscope download --model Qwen/Qwen3-Reranker-8B  --local_dir Qwen3-Reranker-8B
```

**下载完先验完整性**（缺文件启动必失败，省得白等加载）：

```bash
for d in Qwen3.5-9B Qwen3-Embedding-8B Qwen3-Reranker-8B; do
  ls /data/models/$d | grep -E "config.json|tokenizer" && du -sh /data/models/$d
done
# 每个目录必须有 config.json + tokenizer 文件 + 若干 safetensors 分片，体积与官方页对得上
```

---

## 5. 部署 Qwen3.5-9B（主模型，端口 8000）

```bash
vllm serve /data/models/Qwen3.5-9B \
  --host 0.0.0.0 --port 8000 \
  --served-model-name qwen3.5-9b \
  --max-model-len 32768 \
  --gpu-memory-utilization 0.90 \
  --enable-prefix-caching
```

> 9B BF16 权重 ~18G，单卡 24G 直跑——不量化、不并行、一条命令起服务。
> 多卡撑长上下文加 `--tensor-parallel-size <卡数>`、调大 `--max-model-len`。
> 关键参数速查与思考模式说明见 AutoDL 版 §4.2~§4.3，此处不赘述。

验证：

```bash
curl -s http://127.0.0.1:8000/v1/models | head -c 300

curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "qwen3.5-9b",
       "messages": [{"role": "user", "content": "用一句话解释 vLLM 的 PagedAttention"}]}'
# 思考模式默认开；要快可加 "chat_template_kwargs": {"enable_thinking": false}，温度 0.6~0.7
```

---

## 6. 部署 Qwen3-Embedding-8B（端口 8100）

嵌入模型在 vLLM 里不是对话模型，用 **`--task embed`** 声明池化任务：

```bash
vllm serve /data/models/Qwen3-Embedding-8B \
  --host 0.0.0.0 --port 8100 \
  --served-model-name qwen3-embedding-8b \
  --task embed \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.85
```

```bash
curl http://127.0.0.1:8100/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{"model": "qwen3-embedding-8b", "input": "ontology 平台"}'
# 返回 data[0].embedding，长度 4096（8B=4096 维；0.6B=1024）
```

> 指令感知（查询侧带 instruction、文档侧不带）与 MRL 降维（`"dimensions": 1024`，
> 换维度必须全库重建）两个特性详见 AutoDL 版 §5。

---

## 7. 部署 Qwen3-Reranker-8B（端口 8200）

按序列分类加载，**必须用 `--hf_overrides` 覆盖三个字段**（缺了加载失败或分数全错）：

```bash
vllm serve /data/models/Qwen3-Reranker-8B \
  --host 0.0.0.0 --port 8200 \
  --served-model-name qwen3-reranker-8b \
  --task score \
  --max-model-len 4096 \
  --gpu-memory-utilization 0.85 \
  --hf_overrides '{"architectures": ["Qwen3ForSequenceClassification"],
                   "classifier_from_token": ["no", "yes"],
                   "is_original_qwen3_reranker": true}'
```

```bash
curl http://127.0.0.1:8200/v1/rerank \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3-reranker-8b",
    "query": "MU5307 航班的正常率",
    "documents": [
      "MU5307 航班今日准点率 92%，平均延误 8 分钟",
      "浦东机场行李提取流程说明",
      "东方航空会员积分规则"
    ]
  }'
# 返回 results 按 relevance_score 降序；score = P("yes"|query,doc) ∈ [0,1]
```

> 三字段含义（分类头加载 / yes-no 标签 / 服务端包模板）见 AutoDL 版 §6 表格。

---

## 8. 生产化编排：systemd + 防火墙

生产机不用 `nohup`——改用 systemd：开机自启、崩溃自动拉起、日志进 journald。

### 8.1 三个 systemd 服务

`/etc/systemd/system/vllm-llm.service`（主模型，GPU0）：

```ini
[Unit]
Description=vLLM - Qwen3.5-9B (LLM, :8000)
After=network-online.target

[Service]
Type=simple
Environment=CUDA_VISIBLE_DEVICES=0
ExecStart=/opt/miniconda3/envs/vllm/bin/vllm serve /data/models/Qwen3.5-9B \
  --host 0.0.0.0 --port 8000 \
  --served-model-name qwen3.5-9b \
  --max-model-len 32768 \
  --gpu-memory-utilization 0.90 \
  --enable-prefix-caching
Restart=on-failure
RestartSec=15

[Install]
WantedBy=multi-user.target
```

`vllm-embed.service`（嵌入，GPU1；单卡机器去掉 Environment 行并换 0.6B 路径）：

```ini
[Unit]
Description=vLLM - Qwen3-Embedding-8B (:8100)
After=network-online.target

[Service]
Type=simple
Environment=CUDA_VISIBLE_DEVICES=1
ExecStart=/opt/miniconda3/envs/vllm/bin/vllm serve /data/models/Qwen3-Embedding-8B \
  --host 0.0.0.0 --port 8100 \
  --served-model-name qwen3-embedding-8b \
  --task embed \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.85
Restart=on-failure
RestartSec=15

[Install]
WantedBy=multi-user.target
```

`vllm-rerank.service`（精排，GPU2）：

```ini
[Unit]
Description=vLLM - Qwen3-Reranker-8B (:8200)
After=network-online.target

[Service]
Type=simple
Environment=CUDA_VISIBLE_DEVICES=2
ExecStart=/opt/miniconda3/envs/vllm/bin/vllm serve /data/models/Qwen3-Reranker-8B \
  --host 0.0.0.0 --port 8200 \
  --served-model-name qwen3-reranker-8b \
  --task score \
  --max-model-len 4096 \
  --gpu-memory-utilization 0.85 \
  --hf_overrides={"architectures":["Qwen3ForSequenceClassification"],"classifier_from_token":["no","yes"],"is_original_qwen3_reranker":true}
Restart=on-failure
RestartSec=15

[Install]
WantedBy=multi-user.target
```

> 注意 systemd 的 `ExecStart` 里 `--hf_overrides` 的 JSON **不加引号包裹也能传**（systemd 按空格
> 分词，JSON 内部无空格即可）；若有引号需求可改用 `Environment=` 传参或写包装脚本。

**使用流程（以 vllm-llm 为例，共四步）：**

```bash
# 1. 创建并写入文件
vim /etc/systemd/system/vllm-llm.service

# 2. 必须：让 systemd 重新加载 unit 文件（每次改过文件都要执行，否则改动不生效）
systemctl daemon-reload

# 3. 启动 + 设开机自启
systemctl enable --now vllm-llm

# 4. 验证
systemctl status vllm-llm          # active (running) 即在跑（模型加载要 1~2 分钟）
journalctl -u vllm-llm -f          # 出现 "Application startup complete" 即就绪
curl -s http://127.0.0.1:8000/health -w "%{http_code}\n"   # 200
```

三个服务都确认正常后，一次性启动全部：

```bash
systemctl daemon-reload
systemctl enable --now vllm-llm vllm-embed vllm-rerank

# 就绪轮询（三个 200 即全部就绪）
for p in 8000 8100 8200; do
  echo -n "port $p: "; curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:$p/health
done
```

**systemd 使用坑（按踩坑频率排序）：**

1. **`daemon-reload` 最常被忘**——只改 unit 文件不 reload，systemd 用的还是内存里的旧定义；
   之后每次修改都是「改文件 → `daemon-reload` → `systemctl restart vllm-llm`」三连；
2. **文件名与操作名一致**——文件叫 `vllm-llm.service`，所有 systemctl/journalctl 就用 `vllm-llm`；
3. **续行符后不能有空格**——`ExecStart` 里行尾 `\` 是合法续行，但 `\` 之后跟了空格续行即断，
   报 `Executable path is not absolute` 类错；粘贴后逐行检查；
4. **启动前先自查路径**——`ls /opt/miniconda3/envs/vllm/bin/vllm` 与
   `ls /data/models/Qwen3.5-9B/config.json` 都存在再启动，路径错会进 `failed` 循环重启；
5. **首次验证可不注册自启**——先 `systemctl start vllm-llm`（不带 enable），确认能跑再补
   `systemctl enable vllm-llm`；
6. **改了服务想重启**——`systemctl restart vllm-llm`（stop/start 的合体，日常用它就够）。

排错口诀：起不来先 `journalctl -u vllm-llm -n 100` 看真实报错，90% 是路径打错或参数拼错。

> systemd 直接用 `/opt/miniconda3/envs/vllm/bin/vllm` 绝对路径，绕开 conda activate；
> 想以非 root 用户运行：加 `User=llm` 并 `chown -R llm /data/models`。

### 8.2 防火墙放行（外部访问的前提）

```bash
# 服务器版（firewalld）
firewall-cmd --permanent --add-port=8000-8200/tcp
firewall-cmd --reload
firewall-cmd --list-ports

# 桌面版（ufw，若启用）
# ufw allow 8000:8200/tcp
```

然后内网其他机器直接 `http://<服务器IP>:8000/v1/...` 访问，不再需要 AutoDL 的 SSH 隧道。
跨网段/跨区部署时由网络组开通安全组或 ACL，机器本身只管 firewalld。

---

## 9. 接入本体平台（backend/.env）

三个服务都是 OpenAI 兼容接口，配置方法与 AutoDL 版 §8 **完全一致**，只改地址：
`OPENAI_BASE_URL=http://<服务器IP>:8000/v1`（必须以 `/v1` 结尾）。

两个必读坑原样复述：

1. **换嵌入模型 = 全库重建向量**：维度 512 → 4096，向量空间不兼容，旧 collection 必须删掉重灌；
2. **Reranker 两条路**：路 A 给平台加 http provider 接 `/v1/rerank`；路 B 零改动——平台
   `RERANK_PROVIDER=cross-encoder`，`RERANK_MODEL` 指向服务器上 Qwen3-Reranker 目录走
   sentence-transformers 本地加载（或检索对放本机跑 0.6B）。

---

## 10. 常见问题排查（麒麟特有优先）

| 现象 | 原因 | 解决 |
|---|---|---|
| 装驱动报 `ERROR: The Nouveau kernel driver is currently in use` | 没屏蔽 nouveau | 回 §2.1，conf + dracut + 重启 |
| 装驱动报找不到内核源码 / `Unable to find the kernel source` | kernel-devel 缺失或与 `uname -r` 不匹配 | §1.1 重装匹配版本；内核升过级就先重启进新内核再装 |
| 装驱动报编译错误 | gcc 版本与编译内核的编译器不一致 | `cat /proc/version` 看内核 gcc 版本，装对应 gcc（如 `gcc-toolset`） |
| 驱动装完 `nvidia-smi: command not found` 或 `No devices detected` | Secure Boot 拦了模块 / nouveau 未清干净 | BIOS 关 Secure Boot；`lsmod \| grep nouveau` 确认无输出 |
| `pip install vllm` 提示找不到满足的版本 | Python <3.9（麒麟自带 3.7） | 必须走 §3 的 Miniconda 3.12 |
| `pip` 报 `no such option: --torch-backend` | 该参数是 **uv** 的，pip 没有 | 改用 §3 的 `uv pip install`，或纯 `pip install -U vllm`（不带该参数） |
| torch 报 `CUDA driver version is insufficient` | 驱动 < 570.26 | 升级驱动到 570/580 Production Branch |
| 服务内网访问不通 | firewalld 没放行 | §8.2；`firewall-cmd --list-ports` 核对 |
| systemd 服务反复重启起不来 | 路径/参数错，或 JSON 参数被 systemd 分词打断 | `journalctl -u vllm-llm -n 100` 看真实报错；核对 vllm 绝对路径与模型目录 |
| OOM（`CUDA out of memory`） | 权重+KV 超显存 | 降 `--gpu-memory-utilization`、砍 `--max-model-len`、`--kv-cache-dtype fp8`、换 Int4 量化版（同 AutoDL 版 §9） |
| Reranker 分数全 0 | 缺 `--hf_overrides` 三字段 | 按 §7 原样补上 |
| 模型加载卡很久后失败 | 目录不完整/路径错 | 回 §4 验完整性；`vllm serve` 用绝对路径 |
| 重启机器后服务没了 | 没配 systemd 开机自启 | `systemctl is-enabled vllm-llm` 核对；缺了 `systemctl enable` |

---

## 11. 一页速记（从 AutoDL 迁移到麒麟裸机的差异）

1. 驱动自己装：nouveau 拉黑 → `dracut --force` → 重启 → `.run`（DKMS Yes）→ `nvidia-smi` ≥570；
2. CUDA Toolkit 不用装：torch wheel 自带，别被 nvidia-smi 的 "CUDA Version: 12.8" 骗去装 Toolkit；
3. Python 自己建：Miniconda `/opt/miniconda3` + env `vllm`(3.12)，系统 Python 别动；
4. 没有网络加速：pip 清华源 + ModelScope 直连 + `HF_HUB_DISABLE_XET=1` 兜底；
5. 目录约定：`/data/models`（数据盘 + fstab）；
6. `nohup` 换 systemd：`enable --now` 三服务、`journalctl -u` 看日志、崩溃自拉；
7. SSH 隧道换防火墙：`firewall-cmd --permanent --add-port=8000-8200/tcp && firewall-cmd --reload`。
