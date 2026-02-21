# MCP Server Collection

这个仓库包含我所有的 MCP (Model Context Protocol) 服务实现。

## 已实现的 MCP 服务

| 服务 | 描述 | 状态 |
|------|------|------|
| [gdrive-mcp](./gdrive-mcp/) | Google Drive MCP Server - 支持读取、搜索 Google Drive 文件 | ✅ 可用 |
| [gcal-mcp](./gcal-mcp/) | Google Calendar MCP Server - 读取与修改日历事件 | ✅ 可用 |

## 目录结构

```
MCP-Service/
├── config/             # 共享凭证目录（OAuth 密钥与各 MCP 的 credentials）
│   ├── gdrive/         # Drive MCP 使用
│   ├── gcal/           # Calendar MCP 使用
│   └── README.md       # 配置说明
├── gdrive-mcp/         # Google Drive MCP Server
├── gcal-mcp/           # Google Calendar MCP Server
└── README.md
```

Docker Compose 已配置为挂载 `config/gdrive`、`config/gcal`，详见 [config/README.md](./config/README.md)。

## 反向代理与对外路径

若前面有 Nginx/反向代理，可按路径转发到容器：

| 服务       | 容器内端口 | 宿主机端口 | 对外路径（示例）     |
|------------|------------|------------|----------------------|
| gdrive-mcp | 8000       | 8010       | `/gdrive-mcp/mcp`   |
| gcal-mcp   | 8000       | 8020       | `/gcal-mcp/mcp`     |

- 健康检查：`/gdrive-mcp/health` → 转发到 `http://localhost:8010/health`，gcal 同理用 8020。
- MCP 客户端填的 URL：`https://你的域名/gdrive-mcp/mcp`、`https://你的域名/gcal-mcp/mcp`。
- **Nginx 配置示例**（含 gdrive-mcp + gcal-mcp）：见 [config/nginx/](./config/nginx/)，按说明在服务器上添加 `/gcal-mcp/` 即可解决 gcal-mcp 公网 404。

## 什么是 MCP？

[Model Context Protocol (MCP)](https://modelcontextprotocol.io/) 是一个开放协议，允许 AI 应用（如 Claude）与外部数据源和工具进行交互。

## 快速开始

进入对应的 MCP 服务目录，按照各自的 README 进行配置和部署。

## License

MIT
