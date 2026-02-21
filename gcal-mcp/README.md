# Google Calendar MCP Server (Python)

用于读取和修改 Google Calendar 的 MCP 服务。

## 功能

- **列出日历**：查看可访问的日历列表
- **列出事件**：按时间范围或关键词查询事件
- **获取事件**：获取单个事件详情
- **创建事件**：新建日历事件（支持全天/带时间，ISO 8601）
- **更新事件**：修改已有事件
- **删除事件**：删除指定事件

支持 **stdio**（本地）和 **HTTP**（远程/Docker）两种传输模式。

## 快速开始

### 1. Google Cloud 配置

1. 在 [Google Cloud Console](https://console.cloud.google.com/) 中创建或选择项目
2. 启用 **Google Calendar API**
3. 创建 **OAuth 2.0 客户端 ID**（桌面应用），下载 JSON 凭证
4. 将凭证保存为 `config/gcp-oauth.keys.json`（或 `~/.config/gcal-mcp/gcp-oauth.keys.json`）

### 2. 本地运行（stdio）

```bash
pip install -r requirements.txt
mkdir -p config
# 将下载的 client_secret_xxx.json 放到 config 并重命名为 gcp-oauth.keys.json
python gcal_mcp_server.py auth
python gcal_mcp_server.py
```

### 3. Docker 部署（HTTP）

```bash
mkdir -p config
# 将 gcp-oauth.keys.json 放入 config/
python gcal_mcp_server.py auth   # 在本地完成认证，生成 config/credentials.json
docker compose up -d
```

服务默认监听 `http://localhost:8020`，健康检查：`curl http://localhost:8020/health`，MCP 端点：`POST http://localhost:8020/mcp`。

## 环境变量

| 变量 | 说明 | 默认 |
|------|------|------|
| `GCAL_CREDS_DIR` | 凭证目录（内含 `gcp-oauth.keys.json`、`credentials.json`） | `~/.config/gcal-mcp` |
| `PORT` | HTTP 模式端口（容器内） | 8000 |

## 时间格式说明

- **全天事件**：使用日期 `YYYY-MM-DD`（如 `2025-02-21`）
- **带时间事件**：使用 ISO 8601 并含时区（如 `2025-02-21T14:00:00+08:00`）

## MCP 客户端配置

### Claude Desktop（stdio）

```json
{
  "mcpServers": {
    "gcal": {
      "command": "python",
      "args": ["/path/to/gcal-mcp/gcal_mcp_server.py"],
      "env": {
        "GCAL_CREDS_DIR": "/path/to/.config/gcal-mcp"
      }
    }
  }
}
```

### Claude Desktop / Cursor（HTTP）

```json
{
  "mcpServers": {
    "gcal": {
      "url": "http://localhost:8020/mcp"
    }
  }
}
```

## 命令行参数

```bash
python gcal_mcp_server.py auth              # 执行 OAuth 认证
python gcal_mcp_server.py                   # stdio 模式（默认）
python gcal_mcp_server.py --transport http  # HTTP 模式
python gcal_mcp_server.py --transport http --host 0.0.0.0 --port 8000
```

## License

MIT
