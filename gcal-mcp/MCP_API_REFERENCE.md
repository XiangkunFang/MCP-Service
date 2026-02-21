# Google Calendar MCP Server — 标准接口文档

> **服务名称**: `gcal-mcp-server`  
> **协议版本**: MCP `2024-11-05`  
> **服务版本**: `1.0.0`  
> **最后更新**: 2026-02-20  
> **语言/运行时**: Python 3.12+

---

## 目录

1. [服务概述](#1-服务概述)
2. [连接方式](#2-连接方式)
3. [认证与初始化](#3-认证与初始化)
4. [能力声明 (Capabilities)](#4-能力声明-capabilities)
5. [Resources 接口](#5-resources-接口)
6. [Tools 接口](#6-tools-接口)
7. [时间与日历约定](#7-时间与日历约定)
8. [错误处理](#8-错误处理)
9. [集成示例](#9-集成示例)
10. [附录](#附录)

---

## 1. 服务概述

Google Calendar MCP Server 是一个实现了 [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) 的服务，允许 MCP 客户端通过标准化的 JSON-RPC 2.0 协议访问和操作用户的 Google Calendar。

### 1.1 功能摘要

| 功能类别 | 能力 |
|----------|------|
| **读取** | 列出日历、按时间/关键词列出事件、获取单个事件详情 |
| **写入** | 创建事件、更新事件、删除事件 |

### 1.2 支持的 MCP 特性

| MCP 特性 | 支持状态 |
|----------|----------|
| Resources | ❌ 未实现 |
| Tools | ✅ 支持（6 个工具） |
| Prompts | ❌ 未实现 |
| Sampling | ❌ 未实现 |
| Notifications | ✅ 支持（`notifications/initialized`） |

### 1.3 依赖的 Google API

- **Google Calendar API v3**
- **OAuth 2.0 Scope**: `https://www.googleapis.com/auth/calendar`（完整日历读写）

---

## 2. 连接方式

### 2.1 stdio 模式（本地进程）

适用于 MCP 客户端以子进程方式启动服务器的场景。

**启动命令:**
```bash
python gcal_mcp_server.py
# 或
python gcal_mcp_server.py --transport stdio
```

**客户端配置示例 (Claude Desktop / Cursor):**
```json
{
  "mcpServers": {
    "gcal": {
      "command": "python",
      "args": ["/absolute/path/to/gcal_mcp_server.py"],
      "env": {
        "GCAL_CREDS_DIR": "/absolute/path/to/config/gcal-mcp"
      }
    }
  }
}
```

**通信方式:** 通过 `stdin` 发送 JSON-RPC 请求，通过 `stdout` 接收 JSON-RPC 响应。

### 2.2 HTTP 模式（远程 / Docker）

适用于远程部署、Docker 容器化运行，支持多客户端并发访问。

**启动命令:**
```bash
python gcal_mcp_server.py --transport http --host 0.0.0.0 --port 8000
```

**HTTP 端点列表:**

| 端点 | 方法 | 说明 |
|------|------|------|
| `/mcp` | `POST` | **主 MCP JSON-RPC 端点** |
| `/message` | `POST` | MCP 端点别名（兼容性） |
| `/` | `POST` | 根路径 MCP 端点（兼容性） |
| `/health` | `GET` | 健康检查 |
| `/` | `GET` | 根路径健康检查 |
| `/sse` | `GET` | Server-Sent Events 流 |

**客户端配置示例:**
```json
{
  "mcpServers": {
    "gcal": {
      "url": "http://localhost:8020/mcp"
    }
  }
}
```

**请求格式:**
```http
POST /mcp HTTP/1.1
Host: localhost:8020
Content-Type: application/json

{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/list",
  "params": {}
}
```

### 2.3 健康检查

```bash
curl http://localhost:8020/health
```

**响应:**
```json
{
  "status": "healthy",
  "server": "gcal-mcp-server"
}
```

---

## 3. 认证与初始化

### 3.1 前置条件

调用方不需要处理 Google OAuth 认证——认证在服务端完成。但服务端需要提前配置好以下凭证：

| 文件 | 说明 | 获取方式 |
|------|------|----------|
| `gcp-oauth.keys.json` | Google OAuth 应用密钥 | 从 [Google Cloud Console](https://console.cloud.google.com/) 下载 |
| `credentials.json` | 用户授权后的访问令牌 | 运行 `python gcal_mcp_server.py auth` 生成 |

凭证默认存放在 `~/.config/gcal-mcp/`，可通过环境变量 `GCAL_CREDS_DIR` 自定义。

### 3.2 MCP 初始化握手

客户端连接后，必须先发送 `initialize` 请求进行握手：

**请求:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "clientInfo": {
      "name": "your-client-name",
      "version": "1.0.0"
    }
  }
}
```

**响应:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2024-11-05",
    "capabilities": {
      "resources": { "listChanged": false },
      "tools": { "listChanged": false }
    },
    "serverInfo": {
      "name": "gcal-mcp-server",
      "version": "1.0.0"
    }
  }
}
```

初始化完成后，客户端应发送 `notifications/initialized` 通知：

```json
{
  "jsonrpc": "2.0",
  "method": "notifications/initialized"
}
```

---

## 4. 能力声明 (Capabilities)

服务器在初始化响应中声明的能力：

```json
{
  "capabilities": {
    "resources": { "listChanged": false },
    "tools": { "listChanged": false }
  }
}
```

| 能力 | 说明 |
|------|------|
| `resources` | 本服务未实现 Resources，仅声明兼容字段 |
| `tools` | 支持 `tools/list` 和 `tools/call`，不支持工具变更通知 |

---

## 5. Resources 接口

本服务**未实现** MCP Resources。日历与事件通过 **Tools** 接口进行读写。若客户端调用 `resources/list` 或 `resources/read`，将按协议返回方法不存在或空结果（视服务实现而定）。

---

## 6. Tools 接口

### 6.0 列出工具 — `tools/list`

**请求:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/list"
}
```

返回所有可用工具的列表及其 `inputSchema`。

---

### 6.1 list_calendars — 列出日历

列出当前用户可访问的**所有日历**（含主日历与**共享日历**，如学校/公司共享的 NYU、企业日历等）。返回每个日历的 `id` 和 `summary`；查某日历的事件时，在 `list_events` 中传入对应 `calendar_id` 即可。

**请求:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "list_calendars",
    "arguments": {}
  }
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| （无） | — | — | — |

**响应:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "日历列表:\n\n- 主日历 (id: primary)\n- 工作 (id: xxx@group.calendar.google.com)"
      }
    ],
    "isError": false
  }
}
```

---

### 6.2 list_events — 列出事件

在指定日历中按时间范围或关键词列出事件。

**请求:**
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "list_events",
    "arguments": {
      "calendar_id": "primary",
      "time_min": "2025-02-21T00:00:00Z",
      "time_max": "2025-02-28T23:59:59Z",
      "max_results": 50,
      "q": "会议"
    }
  }
}
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `calendar_id` | string | 否 | `primary` | 日历 ID；传 **`all`** 可汇总**所有日历**（主日历 + 共享如 NYU、节假日）的事件；或指定单个 id（含 [公开节假日 ID](#f-公开日历与节假日美国等)） |
| `time_min` | string | 否 | — | 开始时间，ISO 8601 |
| `time_max` | string | 否 | — | 结束时间，ISO 8601 |
| `max_results` | integer | 否 | 50 | 最大条数，上限 250 |
| `q` | string | 否 | — | 按摘要关键词过滤 |

**响应:**
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "日历 primary 中的事件 (共 3 条):\n\n- 周会 | 开始: 2025-02-21T10:00:00+08:00 [event_id: abc123]\n- 评审 | 开始: 2025-02-22T14:00:00Z [event_id: def456]"
      }
    ],
    "isError": false
  }
}
```

---

### 6.3 get_event — 获取事件详情

获取单个事件的完整信息。

**请求:**
```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "get_event",
    "arguments": {
      "calendar_id": "primary",
      "event_id": "abc123xyz"
    }
  }
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `event_id` | string | **是** | 事件 ID（从 list_events 等获取） |
| `calendar_id` | string | 否 | 日历 ID，默认 `primary` |

**响应:**
```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "摘要: 周会\n开始: 2025-02-21T10:00:00+08:00\n结束: 2025-02-21T11:00:00+08:00\n描述: N/A\n地点: 会议室A\nID: abc123xyz"
      }
    ],
    "isError": false
  }
}
```

---

### 6.4 create_event — 创建事件

在指定日历中创建新事件。

**请求:**
```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "method": "tools/call",
  "params": {
    "name": "create_event",
    "arguments": {
      "calendar_id": "primary",
      "summary": "项目评审",
      "start": "2025-02-21T14:00:00+08:00",
      "end": "2025-02-21T15:00:00+08:00",
      "description": "季度评审会",
      "location": "大会议室"
    }
  }
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `summary` | string | **是** | 事件标题 |
| `start` | string | **是** | 开始日期/时间，ISO 8601 |
| `end` | string | **是** | 结束日期/时间，ISO 8601 |
| `calendar_id` | string | 否 | 日历 ID，默认 `primary` |
| `description` | string | 否 | 事件描述 |
| `location` | string | 否 | 地点 |

**响应:**
```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "✅ 事件已创建\n   ID: xyz789\n   链接: https://www.google.com/calendar/event?eid=..."
      }
    ],
    "isError": false
  }
}
```

---

### 6.5 update_event — 更新事件

更新已有事件（仅传需要修改的字段）。

**请求:**
```json
{
  "jsonrpc": "2.0",
  "id": 5,
  "method": "tools/call",
  "params": {
    "name": "update_event",
    "arguments": {
      "calendar_id": "primary",
      "event_id": "xyz789",
      "summary": "项目评审（改期）",
      "start": "2025-02-22T14:00:00+08:00",
      "end": "2025-02-22T15:00:00+08:00"
    }
  }
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `event_id` | string | **是** | 要更新的事件 ID |
| `calendar_id` | string | 否 | 日历 ID，默认 `primary` |
| `summary` | string | 否 | 新标题 |
| `start` | string | 否 | 新开始时间 |
| `end` | string | 否 | 新结束时间 |
| `description` | string | 否 | 新描述 |
| `location` | string | 否 | 新地点 |

**响应:**
```json
{
  "jsonrpc": "2.0",
  "id": 5,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "✅ 事件已更新\n   ID: xyz789\n   链接: https://www.google.com/calendar/event?eid=..."
      }
    ],
    "isError": false
  }
}
```

---

### 6.6 delete_event — 删除事件

删除指定日历中的事件。

**请求:**
```json
{
  "jsonrpc": "2.0",
  "id": 6,
  "method": "tools/call",
  "params": {
    "name": "delete_event",
    "arguments": {
      "calendar_id": "primary",
      "event_id": "xyz789"
    }
  }
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `event_id` | string | **是** | 要删除的事件 ID |
| `calendar_id` | string | 否 | 日历 ID，默认 `primary` |

**响应:**
```json
{
  "jsonrpc": "2.0",
  "id": 6,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "✅ 事件已删除 (event_id: xyz789)"
      }
    ],
    "isError": false
  }
}
```

---

### 6.7 Tools 速查表

| 工具名 | 说明 | 必填参数 | 可选参数 |
|--------|------|----------|----------|
| `list_calendars` | 列出可访问日历 | — | — |
| `list_events` | 按时间/关键词列事件 | — | `calendar_id`, `time_min`, `time_max`, `max_results`, `q` |
| `get_event` | 获取单个事件详情 | `event_id` | `calendar_id` |
| `create_event` | 创建事件 | `summary`, `start`, `end` | `calendar_id`, `description`, `location` |
| `update_event` | 更新事件 | `event_id` | `calendar_id`, `summary`, `start`, `end`, `description`, `location` |
| `delete_event` | 删除事件 | `event_id` | `calendar_id` |

---

## 7. 时间与日历约定

### 7.1 日历 ID

| 取值 | 说明 |
|------|------|
| `primary` | 用户的主日历（推荐默认） |
| 其他 | 通过 `list_calendars` 返回的 `(id: xxx)` 获取 |

### 7.2 时间格式（start / end）

| 类型 | 格式示例 | API 行为 |
|------|----------|----------|
| 全天事件 | `2025-02-21` | 使用 `date` |
| 带时间事件 | `2025-02-21T14:00:00+08:00` | 使用 `dateTime` + 时区 |
| UTC 后缀 | `2025-02-21T06:00:00Z` | 转为 UTC 的 `dateTime` |

服务端将上述字符串转换为 Google Calendar API 所需的 `start`/`end` 结构（`date` 或 `dateTime` + `timeZone`）。

---

## 8. 错误处理

### 8.1 JSON-RPC 标准错误

所有错误响应遵循 JSON-RPC 2.0 标准格式：

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -32601,
    "message": "Method not found: unknown_method"
  }
}
```

| 错误码 | 含义 | 常见原因 |
|--------|------|----------|
| `-32700` | Parse error | 请求 JSON 格式错误 |
| `-32601` | Method not found | 方法名不存在 |
| `-32603` | Internal error | 服务端内部错误（如 Google API 调用失败） |

### 8.2 工具调用错误

工具调用失败时，响应中 `isError` 为 `true`：

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "Error: event_id is required"
      }
    ],
    "isError": true
  }
}
```

### 8.3 常见错误场景

| 错误信息 | 原因 | 解决方案 |
|----------|------|----------|
| `Error: event_id is required` | 缺少必填参数 | 提供 `event_id` 参数 |
| `Error: summary is required` | 创建事件缺少标题 | 提供 `summary` 参数 |
| `Error: start and end are required` | 创建事件缺少时间 | 提供 `start`、`end` 参数 |
| `Unknown tool: xxx` | 调用了不存在的工具 | 先调用 `tools/list` 获取可用工具列表 |
| `获取事件失败: 404 Not Found` | 事件或日历不存在 | 检查 `event_id`、`calendar_id` 是否正确 |
| `凭证无效` | OAuth 令牌过期 | 重新运行 `python gcal_mcp_server.py auth` |

---

## 9. 集成示例

### 9.1 完整调用流程 (HTTP 模式)

```bash
# 步骤 1: 健康检查
curl http://localhost:8020/health

# 步骤 2: 初始化握手
curl -X POST http://localhost:8020/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
      "protocolVersion": "2024-11-05",
      "capabilities": {},
      "clientInfo": {"name": "my-client", "version": "1.0"}
    }
  }'

# 步骤 3: 发送初始化完成通知
curl -X POST http://localhost:8020/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "method": "notifications/initialized"}'

# 步骤 4: 列出可用工具
curl -X POST http://localhost:8020/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}'

# 步骤 5: 列出日历
curl -X POST http://localhost:8020/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 3,
    "method": "tools/call",
    "params": {
      "name": "list_calendars",
      "arguments": {}
    }
  }'

# 步骤 6: 列出本周事件
curl -X POST http://localhost:8020/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 4,
    "method": "tools/call",
    "params": {
      "name": "list_events",
      "arguments": {
        "calendar_id": "primary",
        "time_min": "2025-02-21T00:00:00Z",
        "time_max": "2025-02-28T23:59:59Z",
        "max_results": 50
      }
    }
  }'

# 步骤 7: 创建事件
curl -X POST http://localhost:8020/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 5,
    "method": "tools/call",
    "params": {
      "name": "create_event",
      "arguments": {
        "summary": "MCP 测试会议",
        "start": "2025-02-21T10:00:00+08:00",
        "end": "2025-02-21T11:00:00+08:00"
      }
    }
  }'
```

### 9.2 Python 客户端示例

```python
import requests
import json

BASE_URL = "http://localhost:8020/mcp"
HEADERS = {"Content-Type": "application/json"}
request_id = 0

def call_mcp(method: str, params: dict = None) -> dict:
    """发送 MCP JSON-RPC 请求"""
    global request_id
    request_id += 1
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
    }
    if params:
        payload["params"] = params

    response = requests.post(BASE_URL, headers=HEADERS, json=payload)
    return response.json()

# 初始化
result = call_mcp("initialize", {
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "clientInfo": {"name": "python-client", "version": "1.0"}
})
print("Server:", result["result"]["serverInfo"])

# 初始化完成通知（无 id，需单独发）
requests.post(BASE_URL, headers=HEADERS, json={
    "jsonrpc": "2.0",
    "method": "notifications/initialized"
})

# 列出日历
result = call_mcp("tools/call", {
    "name": "list_calendars",
    "arguments": {}
})
print(result["result"]["content"][0]["text"])

# 列出事件
result = call_mcp("tools/call", {
    "name": "list_events",
    "arguments": {
        "calendar_id": "primary",
        "time_min": "2025-02-21T00:00:00Z",
        "time_max": "2025-02-28T23:59:59Z",
        "max_results": 20
    }
})
print(result["result"]["content"][0]["text"])

# 创建事件
result = call_mcp("tools/call", {
    "name": "create_event",
    "arguments": {
        "summary": "通过 MCP 创建的事件",
        "start": "2025-02-21T14:00:00+08:00",
        "end": "2025-02-21T15:00:00+08:00"
    }
})
print(result["result"]["content"][0]["text"])
```

### 9.3 典型工作流

```
┌─────────────────────────────────────────────────────┐
│              推荐的调用顺序                           │
│                                                      │
│  1. initialize          → 握手建立连接                │
│  2. notifications/initialized → 初始化完成             │
│  3. list_calendars       → 获取可用日历（可选）        │
│  4. list_events          → 按时间/关键词查事件         │
│  5. get_event            → 获取单个事件详情（按需）    │
│  6. create_event /       → 执行写入操作（按需）        │
│     update_event /                                    │
│     delete_event                                      │
└─────────────────────────────────────────────────────┘
```

---

## 附录

### A. 环境变量

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `GCAL_CREDS_DIR` | 凭证文件存储目录 | `~/.config/gcal-mcp` |
| `PORT` | HTTP 服务端口（Docker） | `8000` |

### B. 命令行参数

```
python gcal_mcp_server.py [command] [options]

Commands:
  auth     运行 OAuth 认证流程
  serve    启动 MCP 服务器（默认）

Options:
  --transport {stdio,http}  传输模式（默认: stdio）
  --host HOST               HTTP 监听地址（默认: 0.0.0.0）
  --port PORT               HTTP 监听端口（默认: 8000）
```

### C. Docker 部署

```bash
# 使用 Docker Compose（若项目提供）
docker-compose up -d

# 端口映射: 宿主机 8020 → 容器 8000
# 健康检查: curl http://localhost:8020/health
# 挂载: 将凭证目录挂载到容器内 GCAL_CREDS_DIR（如 /config）
```

### D. 反向代理

若通过 Nginx 等反向代理对外暴露：

| 项目 | 值 |
|------|-----|
| 宿主机端口示例 | 8020 |
| 对外路径示例 | `/gcal-mcp/mcp`、`/gcal-mcp/health` |
| MCP 客户端 URL | `https://<域名>/gcal-mcp/mcp` |

### E. MCP 方法速查

| 方法 | 方向 | 说明 |
|------|------|------|
| `initialize` | Client → Server | 初始化握手 |
| `notifications/initialized` | Client → Server | 初始化完成通知 |
| `resources/list` | Client → Server | 本服务未实现 |
| `resources/read` | Client → Server | 本服务未实现 |
| `tools/list` | Client → Server | 列出可用工具 |
| `tools/call` | Client → Server | 调用工具 |

### F. 公开日历与节假日（美国等）

Calendar API 本身不提供“节假日接口”，但 Google 提供**公开节假日日历**，可作为“通用信息”用现有接口获取。

**当前能力：**

1. **`list_calendars`**  
   返回当前账号**已订阅/可访问**的日历列表。若用户在 Google 日历里添加了“美国节假日”等订阅，这里会看到对应日历及其 `id`，之后可用 `list_events(calendar_id=该id, ...)` 查事件。

2. **`list_events` + 公开日历 ID**  
   部分公开日历即使用户未订阅，也可用固定 **calendar_id** 直接查（视 Google 策略而定）。常用示例：

| 日历 | calendar_id |
|------|------------------------------------------|
| 美国节假日 | `en.usa#holiday@group.v.calendar.google.com` |
| 中国节假日 | `zh.cn#holiday@group.v.calendar.google.com` |
| 英国节假日 | `en.uk#holiday@group.v.calendar.google.com` |

**示例：查美国节假日（某月）**

```json
{
  "name": "list_events",
  "arguments": {
    "calendar_id": "en.usa#holiday@group.v.calendar.google.com",
    "time_min": "2026-01-01T00:00:00Z",
    "time_max": "2026-01-31T23:59:59Z"
  }
}
```

更多国家/宗教节假日 ID 可参考社区列表（如 [Public Holiday Calendars](https://gist.github.com/dhoeric/76bd1c15168ee0ee61ad3bf1730dcb65)）。若某 ID 返回 404，表示当前认证或权限下不可读，可让用户先在 Google 日历网页中订阅该日历后再用 `list_calendars` 获取其 id。

---

**文档结束**
