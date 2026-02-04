# Google Drive MCP Server 技术文档

> **版本**: 1.0.0
> **最后更新**: 2026-02-04
> **作者**: KnowledgeVault Team

---

## 目录

1. [项目概述](#1-项目概述)
2. [系统架构](#2-系统架构)
3. [核心功能详解](#3-核心功能详解)
4. [安装与部署](#4-安装与部署)
5. [配置说明](#5-配置说明)
6. [API 接口参考](#6-api-接口参考)
7. [调试问题记录](#7-调试问题记录)
8. [常见问题排查](#8-常见问题排查)
9. [扩展开发指南](#9-扩展开发指南)

---

## 1. 项目概述

### 1.1 项目背景

本项目是一个 **Python 实现的 Google Drive MCP (Model Context Protocol) Server**，基于 Anthropic 官方的 TypeScript 版本重写。MCP 是一种标准化协议，允许 AI 助手（如 Claude）安全地访问外部数据源和工具。

### 1.2 项目目标

- 让 Claude Desktop 或其他 MCP 客户端能够直接访问用户的 Google Drive 文件
- 支持本地运行（stdio 模式）和远程部署（HTTP 模式）
- 提供 Docker 容器化部署方案

### 1.3 技术栈

| 组件 | 技术选型 | 版本要求 |
|------|---------|---------|
| 编程语言 | Python | 3.12+ |
| MCP SDK | mcp | >= 1.0.0 |
| Google API | google-api-python-client | >= 2.100.0 |
| HTTP 框架 | Starlette + Uvicorn | >= 0.32.0 / 0.24.0 |
| SSE 支持 | sse-starlette | >= 1.6.0 |
| 容器化 | Docker + Docker Compose | 最新版 |

### 1.4 文件结构

```
KnowledgeVault/
└── MCP/
    ├── gdrive_mcp_server.py      # 主服务器代码 (703 行)
    ├── requirements.txt          # Python 依赖配置
    ├── Dockerfile               # Docker 镜像定义
    ├── docker-compose.yml       # Docker Compose 编排配置
    ├── README.md                # 快速开始文档
    ├── DOCUMENTATION_CN.md      # 本技术文档
    └── config/                  # 凭证目录 (git忽略)
        ├── gcp-oauth.keys.json  # Google OAuth 密钥
        └── credentials.json     # 认证后的用户凭证
```

---

## 2. 系统架构

### 2.1 整体架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                        MCP 客户端层                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │Claude Desktop│  │ Claude Code  │  │  其他 MCP 客户端      │  │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘  │
└─────────┼─────────────────┼─────────────────────┼───────────────┘
          │                 │                     │
          │ stdio           │ HTTP/SSE           │ HTTP/SSE
          │                 │                     │
┌─────────┼─────────────────┼─────────────────────┼───────────────┐
│         ▼                 ▼                     ▼               │
│  ┌────────────────────────────────────────────────────────┐    │
│  │            Google Drive MCP Server                      │    │
│  │  ┌─────────────────────────────────────────────────┐   │    │
│  │  │              传输层 (Transport Layer)            │   │    │
│  │  │  ┌─────────────┐      ┌─────────────────────┐   │   │    │
│  │  │  │ stdio 模式   │      │    HTTP/SSE 模式     │   │   │    │
│  │  │  │ (本地进程)   │      │ (Starlette+Uvicorn) │   │   │    │
│  │  │  └─────────────┘      └─────────────────────┘   │   │    │
│  │  └─────────────────────────────────────────────────┘   │    │
│  │  ┌─────────────────────────────────────────────────┐   │    │
│  │  │              MCP 协议层 (Protocol Layer)         │   │    │
│  │  │  • JSON-RPC 2.0 消息处理                         │   │    │
│  │  │  • Resources / Tools 注册与调用                  │   │    │
│  │  └─────────────────────────────────────────────────┘   │    │
│  │  ┌─────────────────────────────────────────────────┐   │    │
│  │  │          Google Drive Client (业务层)            │   │    │
│  │  │  • OAuth 认证管理                                │   │    │
│  │  │  • 文件列表/读取/搜索                            │   │    │
│  │  └─────────────────────────────────────────────────┘   │    │
│  └────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ Google Drive API (REST)
                              ▼
                    ┌──────────────────┐
                    │  Google Drive    │
                    │  Cloud Service   │
                    └──────────────────┘
```

### 2.2 核心模块说明

#### 2.2.1 GoogleDriveClient 类

位置: `gdrive_mcp_server.py:64-236`

负责与 Google Drive API 交互的封装类：

```python
class GoogleDriveClient:
    def authenticate() -> bool       # 加载/刷新 OAuth 凭证
    def run_auth_flow() -> bool      # 交互式 OAuth 认证
    def list_files() -> dict         # 列出文件
    def get_file_metadata() -> dict  # 获取文件元数据
    def read_file() -> tuple         # 读取文件内容
    def search_files() -> list       # 搜索文件
```

#### 2.2.2 MCP Server 创建

位置: `gdrive_mcp_server.py:243-392`

使用 `mcp.server.Server` 创建 MCP 服务器实例，注册 Resources 和 Tools。

#### 2.2.3 传输层实现

| 模式 | 函数 | 代码位置 | 说明 |
|------|------|---------|------|
| stdio | `run_server_stdio()` | 395-405 | 本地进程间通信 |
| HTTP | `run_server_http()` | 408-648 | 基于 Starlette 的 HTTP 服务 |

### 2.3 通信流程

#### stdio 模式

```
Claude Desktop
    │
    ├─ spawn process ──> python gdrive_mcp_server.py
    │
    ├─ stdin ──> JSON-RPC Request
    │
    └─ stdout <── JSON-RPC Response
```

#### HTTP 模式

```
MCP Client
    │
    ├─ POST /mcp ──> JSON-RPC Request
    │              (Content-Type: application/json)
    │
    └─ Response <── JSON-RPC Response

    ├─ GET /sse ──> Server-Sent Events (可选)
```

---

## 3. 核心功能详解

### 3.1 MCP Resources (资源)

Resources 提供对 Google Drive 文件的读取访问。

#### 3.1.1 list_resources

列出 Google Drive 中的文件。

**请求示例:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "resources/list",
  "params": {
    "cursor": null
  }
}
```

**响应示例:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "resources": [
      {
        "uri": "gdrive:///1ABC123xyz",
        "name": "项目文档.docx",
        "mimeType": "application/vnd.google-apps.document"
      }
    ],
    "nextCursor": "CAE="
  }
}
```

#### 3.1.2 read_resource

读取指定文件内容。

**URI 格式:** `gdrive:///{file_id}`

**请求示例:**
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "resources/read",
  "params": {
    "uri": "gdrive:///1ABC123xyz"
  }
}
```

### 3.2 MCP Tools (工具)

#### 3.2.1 search

全文搜索 Google Drive 文件。

**参数:**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| query | string | 是 | 搜索关键词 |

**请求示例:**
```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "search",
    "arguments": {
      "query": "项目计划"
    }
  }
}
```

#### 3.2.2 read_file

按文件 ID 读取文件内容。

**参数:**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| file_id | string | 是 | Google Drive 文件 ID |

### 3.3 文件类型处理

服务器对不同类型的文件采用不同的处理策略：

| 原始类型 | MIME Type | 导出/处理方式 |
|---------|-----------|--------------|
| Google Docs | application/vnd.google-apps.document | 导出为 Markdown (text/markdown) |
| Google Sheets | application/vnd.google-apps.spreadsheet | 导出为 CSV (text/csv) |
| Google Slides | application/vnd.google-apps.presentation | 导出为纯文本 (text/plain) |
| Google Drawing | application/vnd.google-apps.drawing | 导出为 PNG (image/png) |
| 文本文件 | text/* | 直接读取 UTF-8 内容 |
| JSON 文件 | application/json | 直接读取 |
| 二进制文件 | 其他 | Base64 编码后返回 |

**实现代码位置:** `gdrive_mcp_server.py:184-218`

---

## 4. 安装与部署

### 4.1 前提条件

#### 4.1.1 创建 Google Cloud 项目

1. 访问 [Google Cloud Console](https://console.cloud.google.com/)
2. 创建新项目或选择现有项目
3. 启用 **Google Drive API**:
   - 导航到 "APIs & Services" > "Library"
   - 搜索 "Google Drive API"
   - 点击 "Enable"

#### 4.1.2 创建 OAuth 2.0 凭证

1. 导航到 "APIs & Services" > "Credentials"
2. 点击 "Create Credentials" > "OAuth client ID"
3. 应用类型选择 **"Desktop app"**
4. 下载 JSON 文件
5. 重命名为 `gcp-oauth.keys.json`

### 4.2 本地部署 (stdio 模式)

```bash
# 1. 克隆代码
git clone <repository-url>
cd KnowledgeVault/MCP

# 2. 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/macOS
# 或: venv\Scripts\activate  # Windows

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置 OAuth 密钥
mkdir -p ~/.config/gdrive-mcp
cp /path/to/gcp-oauth.keys.json ~/.config/gdrive-mcp/

# 5. 运行认证
python gdrive_mcp_server.py auth
# 将在浏览器中完成 Google 账户授权

# 6. 启动服务器
python gdrive_mcp_server.py
```

### 4.3 Docker 部署 (HTTP 模式)

#### 4.3.1 准备凭证

```bash
# 在本地先完成认证
mkdir -p config
export GDRIVE_CREDS_DIR=./config
cp /path/to/gcp-oauth.keys.json ./config/
python gdrive_mcp_server.py auth
```

#### 4.3.2 使用 Docker Compose

```bash
# 构建并启动
docker-compose up -d

# 查看日志
docker-compose logs -f

# 验证服务
curl http://localhost:8010/health
```

#### 4.3.3 手动 Docker 命令

```bash
# 构建镜像
docker build -t gdrive-mcp-server .

# 运行容器
docker run -d \
  --name gdrive-mcp \
  -p 8010:8000 \
  -v $(pwd)/config:/config:ro \
  -e GDRIVE_CREDS_DIR=/config \
  gdrive-mcp-server
```

### 4.4 EC2 远程部署

详细步骤参见 `README.md` 中的 "部署到 AWS EC2" 章节。

---

## 5. 配置说明

### 5.1 环境变量

| 变量名 | 说明 | 默认值 | 适用场景 |
|-------|------|-------|---------|
| `GDRIVE_CREDS_DIR` | 凭证文件存储目录 | `~/.config/gdrive-mcp` | 所有模式 |
| `PORT` | HTTP 服务监听端口 | `8000` | Docker/HTTP 模式 |

### 5.2 命令行参数

```bash
python gdrive_mcp_server.py [command] [options]
```

**Commands:**

| 命令 | 说明 |
|------|------|
| `auth` | 运行 OAuth 认证流程 |
| `serve` (默认) | 启动 MCP 服务器 |

**Options:**

| 参数 | 说明 | 默认值 |
|------|------|-------|
| `--transport` | 传输模式: `stdio` 或 `http` | `stdio` |
| `--host` | HTTP 监听地址 | `0.0.0.0` |
| `--port` | HTTP 监听端口 | `8000` |

### 5.3 凭证文件

服务器需要两个凭证文件：

| 文件 | 说明 | 来源 |
|-----|------|-----|
| `gcp-oauth.keys.json` | Google OAuth 应用密钥 | 从 Google Cloud Console 下载 |
| `credentials.json` | 用户授权后的访问令牌 | 运行 `auth` 命令后自动生成 |

**文件位置:**
- 默认: `~/.config/gdrive-mcp/`
- 自定义: 通过 `GDRIVE_CREDS_DIR` 环境变量指定

### 5.4 Docker Compose 配置详解

```yaml
version: '3.8'

services:
  gdrive-mcp:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: gdrive-mcp-server
    restart: unless-stopped

    ports:
      - "8010:8000"       # 外部端口:内部端口

    volumes:
      - ./config:/config  # 凭证目录挂载

    environment:
      - GDRIVE_CREDS_DIR=/config
      - PORT=8000

    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s       # 检查间隔
      timeout: 10s        # 超时时间
      retries: 3          # 重试次数
      start_period: 10s   # 启动等待时间

    logging:
      driver: "json-file"
      options:
        max-size: "10m"   # 单个日志文件最大 10MB
        max-file: "3"     # 最多保留 3 个日志文件
```

### 5.5 MCP 客户端配置

#### Claude Desktop (stdio 模式)

文件位置: `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS)

```json
{
  "mcpServers": {
    "gdrive": {
      "command": "python",
      "args": ["/absolute/path/to/gdrive_mcp_server.py"],
      "env": {
        "GDRIVE_CREDS_DIR": "/absolute/path/to/.config/gdrive-mcp"
      }
    }
  }
}
```

#### Claude Desktop (HTTP 模式)

```json
{
  "mcpServers": {
    "gdrive": {
      "url": "http://localhost:8010/mcp"
    }
  }
}
```

---

## 6. API 接口参考

### 6.1 HTTP 端点列表

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/mcp` | POST | MCP JSON-RPC 主端点 |
| `/message` | POST | MCP 端点别名 (兼容性) |
| `/` | POST | 根路径 POST (兼容性) |
| `/` | GET | 根路径健康检查 |
| `/sse` | GET | Server-Sent Events 流 |

### 6.2 健康检查接口

**请求:**
```http
GET /health HTTP/1.1
Host: localhost:8010
```

**响应:**
```json
{
  "status": "healthy",
  "server": "gdrive-mcp-server"
}
```

### 6.3 MCP 方法列表

| 方法 | 说明 |
|------|------|
| `initialize` | 初始化握手 |
| `resources/list` | 列出可用资源 |
| `resources/read` | 读取资源内容 |
| `tools/list` | 列出可用工具 |
| `tools/call` | 调用工具 |
| `notifications/initialized` | 客户端初始化完成通知 |

### 6.4 测试命令

```bash
# 健康检查
curl http://localhost:8010/health

# 列出工具
curl -X POST http://localhost:8010/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

# 初始化
curl -X POST http://localhost:8010/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'

# 列出资源
curl -X POST http://localhost:8010/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"resources/list"}'

# 搜索文件
curl -X POST http://localhost:8010/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"search","arguments":{"query":"test"}}}'
```

---

## 7. 调试问题记录

本节记录了开发和调试过程中遇到的配置问题及解决方案。

### 7.1 端口映射修改 (8000 -> 8010)

**问题描述:**

最初 docker-compose.yml 配置的端口映射为 `8000:8000`，但在实际部署中发现端口冲突。

**原始配置:**
```yaml
ports:
  - "8000:8000"
```

**修改后:**
```yaml
ports:
  - "8010:8000"
```

**原因分析:**

- 本地可能有其他服务占用 8000 端口（如其他 MCP 服务器、开发服务器等）
- 容器内部仍使用 8000 端口，仅外部映射改为 8010

**注意事项:**

- 修改端口后，客户端配置也需要相应调整
- 健康检查命令变为: `curl http://localhost:8010/health`

---

### 7.2 Volume 挂载选项调整

**问题描述:**

初始版本的 volume 挂载使用了 `:ro` (只读) 选项，但这可能导致某些场景下的问题。

**原始配置:**
```yaml
volumes:
  - ./config:/config:ro
```

**修改后:**
```yaml
volumes:
  - ./config:/config
```

**原因分析:**

- 如果服务器需要刷新 OAuth token 并保存到 credentials.json，只读挂载会导致写入失败
- 移除 `:ro` 选项允许服务器更新凭证文件

**建议:**

- 生产环境如果不需要自动刷新凭证，可以保持 `:ro` 增强安全性
- 开发环境建议移除 `:ro`，方便凭证自动刷新

---

### 7.3 未使用的 BlobContent 导入

**问题描述:**

代码中最初导入了 `BlobContent` 类，但实际并未使用。

**原始代码:**
```python
from mcp.types import (
    Resource,
    Tool,
    TextContent,
    BlobContent,  # 未使用
    ...
)
```

**修改后:**
```python
from mcp.types import (
    Resource,
    Tool,
    TextContent,
    # BlobContent 已移除
    ...
)
```

**原因分析:**

- 最初计划使用 `BlobContent` 返回二进制内容
- 实际实现中选择将二进制内容 Base64 编码后以 `TextContent` 返回
- 这种方式兼容性更好，避免了某些 MCP 客户端对 BlobContent 支持不完善的问题

---

### 7.4 服务器路由配置优化

**问题描述:**

初始版本的 HTTP 端点配置不够完善，某些客户端无法正常连接。

**修改内容:**

1. 添加了多个兼容性路由:
   ```python
   Route("/mcp", handle_message, methods=["POST"]),     # 主端点
   Route("/message", handle_message, methods=["POST"]), # 兼容别名
   Route("/", handle_message, methods=["POST"]),        # 根路径 POST
   Route("/", health_check, methods=["GET"]),           # 根路径 GET
   ```

2. 添加了 `/message` 作为 `/mcp` 的别名，兼容不同的 MCP 客户端实现

**原因分析:**

- 不同的 MCP 客户端可能使用不同的端点路径
- 提供多个路由入口增强兼容性

---

### 7.5 日志输出到 stderr 的重要性

**问题描述:**

stdio 模式下，如果日志输出到 stdout，会干扰 MCP 协议通信。

**正确配置:**
```python
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,  # 必须输出到 stderr
)
```

**原因分析:**

- MCP stdio 模式使用 stdin/stdout 进行 JSON-RPC 通信
- 日志如果输出到 stdout，会被 MCP 客户端解析为协议消息，导致解析错误
- 所有日志必须输出到 stderr，保持 stdout 干净

**调试技巧:**

如果 stdio 模式连接失败，检查是否有任何 `print()` 语句或日志输出到 stdout。

---

### 7.6 Google OAuth 认证常见问题

#### 7.6.1 redirect_uri_mismatch 错误

**问题描述:**

运行 `python gdrive_mcp_server.py auth` 时，浏览器显示 "redirect_uri_mismatch" 错误。

**解决方案:**

1. 进入 Google Cloud Console > APIs & Services > Credentials
2. 编辑 OAuth 2.0 Client ID
3. 在 "Authorized redirect URIs" 中添加:
   - `http://localhost:8080/`
   - `http://localhost/`
4. 保存更改，等待几分钟生效

#### 7.6.2 access_denied 错误

**问题描述:**

授权时显示 "Access denied" 或 "App not verified"。

**解决方案:**

1. 如果是个人/测试使用:
   - 在 OAuth 同意屏幕添加测试用户（你的 Google 账户邮箱）

2. 如果需要公开发布:
   - 提交应用进行 Google 审核验证

#### 7.6.3 凭证过期

**问题描述:**

服务器启动时报错 "凭证无效，请先运行 auth 进行认证"。

**原因分析:**

- OAuth refresh token 过期（通常 7 天未使用会过期）
- credentials.json 文件损坏

**解决方案:**

```bash
# 删除旧凭证，重新认证
rm ~/.config/gdrive-mcp/credentials.json
python gdrive_mcp_server.py auth
```

---

### 7.7 Docker 容器问题排查

#### 7.7.1 容器启动后立即退出

**排查步骤:**

```bash
# 查看容器状态
docker ps -a

# 查看退出日志
docker logs gdrive-mcp-server

# 常见原因
# 1. 凭证文件缺失
docker exec gdrive-mcp-server ls -la /config

# 2. 凭证文件无效
docker exec gdrive-mcp-server cat /config/credentials.json
```

#### 7.7.2 健康检查失败

**排查步骤:**

```bash
# 进入容器内部测试
docker exec -it gdrive-mcp-server /bin/bash
curl http://localhost:8000/health

# 检查进程是否在运行
ps aux | grep python
```

#### 7.7.3 无法连接到容器服务

**排查清单:**

1. 确认端口映射正确: `docker port gdrive-mcp-server`
2. 确认没有防火墙阻止: `sudo ufw status`
3. 确认服务在监听: `docker exec gdrive-mcp-server netstat -tlnp`

---

### 7.8 MCP 协议版本兼容性

**问题描述:**

某些旧版 MCP 客户端可能不支持最新的协议版本。

**当前配置:**
```python
result = {
    "protocolVersion": "2024-11-05",  # MCP 协议版本
    ...
}
```

**兼容性说明:**

- 本服务器实现的是 `2024-11-05` 版本的 MCP 协议
- 如果客户端报协议版本不匹配，需要检查客户端版本

---

## 8. 常见问题排查

### 8.1 问题排查流程图

```
服务器无法启动
    │
    ├─ 检查 Python 版本 >= 3.12?
    │   └─ 否 → 升级 Python
    │
    ├─ 依赖安装完整?
    │   └─ 否 → pip install -r requirements.txt
    │
    ├─ OAuth 密钥文件存在?
    │   └─ 否 → 从 Google Cloud Console 下载
    │
    └─ credentials.json 存在?
        └─ 否 → 运行 python gdrive_mcp_server.py auth

MCP 客户端连接失败
    │
    ├─ stdio 模式
    │   ├─ 检查命令路径是否正确
    │   ├─ 检查 Python 虚拟环境是否激活
    │   └─ 检查日志输出（应在 stderr）
    │
    └─ HTTP 模式
        ├─ 健康检查通过? curl /health
        ├─ 端口可访问? telnet localhost 8010
        └─ 防火墙/安全组配置?
```

### 8.2 错误代码参考

| 错误码 | 含义 | 解决方案 |
|-------|------|---------|
| -32700 | Parse error | 检查请求 JSON 格式 |
| -32601 | Method not found | 检查方法名是否正确 |
| -32603 | Internal error | 查看服务器日志获取详情 |

### 8.3 日志级别调整

如需更详细的调试信息:

```python
# 在 gdrive_mcp_server.py 中修改
logging.basicConfig(
    level=logging.DEBUG,  # 改为 DEBUG
    ...
)
```

---

## 9. 扩展开发指南

### 9.1 添加新的 Tool

```python
# 1. 在 list_tools() 中注册
Tool(
    name="create_file",
    description="Create a new file in Google Drive",
    inputSchema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "File name"},
            "content": {"type": "string", "description": "File content"},
        },
        "required": ["name", "content"],
    },
),

# 2. 在 call_tool() 中实现
elif name == "create_file":
    file_name = arguments.get("name")
    content = arguments.get("content")
    # 实现创建文件逻辑
    result = await drive_client.create_file(file_name, content)
    return CallToolResult(
        content=[TextContent(type="text", text=f"Created file: {result}")]
    )
```

### 9.2 添加向量数据库支持

可以集成 ChromaDB 实现语义搜索:

```yaml
# docker-compose.yml 添加
services:
  chroma:
    image: chromadb/chroma:latest
    ports:
      - "8001:8000"
    volumes:
      - chroma_data:/chroma/chroma

volumes:
  chroma_data:
```

### 9.3 添加新的传输模式

如需支持 WebSocket:

```python
async def run_server_websocket(host: str, port: int):
    # 使用 websockets 库实现
    pass
```

---

## 附录

### A. 依赖包版本说明

| 包名 | 用途 | 必需 |
|-----|------|------|
| mcp >= 1.0.0 | MCP 协议 SDK | 是 |
| google-api-python-client >= 2.100.0 | Google API 客户端 | 是 |
| google-auth-httplib2 >= 0.1.0 | Google 认证 HTTP 支持 | 是 |
| google-auth-oauthlib >= 1.0.0 | OAuth 2.0 流程支持 | 是 |
| aiohttp >= 3.9.0 | 异步 HTTP | 是 |
| uvicorn >= 0.24.0 | ASGI 服务器 | HTTP 模式 |
| starlette >= 0.32.0 | Web 框架 | HTTP 模式 |
| sse-starlette >= 1.6.0 | SSE 支持 | HTTP 模式 |

### B. 版本更新日志

| 日期 | 版本 | 变更内容 |
|-----|------|---------|
| 2026-02-04 | 1.0.0 | 初始版本发布 |
| - | - | - 端口映射从 8000 改为 8010 |
| - | - | - 移除未使用的 BlobContent 导入 |
| - | - | - 优化服务器路由配置 |
| - | - | - 调整 Volume 挂载选项 |

### C. 参考资料

- [MCP 协议规范](https://spec.modelcontextprotocol.io/)
- [Google Drive API 文档](https://developers.google.com/drive/api/v3/reference)
- [Anthropic MCP SDK](https://github.com/anthropics/mcp)

---

**文档结束**
