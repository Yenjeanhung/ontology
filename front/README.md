# KnowSource 前端框架说明

## 技术栈

| 技术 | 版本 | 用途 |
|------|------|------|
| Vue 3 | ^3.5 | UI 框架 (Composition API + `<script setup>`) |
| Vite | ^6.3 | 构建工具 (开发服务器 + 生产打包) |
| @vitejs/plugin-vue | ^5.2 | Vite 的 Vue SFC 编译支持 |

无其他第三方 UI 库，样式手写纯 CSS（Scoped CSS）。

## 项目结构

```
front/
├── index.html              # Vite 入口 HTML
├── package.json            # 依赖和脚本
├── vite.config.js          # Vite 配置 (含 API 代理)
├── dist/                   # 生产构建输出 (gitignored)
├── src/
│   ├── main.js             # 应用入口，挂载 Vue
│   ├── App.vue             # 根组件 (布局 + Tab 切换)
│   ├── style.css           # 全局基础样式 (CSS 变量、通用类)
│   ├── api/
│   │   └── index.js        # API 请求层 (所有后端接口封装)
│   └── components/
│       ├── KbList.vue       # 知识库列表 (搜索 + 列表 + 新建入口)
│       ├── KbDetail.vue     # 知识库详情 (文件上传 + 文件列表)
│       ├── QueryView.vue    # RAG 查询界面 (选择 KB + 输入 + 结果)
│       └── CreateKbModal.vue # 新建知识库弹窗
└── index_old.html          # 旧版 CDN 单文件备份
```

## 开发命令

```bash
cd front

# 安装依赖
npm install

# 开发模式 (热更新, 端口 3000, 自动代理 API 到 8000)
npm run dev

# 生产构建 (输出到 dist/)
npm run build

# 预览构建结果
npm run preview
```

## 开发 vs 生产

| 场景 | 前端 | 后端 |
|------|------|------|
| 开发 | `npm run dev` → localhost:3000 (Vite dev server) | `python server.py` → localhost:8000 |
| 生产 | `npm run build` → `front/dist/` | `python server.py` 直接服务 dist/ |

开发时 Vite 自动将 `/api/*` 请求代理到 `localhost:8000`，无需跨域配置。
生产时后端直接托管 `front/dist/` 静态文件。

## API 接口

前端通过 `src/api/index.js` 统一调用后端：

| 函数 | 方法 | 路径 | 说明 |
|------|------|------|------|
| `fetchKbs()` | GET | `/api/kb` | 获取知识库列表 |
| `createKb(name)` | POST | `/api/kb` | 创建知识库 |
| `getKb(kbId)` | GET | `/api/kb/{id}` | 获取知识库详情含文件 |
| `deleteKb(kbId)` | DELETE | `/api/kb/{id}` | 删除知识库及所有文件 |
| `uploadChunk(...)` | POST | `/api/upload/chunk` | 上传文件分片 |
| `deleteFile(fileId)` | DELETE | `/api/files/{id}` | 删除单个文件 |
| `queryRag(kbId, query)` | POST | `/api/query` | RAG 检索查询 |

## 设计规范

- 颜色系统通过 CSS 变量定义在 `src/style.css` 的 `:root`
- 各组件使用 `<style scoped>` 避免样式污染
- SVG 图标内联在模板中（无第三方图标库依赖）
- 扁平极简风格，黑白 + 金色 (#A16207) 点缀
