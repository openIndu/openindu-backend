# openIndu Backend

> **语言:** [English](README.md) | 中文

## 项目简介

openIndu Backend 是 [openIndu](https://openindu.com) 开源工业自动化生态平台的服务端平台，提供 REST API（供 Portal 和 Admin 前端应用调用）和 MCP Server（供 Claude Code AI Agent 知识检索）。

## 技术栈

| 层级            | 技术                            |
| --------------- | ------------------------------- |
| Web 框架        | FastAPI 0.115                   |
| ASGI 服务器     | Uvicorn                         |
| ORM             | SQLAlchemy 2.x + Alembic        |
| 数据库          | PostgreSQL 15                   |
| 向量数据库      | Milvus 2.4                      |
| 对象存储        | MinIO（开发）/ 阿里云 OSS（生产） |
| MCP SDK         | mcp 1.9                         |
| 定时任务        | APScheduler                     |
| 限流            | slowapi                         |
| 短信服务        | 阿里云短信                      |
| LLM 集成        | OpenAI SDK                      |

## 快速开始

### 前置条件

- Docker & Docker Compose
- Python 3.11+（用于容器外本地开发）

### Docker Compose 启动

```bash
# 1. 克隆仓库
git clone https://github.com/openIndu/openindu-backend.git
cd openindu-backend

# 2. 从模板创建 .env
cp .env.example .env

# 3. 启动所有服务
docker compose up -d --build
```

启动后包含以下服务：

| 服务       | 端口   | 说明                         |
| ---------- | ------ | ---------------------------- |
| Web API    | `8004` | REST API（Portal + Admin）   |
| MCP API    | `8005` | MCP Server（Claude Code 知识检索） |
| PostgreSQL | `5432` | 业务数据库                   |
| Milvus     | `19530` | 向量数据库                   |
| MinIO      | `9000` | S3 兼容对象存储              |

### 本地开发

```bash
# 安装依赖
pip install -r requirements.txt

# 运行数据库迁移
alembic upgrade head

# 启动 Web API
uvicorn app.web_app:app --reload --port 8004

# 启动 MCP Server（新终端）
uvicorn app.mcp_app:app --reload --port 8005
```

## 项目结构

```
openindu-backend/
├── app/
│   ├── api/              # REST API 路由处理
│   │   ├── auth.py       #   认证（短信登录）
│   │   ├── users.py      #   用户管理
│   │   ├── documents.py  #   知识库文档
│   │   ├── files.py      #   文件上传/下载
│   │   ├── chat.py       #   RAG 对话
│   │   ├── software.py   #   软件目录
│   │   ├── admin.py      #   管理后台接口
│   │   ├── portal.py     #   官网 CMS 接口
│   │   ├── stats.py      #   统计（PV/UV）
│   │   ├── tags.py       #   标签管理
│   │   ├── sync.py       #   数据同步
│   │   ├── brand_mapping.py # 品牌名称映射
│   │   ├── visits.py     #   访问追踪
│   │   └── ...
│   ├── models/           # SQLAlchemy ORM 模型
│   ├── core/             # 配置、数据库会话、工具函数
│   ├── services/         # 业务逻辑层
│   │   ├── auth_service.py
│   │   ├── chat_service.py
│   │   ├── file_storage.py
│   │   ├── milvus_service.py
│   │   ├── rag_sync_service.py
│   │   └── ...
│   ├── mcp/              # MCP Server（模型上下文协议）
│   │   ├── server.py
│   │   └── tools.py
│   ├── middleware/        # 自定义中间件
│   ├── tasks/             # 定时后台任务
│   ├── web_app.py         # Web API 入口（端口 8004）
│   └── mcp_app.py         # MCP Server 入口（端口 8005）
├── alembic/              # 数据库迁移脚本
├── tests/                # 测试套件（pytest）
├── scripts/              # 工具脚本
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 许可证

[Apache-2.0](LICENSE)
