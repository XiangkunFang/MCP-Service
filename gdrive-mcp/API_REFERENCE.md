# Google Drive MCP Server — API Reference

> **服务名称**: `gdrive-mcp-server`  
> **协议版本**: MCP `2024-11-05`  
> **服务版本**: `1.0.0`  
> **最后更新**: 2026-02-12  
> **语言/运行时**: Python 3.12+

---

## 目录

1. [服务概述](#1-服务概述)
2. [连接与端点](#2-连接与端点)
3. [认证与初始化](#3-认证与初始化)
4. [能力声明](#4-能力声明)
5. [Resources API](#5-resources-api)
6. [Tools API](#6-tools-api)
7. [文件类型与 MIME](#7-文件类型与-mime)
8. [错误码与错误响应](#8-错误码与错误响应)
9. [集成示例](#9-集成示例)
10. [附录](#附录)

---

## 1. 服务概述

| 项目 | 说明 |
|------|------|
| 协议 | JSON-RPC 2.0 over stdio / HTTP |
| 功能 | 列出、读取、搜索、创建、更新、删除、移动 Google Drive 文件 |
| MCP 特性 | Resources ✅ \| Tools ✅ \| Prompts ❌ \| Sampling ❌ \| Notifications ✅ |

---

## 2. 连接与端点

### 2.1 传输模式

| 模式 | 启动命令 | 说明 |
|------|----------|------|
| stdio | `python gdrive_mcp_server.py` | 本地子进程，stdin/stdout |
| HTTP | `python gdrive_mcp_server.py --transport http --host 0.0.0.0 --port 8000` | 远程/Docker，多客户端 |

### 2.2 HTTP 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/mcp` | `POST` | 主 MCP JSON-RPC 端点 |
| `/message` | `POST` | MCP 别名 |
| `/` | `POST` | 根路径 MCP |
| `/health` | `GET` | 健康检查 |
| `/` | `GET` | 根路径健康检查 |
| `/sse` | `GET` | Server-Sent Events 流 |

### 2.3 健康检查

**请求**
```http
GET /health
```

**响应**
```json
{
  "status": "healthy",
  "server": "gdrive-mcp-server"
}
```

---

## 3. 认证与初始化

### 3.1 服务端凭证

| 文件 | 说明 |
|------|------|
| `gcp-oauth.keys.json` | OAuth 应用密钥（Google Cloud Console） |
| `credentials.json` | 用户授权令牌（由 `python gdrive_mcp_server.py auth` 生成） |

默认目录: `~/.config/gdrive-mcp`，可通过 `GDRIVE_CREDS_DIR` 覆盖。

### 3.2 初始化握手

**请求**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "clientInfo": { "name": "client-name", "version": "1.0.0" }
  }
}
```

**响应**
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
    "serverInfo": { "name": "gdrive-mcp-server", "version": "1.0.0" }
  }
}
```

### 3.3 初始化完成通知

**请求（无 id）**
```json
{
  "jsonrpc": "2.0",
  "method": "notifications/initialized"
}
```

---

## 4. 能力声明

| 能力 | 说明 |
|------|------|
| `resources` | 支持 `resources/list`、`resources/read`；`listChanged: false` |
| `tools` | 支持 `tools/list`、`tools/call`；`listChanged: false` |

---

## 5. Resources API

### 5.1 resources/list

分页列出 Drive 中的文件（资源）。

| 项目 | 说明 |
|------|------|
| **方法** | `resources/list` |
| **Params** | `cursor?: string \| null` — 分页游标，首次为 `null` |

**请求**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "resources/list",
  "params": { "cursor": null }
}
```

**响应**
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

| 字段 | 类型 | 说明 |
|------|------|------|
| `resources[].uri` | string | `gdrive:///{file_id}` |
| `resources[].name` | string | 文件名 |
| `resources[].mimeType` | string | MIME 类型 |
| `nextCursor` | string \| null | 下一页游标，`null` 表示无更多 |

---

### 5.2 resources/read

按 URI 读取文件内容。

| 项目 | 说明 |
|------|------|
| **方法** | `resources/read` |
| **Params** | `uri`: string（必填）— 格式 `gdrive:///{file_id}` |

**请求**
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "resources/read",
  "params": { "uri": "gdrive:///1ABC123xyz" }
}
```

**响应（文本）**
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "contents": [
      {
        "uri": "gdrive:///1ABC123xyz",
        "mimeType": "text/markdown",
        "text": "# 文档标题\n\n内容..."
      }
    ]
  }
}
```

**响应（二进制）**
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "contents": [
      {
        "uri": "gdrive:///1ABC123xyz",
        "mimeType": "image/png",
        "blob": "iVBORw0KGgo..."
      }
    ]
  }
}
```

---

## 6. Tools API

### 6.1 tools/list

列出所有工具及 `inputSchema`（部分工具的 `description` 含动态根目录信息）。

**请求**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/list"
}
```

**响应**  
返回 `result.tools[]`，每项含 `name`、`description`、`inputSchema`。

---

### 6.2 tools/call — 工具速查

| 工具名 | 说明 | 必填参数 | 可选参数 |
|--------|------|----------|----------|
| `get_file_tree` | 获取完整文件树 | — | `max_files` |
| `list_folder` | 列出文件夹内容 | — | `folder_id` |
| `search` | 全文搜索 | `query` | `folder_id` |
| `read_file` | 读取文件内容 | `file_id` | — |
| `create_file` | 创建文件 | `name`, `content` | `folder_id` |
| `update_file` | 更新文件内容 | `file_id`, `content` | — |
| `create_folder` | 创建文件夹 | `name` | `parent_folder_id` |
| `delete_file` | 移至回收站 | `file_id` | — |
| `move_file` | 移动文件/文件夹 | `file_id`, `new_parent_id` | — |

**通用请求格式**
```json
{
  "jsonrpc": "2.0",
  "id": <number>,
  "method": "tools/call",
  "params": {
    "name": "<tool_name>",
    "arguments": { ... }
  }
}
```

**通用成功响应**
```json
{
  "jsonrpc": "2.0",
  "id": <number>,
  "result": {
    "content": [{ "type": "text", "text": "..." }],
    "isError": false
  }
}
```

**通用错误响应（工具层）**
```json
{
  "jsonrpc": "2.0",
  "id": <number>,
  "result": {
    "content": [{ "type": "text", "text": "Error: ..." }],
    "isError": true
  }
}
```

---

### 6.3 get_file_tree

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `max_files` | integer | 否 | 500 | 最大文件数 |

**响应示例**
```text
📊 Google Drive 文件系统快照
   总计: 42 项 (5 个文件夹, 37 个文件)
📂 根目录
├── 📁 Projects [folder_id: 1abc...]
│   ├── 📄 README.md [file_id: 2def...]
└── 📁 Documents [folder_id: 4jkl...]
```

---

### 6.4 list_folder

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `folder_id` | string | 否 | 文件夹 ID；省略或 `"root"` 为根目录 |

---

### 6.5 search

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `query` | string | 是 | 搜索关键词 |
| `folder_id` | string | 否 | 限定搜索范围 |

---

### 6.6 read_file

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_id` | string | 是 | Google Drive 文件 ID |

二进制文件在 `content[].text` 中返回 `[Binary content, N bytes]` 描述。

---

### 6.7 create_file

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 文件名（如 `notes.md`） |
| `content` | string | 是 | 文本内容 |
| `folder_id` | string | 否 | 父文件夹 ID，省略为根目录 |

MIME 根据扩展名推断：`.md` → `text/markdown`，`.json` → `application/json`，`.html` → `text/html`，`.csv` → `text/csv`，其他 → `text/plain`。

---

### 6.8 update_file

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_id` | string | 是 | 文件 ID |
| `content` | string | 是 | 新内容（全文替换） |

---

### 6.9 create_folder

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 文件夹名称 |
| `parent_folder_id` | string | 否 | 父文件夹 ID，省略为根目录 |

---

### 6.10 delete_file

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_id` | string | 是 | 文件或文件夹 ID（移至回收站，可恢复） |

---

### 6.11 move_file

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_id` | string | 是 | 要移动的文件/文件夹 ID |
| `new_parent_id` | string | 是 | 目标文件夹 ID |

---

## 7. 文件类型与 MIME

| 原始类型 | MIME | 处理方式 | 返回格式 |
|---------|------|----------|----------|
| Google Docs | `application/vnd.google-apps.document` | 导出为 Markdown | `text/markdown` |
| Google Sheets | `application/vnd.google-apps.spreadsheet` | 导出为 CSV | `text/csv` |
| Google Slides | `application/vnd.google-apps.presentation` | 导出为纯文本 | `text/plain` |
| Google Drawing | `application/vnd.google-apps.drawing` | 导出为 PNG | `image/png` |
| 文本 | `text/*` | 直接读取 | UTF-8 文本 |
| JSON | `application/json` | 直接读取 | UTF-8 文本 |
| 其他 | 其他 | Base64 | Base64 文本 |

---

## 8. 错误码与错误响应

### 8.1 JSON-RPC 错误

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -32601,
    "message": "Method not found: ..."
  }
}
```

| code | 含义 |
|------|------|
| -32700 | Parse error |
| -32601 | Method not found |
| -32603 | Internal error |

### 8.2 工具错误

通过 `result.isError === true` 与 `result.content[].text` 表示错误信息。

### 8.3 常见错误与处理

| 错误信息 | 原因 | 处理 |
|---------|------|------|
| `Error: file_id is required` | 缺少 `file_id` | 传入 `file_id` |
| `Error: name is required` | 缺少 `name` | 传入 `name` |
| `Unknown tool: xxx` | 工具名不存在 | 先调 `tools/list` |
| `获取文件树失败: ...` | Drive API 失败 | 检查凭证与网络 |
| `凭证无效` | OAuth 过期 | 执行 `python gdrive_mcp_server.py auth` |

---

## 9. 集成示例

### 9.1 推荐调用顺序

1. `initialize` — 握手  
2. `get_file_tree` — 获取结构概览  
3. `list_folder` / `search` — 定位文件  
4. `read_file` — 读取内容  
5. 按需：`create_file` / `update_file` / `create_folder` / `delete_file` / `move_file`

### 9.2 cURL 示例（HTTP）

```bash
# 健康检查
curl http://localhost:8010/health

# 初始化
curl -X POST http://localhost:8010/mcp -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"cli","version":"1.0"}}}'

# 初始化完成
curl -X POST http://localhost:8010/mcp -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'

# 列出工具
curl -X POST http://localhost:8010/mcp -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'

# 调用 get_file_tree
curl -X POST http://localhost:8010/mcp -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_file_tree","arguments":{"max_files":100}}}'
```

### 9.3 Python 请求示例

```python
import requests

BASE = "http://localhost:8010/mcp"
def mcp(method, params=None):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params:
        payload["params"] = params
    return requests.post(BASE, json=payload).json()

# 初始化
mcp("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "py", "version": "1.0"}})
mcp("notifications/initialized")  # 无 id

# 获取文件树
r = mcp("tools/call", {"name": "get_file_tree", "arguments": {"max_files": 50}})
print(r["result"]["content"][0]["text"])
```

---

## 附录

### A. 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `GDRIVE_CREDS_DIR` | 凭证目录 | `~/.config/gdrive-mcp` |
| `GDRIVE_ROOT_FOLDER_ID` | 限定根目录（仅操作该文件夹及子项） | 未设置则整个 Drive |
| `PORT` | HTTP 端口（如 Docker） | 8000 |

### B. 命令行

```
python gdrive_mcp_server.py [command] [options]

Commands:
  auth    执行 OAuth 认证
  serve   启动 MCP 服务（默认）

Options:
  --transport {stdio,http}  默认 stdio
  --host HOST                默认 0.0.0.0
  --port PORT                默认 8000
```

### C. URI 格式

```
gdrive:///{file_id}
```

示例: `gdrive:///1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms`

### D. MCP 方法速查

| 方法 | 方向 | 说明 |
|------|------|------|
| `initialize` | Client → Server | 初始化握手 |
| `notifications/initialized` | Client → Server | 初始化完成 |
| `resources/list` | Client → Server | 列出资源 |
| `resources/read` | Client → Server | 读取资源 |
| `tools/list` | Client → Server | 列出工具 |
| `tools/call` | Client → Server | 调用工具 |

---

**文档结束**
