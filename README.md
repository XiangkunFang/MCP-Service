# MCP Server Collection

这个仓库包含我所有的 MCP (Model Context Protocol) 服务实现。

## 已实现的 MCP 服务

| 服务 | 描述 | 状态 |
|------|------|------|
| [gdrive-mcp](./gdrive-mcp/) | Google Drive MCP Server - 支持读取、搜索 Google Drive 文件 | ✅ 可用 |

## 目录结构

```
mcp-servers/
├── gdrive-mcp/          # Google Drive MCP Server
│   ├── gdrive_mcp_server.py
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── README.md
├── [future-mcp]/        # 未来的 MCP 服务
└── README.md
```

## 什么是 MCP？

[Model Context Protocol (MCP)](https://modelcontextprotocol.io/) 是一个开放协议，允许 AI 应用（如 Claude）与外部数据源和工具进行交互。

## 快速开始

进入对应的 MCP 服务目录，按照各自的 README 进行配置和部署。

## License

MIT
