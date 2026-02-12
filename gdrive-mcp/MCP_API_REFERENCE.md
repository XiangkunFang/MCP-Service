# Google Drive MCP Server — 标准接口文档

> **服务名称**: `gdrive-mcp-server`
> **协议版本**: MCP `2024-11-05`
> **服务版本**: `1.0.0`
> **最后更新**: 2026-02-12
> **语言/运行时**: Python 3.12+

---

## 目录

1. [服务概述](#1-服务概述)
2. [连接方式](#2-连接方式)
3. [认证与初始化](#3-认证与初始化)
4. [能力声明 (Capabilities)](#4-能力声明-capabilities)
5. [Resources 接口](#5-resources-接口)
6. [Tools 接口](#6-tools-接口)
7. [文件类型处理规则](#7-文件类型处理规则)
8. [错误处理](#8-错误处理)
9. [集成示例](#9-集成示例)
10. [附录](#附录)

---

## 1. 服务概述

Google Drive MCP Server 是一个实现了 [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) 的服务，允许 MCP 客户端（如 Claude Desktop、Claude Code 等）通过标准化的 JSON-RPC 2.0 协议访问和操作用户的 Google Drive 文件。

### 1.1 功能摘要

| 功能类别 | 能力 |
|---------|------|
| **读取** | 列出文件、浏览文件夹、搜索文件、读取文件内容、获取文件树 |
| **写入** | 创建文件、更新文件、创建文件夹 |
| **管理** | 删除文件（移至回收站）、移动文件 |

### 1.2 支持的 MCP 特性

| MCP 特性 | 支持状态 |
|----------|---------|
| Resources | ✅ 支持 (list + read) |
| Tools | ✅ 支持 (9 个工具) |
| Prompts | ❌ 未实现 |
| Sampling | ❌ 未实现 |
| Notifications | ✅ 支持 (`notifications/initialized`) |

---

## 2. 连接方式

### 2.1 stdio 模式（本地进程）

适用于 MCP 客户端以子进程方式启动服务器的场景。

**启动命令:**
```bash
python gdrive_mcp_server.py
# 或
python gdrive_mcp_server.py --transport stdio
```

**客户端配置示例 (Claude Desktop):**
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

**通信方式:** 通过 `stdin` 发送 JSON-RPC 请求，通过 `stdout` 接收 JSON-RPC 响应。

### 2.2 HTTP 模式（远程/Docker）

适用于远程部署、Docker 容器化运行，支持多客户端并发访问。

**启动命令:**
```bash
python gdrive_mcp_server.py --transport http --host 0.0.0.0 --port 8000
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

**客户端配置示例 (Claude Desktop):**
```json
{
  "mcpServers": {
    "gdrive": {
      "url": "http://localhost:8010/mcp"
    }
  }
}
```

**请求格式:**
```http
POST /mcp HTTP/1.1
Host: localhost:8010
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
curl http://localhost:8010/health
```

**响应:**
```json
{
  "status": "healthy",
  "server": "gdrive-mcp-server"
}
```

---

## 3. 认证与初始化

### 3.1 前置条件

调用方不需要处理 Google OAuth 认证——认证在服务端完成。但服务端需要提前配置好以下凭证：

| 文件 | 说明 | 获取方式 |
|-----|------|---------|
| `gcp-oauth.keys.json` | Google OAuth 应用密钥 | 从 [Google Cloud Console](https://console.cloud.google.com/) 下载 |
| `credentials.json` | 用户授权后的访问令牌 | 运行 `python gdrive_mcp_server.py auth` 生成 |

凭证默认存放在 `~/.config/gdrive-mcp/`，可通过环境变量 `GDRIVE_CREDS_DIR` 自定义。

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
      "name": "gdrive-mcp-server",
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
| `resources` | 支持 `resources/list` 和 `resources/read`，不支持资源变更通知 |
| `tools` | 支持 `tools/list` 和 `tools/call`，不支持工具变更通知 |

---

## 5. Resources 接口

Resources 提供对 Google Drive 文件的标准化读取访问。

### 5.1 列出资源 — `resources/list`

分页列出 Google Drive 中的文件。

**请求:**
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

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `cursor` | `string \| null` | 否 | 分页游标，首次请求传 `null` |

**响应:**
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
      },
      {
        "uri": "gdrive:///2DEF456abc",
        "name": "数据表.xlsx",
        "mimeType": "application/vnd.google-apps.spreadsheet"
      }
    ],
    "nextCursor": "CAE="
  }
}
```

| 响应字段 | 说明 |
|---------|------|
| `resources[].uri` | 资源 URI，格式为 `gdrive:///{file_id}` |
| `resources[].name` | 文件名 |
| `resources[].mimeType` | MIME 类型 |
| `nextCursor` | 下一页游标，为 `null` 时表示没有更多数据 |

### 5.2 读取资源 — `resources/read`

读取指定文件的内容。

**请求:**
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

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `uri` | `string` | 是 | 资源 URI，格式: `gdrive:///{file_id}` |

**响应 (文本内容):**
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "contents": [
      {
        "uri": "gdrive:///1ABC123xyz",
        "mimeType": "text/markdown",
        "text": "# 文档标题\n\n文档内容..."
      }
    ]
  }
}
```

**响应 (二进制内容):**
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

返回所有可用工具的列表及其 `inputSchema`。注意：部分工具的 `description` 字段中包含**动态生成的根目录文件夹列表**，每次请求时可能不同。

---

### 6.1 get_file_tree — 获取完整文件树

获取 Google Drive 的完整文件系统树状结构快照。**推荐首先调用此工具**来了解 Drive 的整体结构。

**请求:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "get_file_tree",
    "arguments": {
      "max_files": 500
    }
  }
}
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `max_files` | `integer` | 否 | `500` | 最大获取文件数，用于控制响应大小和速度 |

**响应:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "📊 Google Drive 文件系统快照\n   总计: 42 项 (5 个文件夹, 37 个文件)\n\n📂 根目录\n├── 📁 Projects [folder_id: 1abc...]\n│   ├── 📄 README.md [file_id: 2def...]\n│   └── 📄 notes.txt [file_id: 3ghi...]\n└── 📁 Documents [folder_id: 4jkl...]"
      }
    ]
  }
}
```

---

### 6.2 list_folder — 列出文件夹内容

列出指定文件夹中的文件和子文件夹。

**请求:**
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "list_folder",
    "arguments": {
      "folder_id": "1abc123xyz"
    }
  }
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `folder_id` | `string` | 否 | 文件夹 ID。省略或传 `"root"` 表示根目录 |

**响应:**
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "📂 文件夹 内容 (3 项):\n\n📁 子文件夹:\n  - SubFolder [folder_id: xxx]\n\n📄 文件:\n  - document.md (text/markdown) [file_id: yyy]\n  - data.json (application/json) [file_id: zzz]"
      }
    ]
  }
}
```

---

### 6.3 search — 搜索文件

全文搜索 Google Drive 中的文件名和文件内容。

**请求:**
```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "search",
    "arguments": {
      "query": "项目计划",
      "folder_id": "1abc123xyz"
    }
  }
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `query` | `string` | **是** | 搜索关键词 |
| `folder_id` | `string` | 否 | 限制搜索范围到指定文件夹。省略则搜索整个 Drive |

**响应:**
```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "在 整个 Drive 中找到 2 个文件:\n- 项目计划书.docx (application/vnd.google-apps.document) [file_id: abc123]\n- 2026项目计划.md (text/markdown) [file_id: def456]"
      }
    ]
  }
}
```

---

### 6.4 read_file — 读取文件内容

按文件 ID 读取文件内容。文件 ID 可从 `get_file_tree`、`list_folder` 或 `search` 的结果中获取。

**请求:**
```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "method": "tools/call",
  "params": {
    "name": "read_file",
    "arguments": {
      "file_id": "1ABC123xyz"
    }
  }
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_id` | `string` | **是** | Google Drive 文件 ID |

**响应:**
```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "# 文档标题\n\n这是文档内容..."
      }
    ]
  }
}
```

> **注意:** 二进制文件返回 `[Binary content, N bytes]` 的文本描述。

---

### 6.5 create_file — 创建文件

在 Google Drive 中创建新文件。

**请求:**
```json
{
  "jsonrpc": "2.0",
  "id": 5,
  "method": "tools/call",
  "params": {
    "name": "create_file",
    "arguments": {
      "name": "notes.md",
      "content": "# 笔记\n\n这是内容",
      "folder_id": "1abc123xyz"
    }
  }
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | `string` | **是** | 文件名（如 `notes.txt`、`data.json`、`readme.md`） |
| `content` | `string` | **是** | 文件文本内容 |
| `folder_id` | `string` | 否 | 父文件夹 ID。省略则创建在根目录 |

**自动 MIME 类型检测:**

| 扩展名 | MIME 类型 |
|--------|-----------|
| `.md` | `text/markdown` |
| `.json` | `application/json` |
| `.html` | `text/html` |
| `.csv` | `text/csv` |
| 其他 | `text/plain` |

**响应:**
```json
{
  "jsonrpc": "2.0",
  "id": 5,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "✅ 文件创建成功!\n   名称: notes.md\n   ID: 1XYZ789abc\n   类型: text/markdown\n   链接: https://drive.google.com/file/d/1XYZ789abc/view"
      }
    ]
  }
}
```

---

### 6.6 update_file — 更新文件

更新已有文件的内容。

**请求:**
```json
{
  "jsonrpc": "2.0",
  "id": 6,
  "method": "tools/call",
  "params": {
    "name": "update_file",
    "arguments": {
      "file_id": "1XYZ789abc",
      "content": "# 更新后的笔记\n\n新内容"
    }
  }
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_id` | `string` | **是** | 要更新的文件 ID |
| `content` | `string` | **是** | 新的文件内容（完全替换） |

**响应:**
```json
{
  "jsonrpc": "2.0",
  "id": 6,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "✅ 文件更新成功!\n   名称: notes.md\n   ID: 1XYZ789abc\n   修改时间: 2026-02-12T10:30:00.000Z"
      }
    ]
  }
}
```

---

### 6.7 create_folder — 创建文件夹

**请求:**
```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "method": "tools/call",
  "params": {
    "name": "create_folder",
    "arguments": {
      "name": "新项目",
      "parent_folder_id": "1abc123xyz"
    }
  }
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | `string` | **是** | 文件夹名称 |
| `parent_folder_id` | `string` | 否 | 父文件夹 ID。省略则创建在根目录 |

**响应:**
```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "✅ 文件夹创建成功!\n   名称: 新项目\n   ID: 1NEW456def\n   链接: https://drive.google.com/drive/folders/1NEW456def"
      }
    ]
  }
}
```

---

### 6.8 delete_file — 删除文件

将文件或文件夹移至回收站（可恢复）。

**请求:**
```json
{
  "jsonrpc": "2.0",
  "id": 8,
  "method": "tools/call",
  "params": {
    "name": "delete_file",
    "arguments": {
      "file_id": "1XYZ789abc"
    }
  }
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_id` | `string` | **是** | 要删除的文件或文件夹 ID |

**响应:**
```json
{
  "jsonrpc": "2.0",
  "id": 8,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "✅ 文件已移至回收站 (ID: 1XYZ789abc)\n   提示: 可在 Google Drive 回收站中恢复"
      }
    ]
  }
}
```

> **安全说明:** 此操作不会永久删除文件，而是移至 Google Drive 回收站，可手动恢复。

---

### 6.9 move_file — 移动文件

将文件或文件夹移动到另一个文件夹。

**请求:**
```json
{
  "jsonrpc": "2.0",
  "id": 9,
  "method": "tools/call",
  "params": {
    "name": "move_file",
    "arguments": {
      "file_id": "1XYZ789abc",
      "new_parent_id": "1DEST000xyz"
    }
  }
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_id` | `string` | **是** | 要移动的文件或文件夹 ID |
| `new_parent_id` | `string` | **是** | 目标文件夹 ID |

**响应:**
```json
{
  "jsonrpc": "2.0",
  "id": 9,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "✅ 文件移动成功!\n   名称: notes.md\n   ID: 1XYZ789abc\n   新位置: 1DEST000xyz"
      }
    ]
  }
}
```

---

### 6.10 Tools 速查表

| 工具名 | 说明 | 必填参数 | 可选参数 |
|--------|------|----------|----------|
| `get_file_tree` | 获取完整文件树 | — | `max_files` |
| `list_folder` | 列出文件夹内容 | — | `folder_id` |
| `search` | 全文搜索 | `query` | `folder_id` |
| `read_file` | 读取文件内容 | `file_id` | — |
| `create_file` | 创建文件 | `name`, `content` | `folder_id` |
| `update_file` | 更新文件内容 | `file_id`, `content` | — |
| `create_folder` | 创建文件夹 | `name` | `parent_folder_id` |
| `delete_file` | 删除文件（移至回收站） | `file_id` | — |
| `move_file` | 移动文件 | `file_id`, `new_parent_id` | — |

---

## 7. 文件类型处理规则

服务器在读取文件时，会根据 MIME 类型自动转换格式：

| 原始类型 | MIME Type | 导出/处理方式 | 返回格式 |
|---------|-----------|--------------|---------|
| Google Docs | `application/vnd.google-apps.document` | 导出为 Markdown | `text/markdown` |
| Google Sheets | `application/vnd.google-apps.spreadsheet` | 导出为 CSV | `text/csv` |
| Google Slides | `application/vnd.google-apps.presentation` | 导出为纯文本 | `text/plain` |
| Google Drawing | `application/vnd.google-apps.drawing` | 导出为 PNG | `image/png` |
| 文本文件 | `text/*` | 直接读取 | UTF-8 文本 |
| JSON 文件 | `application/json` | 直接读取 | UTF-8 文本 |
| 二进制文件 | 其他 | Base64 编码 | Base64 文本 |

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
|--------|------|---------|
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
        "text": "Error: file_id is required"
      }
    ],
    "isError": true
  }
}
```

### 8.3 常见错误场景

| 错误信息 | 原因 | 解决方案 |
|---------|------|---------|
| `Error: file_id is required` | 缺少必填参数 | 提供 `file_id` 参数 |
| `Error: name is required` | 缺少文件名 | 提供 `name` 参数 |
| `Unknown tool: xxx` | 调用了不存在的工具 | 先调用 `tools/list` 获取可用工具列表 |
| `获取文件树失败: ...` | Google Drive API 调用失败 | 检查认证凭证是否有效 |
| `凭证无效` | OAuth 令牌过期 | 重新运行 `python gdrive_mcp_server.py auth` |

---

## 9. 集成示例

### 9.1 完整调用流程 (HTTP 模式)

```bash
# 步骤 1: 健康检查
curl http://localhost:8010/health

# 步骤 2: 初始化握手
curl -X POST http://localhost:8010/mcp \
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
curl -X POST http://localhost:8010/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "method": "notifications/initialized"}'

# 步骤 4: 列出可用工具
curl -X POST http://localhost:8010/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}'

# 步骤 5: 获取文件树
curl -X POST http://localhost:8010/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 3,
    "method": "tools/call",
    "params": {
      "name": "get_file_tree",
      "arguments": {"max_files": 100}
    }
  }'

# 步骤 6: 搜索文件
curl -X POST http://localhost:8010/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 4,
    "method": "tools/call",
    "params": {
      "name": "search",
      "arguments": {"query": "项目计划"}
    }
  }'

# 步骤 7: 读取文件内容
curl -X POST http://localhost:8010/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 5,
    "method": "tools/call",
    "params": {
      "name": "read_file",
      "arguments": {"file_id": "YOUR_FILE_ID_HERE"}
    }
  }'
```

### 9.2 Python 客户端示例

```python
import requests
import json

BASE_URL = "http://localhost:8010/mcp"
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

# 获取文件树
result = call_mcp("tools/call", {
    "name": "get_file_tree",
    "arguments": {"max_files": 50}
})
print(result["result"]["content"][0]["text"])

# 搜索文件
result = call_mcp("tools/call", {
    "name": "search",
    "arguments": {"query": "会议纪要"}
})
print(result["result"]["content"][0]["text"])

# 创建文件
result = call_mcp("tools/call", {
    "name": "create_file",
    "arguments": {
        "name": "test-note.md",
        "content": "# 测试笔记\n\n通过 MCP 创建的文件"
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
│  2. get_file_tree       → 获取全局文件结构概览        │
│  3. list_folder / search → 定位具体文件               │
│  4. read_file            → 读取目标文件内容           │
│  5. create_file /        → 执行写入操作（按需）       │
│     update_file /                                    │
│     create_folder /                                  │
│     delete_file /                                    │
│     move_file                                        │
└─────────────────────────────────────────────────────┘
```

---

## 附录

### A. 环境变量

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `GDRIVE_CREDS_DIR` | 凭证文件存储目录 | `~/.config/gdrive-mcp` |
| `PORT` | HTTP 服务端口（Docker） | `8000` |

### B. 命令行参数

```
python gdrive_mcp_server.py [command] [options]

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
# 使用 Docker Compose（推荐）
docker-compose up -d

# 端口映射: 宿主机 8010 → 容器 8000
# 健康检查: curl http://localhost:8010/health
```

### D. URI 格式规范

本服务使用的资源 URI 格式：

```
gdrive:///{file_id}
```

- 协议: `gdrive`
- 路径: 以 `/` 开头，后跟 Google Drive 文件 ID
- 示例: `gdrive:///1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms`

### E. MCP 方法速查

| 方法 | 方向 | 说明 |
|------|------|------|
| `initialize` | Client → Server | 初始化握手 |
| `notifications/initialized` | Client → Server | 初始化完成通知 |
| `resources/list` | Client → Server | 列出可用资源 |
| `resources/read` | Client → Server | 读取资源内容 |
| `tools/list` | Client → Server | 列出可用工具 |
| `tools/call` | Client → Server | 调用工具 |

---

**文档结束**
