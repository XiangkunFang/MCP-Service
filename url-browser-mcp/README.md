# URL Browser MCP Server

为 AI Agent 提供网页浏览能力的 MCP 服务。Agent 可以通过这个 MCP 读取任意 URL 的内容，进行总结、提取信息、回答用户问题。

## 功能

| 工具 | 描述 |
|------|------|
| `browse_url` | 浏览 URL 并返回 Markdown 格式的可读内容（支持 HTML、JSON、纯文本） |
| `extract_links` | 提取页面中的所有超链接及其文本 |

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 启动服务器

**stdio 模式（本地/Claude Desktop）：**

```bash
python url_browser_mcp_server.py
```

**HTTP 模式（Docker/远程）：**

```bash
python url_browser_mcp_server.py --transport http --port 8000
```

### 3. Docker 部署

```bash
docker-compose up -d
```

服务将在 `http://localhost:8020` 启动。

## Claude Desktop 配置

在 `claude_desktop_config.json` 中添加：

```json
{
  "mcpServers": {
    "url-browser": {
      "command": "python",
      "args": ["/path/to/url_browser_mcp_server.py"]
    }
  }
}
```

## 环境变量

| 变量 | 默认值 | 描述 |
|------|--------|------|
| `URL_BROWSER_TIMEOUT` | `30` | HTTP 请求超时时间（秒） |
| `URL_BROWSER_MAX_LENGTH` | `500000` | 内容最大长度（字符） |
| `URL_BROWSER_USER_AGENT` | (内置) | 自定义 User-Agent |

## 使用场景

- Agent 读取用户提供的 URL，返回页面内容
- Agent 对网页内容进行总结
- Agent 提取页面链接，进行深度浏览
- Agent 读取 API 返回的 JSON 数据
