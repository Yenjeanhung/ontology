# KnowSource

基于知识库的轻量级 RAG（检索增强生成）问答系统。

## 技术栈

| 层 | 技术 |
|---|------|
| 前端 | Vue 3 + Vite |
| 后端 | FastAPI + SQLAlchemy |
| 智能体框架 | LangChain |
| 向量存储 | ChromaDB（可切换 Milvus） |
| 嵌入模型 | bge-small-zh-v1.5（可切换在线 API） |
| LLM | OpenAI 兼容接口（支持 Ollama） |

## 快速开始

### 1. 环境准备

```bash
# Python 3.10+、Node.js 18+
python --version
node --version
```

### 2. 启动后端

```bash
cd backend

# 创建虚拟环境
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 复制配置文件并按需修改（填入 API Key 等）
cp .env.example .env

# 启动服务
python server.py
```

后端运行在 `http://localhost:8000`

### 3. 启动前端

```bash
cd front

# 安装依赖
npm install

# 开发模式启动
npm run dev
```

前端运行在 `http://localhost:3000`，自动代理 API 请求到后端。

### 4. 访问应用

浏览器打开 `http://localhost:3000`

## 生产部署

```bash
# 构建前端
cd front
npm run build

# 启动后端（自动托管 front/dist/ 静态文件）
cd ../backend
python server.py
```

直接访问 `http://localhost:8000` 即可。

## 配置说明

后端配置通过 `backend/.env` 文件管理，主要配置项：

```env
# 嵌入模型：local（本地）或 openai（在线 API）
EMBEDDING_PROVIDER=local

# 向量存储：chroma 或 milvus
VECTOR_STORE_PROVIDER=chroma

# LLM 配置（支持 Ollama）
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
```

切换组件只需改配置，无需改代码。详见 `backend/ARCHITECTURE.md`。

## 项目结构

```
myRAG/
├── backend/               # 后端服务
│   ├── server.py          # 入口
│   ├── config.py          # 配置管理
│   ├── providers/         # 可插拔组件（LangChain）
│   ├── services/          # 业务逻辑
│   ├── routers/           # API 路由
│   ├── core/              # 文本分块等工具
│   └── ARCHITECTURE.md    # 技术架构文档
├── front/                 # 前端应用
│   ├── src/
│   │   ├── components/    # Vue 组件
│   │   └── api/           # API 请求层
│   └── package.json
└── README.md
```
