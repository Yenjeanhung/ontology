#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""环境自检：Docker、容器、端口、Python 依赖、.env 配置。

在切换到 Neo4j + Milvus 之前先跑这个脚本，能一次性定位卡点。

用法：
  python check_env.py
"""

from __future__ import annotations

import importlib.util
import shutil
import socket
import subprocess
import sys
from pathlib import Path

OK = "[OK]   "
WARN = "[WARN] "
FAIL = "[FAIL] "
INFO = "[INFO] "


def _print(status: str, msg: str):
    print(f"{status}{msg}")


def check_port(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def check_python_packages(packages: list[str]) -> list[str]:
    missing = []
    for pkg in packages:
        # 包名与导入名不一致时按常见映射处理
        import_name = {
            "pymilvus": "pymilvus",
            "neo4j": "neo4j",
            "langchain-milvus": "langchain_milvus",
            "langchain-chroma": "langchain_chroma",
        }.get(pkg, pkg.replace("-", "_"))
        if importlib.util.find_spec(import_name) is None:
            missing.append(pkg)
    return missing


def check_docker() -> tuple[bool, bool]:
    """返回 (docker 可用, compose 可用)。"""
    docker_ok = shutil.which("docker") is not None
    compose_ok = False
    if docker_ok:
        try:
            r = subprocess.run(
                ["docker", "compose", "version"],
                capture_output=True, text=True, timeout=20,
            )
            compose_ok = r.returncode == 0
        except Exception:  # noqa: BLE001
            compose_ok = False
    return docker_ok, compose_ok


def check_containers() -> dict[str, str]:
    """返回 {容器名: 状态}。"""
    if shutil.which("docker") is None:
        return {}
    try:
        r = subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=ontology-",
             "--format", "{{.Names}}\t{{.Status}}"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return {}
        result = {}
        for line in r.stdout.strip().splitlines():
            if "\t" in line:
                name, status = line.split("\t", 1)
                result[name] = status
        return result
    except Exception:  # noqa: BLE001
        return {}


def read_env() -> dict[str, str]:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        values[k.strip()] = v.strip()
    return values


def main() -> int:
    print("=" * 66)
    print("Neo4j + Milvus 环境自检")
    print("=" * 66)

    problems = 0

    # 1. Docker
    print("\n[1/4] Docker 环境")
    docker_ok, compose_ok = check_docker()
    if docker_ok:
        _print(OK, "docker 命令可用")
    else:
        _print(FAIL, "未检测到 docker")
        _print(INFO, "请先安装 Docker Desktop：https://www.docker.com/products/docker-desktop/")
        _print(INFO, "Windows 需开启 WSL2 后端（本机 wsl 命令可用，只需装 Docker Desktop）")
        problems += 1

    if compose_ok:
        _print(OK, "docker compose 可用")
    elif docker_ok:
        _print(WARN, "docker compose 不可用，请升级 Docker Desktop")
        problems += 1

    # 2. 容器
    print("\n[2/4] 容器状态")
    containers = check_containers()
    if not containers:
        _print(WARN, "尚未创建容器（或 docker 不可用）")
        _print(INFO, "启动命令： docker compose up -d")
    else:
        for name in ("ontology-neo4j", "ontology-milvus", "ontology-milvus-etcd",
                     "ontology-milvus-minio", "ontology-attu"):
            status = containers.get(name)
            if status is None:
                _print(WARN, f"{name} 不存在")
            elif status.lower().startswith("up"):
                _print(OK, f"{name} 运行中（{status}）")
            else:
                _print(FAIL, f"{name} 未运行（{status}）")
                problems += 1

    # 3. 端口
    print("\n[3/4] 端口连通性")
    neo4j_up = check_port("127.0.0.1", 7687)
    milvus_up = check_port("127.0.0.1", 19530)
    _print(OK if neo4j_up else FAIL, "Neo4j Bolt 7687 " + ("可连接" if neo4j_up else "不可连接"))
    _print(OK if milvus_up else FAIL, "Milvus  19530 " + ("可连接" if milvus_up else "不可连接"))
    if check_port("127.0.0.1", 7474):
        _print(OK, "Neo4j 浏览器 http://localhost:7474")
    if check_port("127.0.0.1", 8001):
        _print(OK, "Attu（Milvus 管理）http://localhost:8001")
    if check_port("127.0.0.1", 9001):
        _print(OK, "MinIO 控制台 http://localhost:9001")

    # 4. Python 依赖
    print("\n[4/4] Python 依赖与配置")
    needed = ["neo4j", "pymilvus", "langchain-milvus"]
    missing = check_python_packages(needed)
    for pkg in needed:
        _print(OK if pkg not in missing else WARN, pkg)
    if missing:
        _print(INFO, "安装命令： pip install " + " ".join(missing))
        problems += 1

    env = read_env()
    if not env:
        _print(WARN, "未找到 backend/.env")
    else:
        provider = env.get("VECTOR_STORE_PROVIDER", "")
        graph = env.get("GRAPH_STORE_PROVIDER", "")
        _print(OK if graph == "neo4j" else WARN, f"GRAPH_STORE_PROVIDER = {graph or '(未设置)'}（目标 neo4j）")
        _print(OK if provider == "milvus" else WARN, f"VECTOR_STORE_PROVIDER = {provider or '(未设置)'}（目标 milvus）")
        if graph != "neo4j" or provider != "milvus":
            _print(INFO, "修改 backend/.env 后重启后端服务生效")

    # 总结
    print("\n" + "=" * 66)
    if problems == 0:
        print("环境就绪。下一步：")
        print("  python scripts/load_graph_to_neo4j.py --reset")
    else:
        print(f"存在 {problems} 个待处理项，按上面的提示处理后重跑本脚本。")
        if not docker_ok:
            print("\n无 Docker 时的替代路径（不推荐，仅临时验证）：")
            print("  - 图库：Neo4j 可直接装 Windows 版，无需 Docker")
            print("  - 向量库：Milvus 只能跑在 Docker，临时可继续用 chroma")
            print("  - 数据不受影响：图谱数据已生成在 backend/data/graph_dataset")
    print("=" * 66)
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
