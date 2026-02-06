# Google Drive MCP Server (Python)

一个 Python 实现的 Google Drive MCP Server，基于官方 TypeScript 版本重写。

## 功能

- ✅ **列出文件** - 浏览 Google Drive 中的文件
- ✅ **读取文件** - 读取文件内容（支持 md、txt、Google Docs 等）
- ✅ **搜索文件** - 全文搜索 Google Drive
- ✅ **双模式支持** - stdio（本地）和 HTTP（远程/Docker）

### 支持的文件类型

| 文件类型 | 处理方式 |
|---------|---------|
| Google Docs | 导出为 Markdown |
| Google Sheets | 导出为 CSV |
| Google Slides | 导出为纯文本 |
| md / txt / json | 直接读取 |
| 其他二进制文件 | Base64 编码 |

## 快速开始

### 方式 1：本地运行（stdio 模式）

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 OAuth
mkdir -p ~/.config/gdrive-mcp
mv ~/Downloads/client_secret_xxx.json ~/.config/gdrive-mcp/gcp-oauth.keys.json

# 3. 运行认证
python gdrive_mcp_server.py auth

# 4. 启动服务器
python gdrive_mcp_server.py
```

### 方式 2：Docker 部署（HTTP 模式）⭐ 推荐

```bash
# 1. 本地先完成认证（生成凭证）
mkdir -p config
export GDRIVE_CREDS_DIR=./config
mv ~/Downloads/client_secret_xxx.json ./config/gcp-oauth.keys.json
python gdrive_mcp_server.py auth

# 2. 构建镜像
docker build -t gdrive-mcp-server .

# 3. 运行容器
docker run -d \
  --name gdrive-mcp \
  -p 8000:8000 \
  -v $(pwd)/config:/config:ro \
  gdrive-mcp-server

# 或使用 docker-compose
docker-compose up -d
```

### 验证服务

```bash
# 健康检查
curl http://localhost:8000/health

# 测试 MCP 调用
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

## 部署到 AWS EC2

### 1. 本地准备凭证

```bash
# 本地完成 OAuth 认证
python gdrive_mcp_server.py auth

# 将凭证传到 EC2
scp -r ./config ubuntu@your-ec2-ip:~/gdrive-mcp/config
```

### 2. EC2 上部署

```bash
# SSH 到 EC2
ssh ubuntu@your-ec2-ip

# 克隆/上传代码
cd ~/gdrive-mcp

# 使用 Docker 部署
docker-compose up -d

# 查看日志
docker-compose logs -f
```

### 3. 配置 HTTPS（可选但推荐）

使用 nginx + Let's Encrypt：

```bash
# 安装 nginx 和 certbot
sudo apt install nginx certbot python3-certbot-nginx

# 配置 nginx
sudo nano /etc/nginx/sites-available/mcp

# 内容：
server {
    listen 80;
    server_name your-domain.com;
    
    location / {
        proxy_pass http://localhost:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
    }
}

# 启用站点
sudo ln -s /etc/nginx/sites-available/mcp /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx

# 获取 SSL 证书
sudo certbot --nginx -d your-domain.com
```

## 配置 MCP 客户端

### Claude Desktop（stdio 模式）

编辑 `~/Library/Application Support/Claude/claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "gdrive": {
      "command": "python",
      "args": ["/path/to/gdrive_mcp_server.py"],
      "env": {
        "GDRIVE_CREDS_DIR": "/path/to/.config/gdrive-mcp"
      }
    }
  }
}
```

### Claude Desktop（HTTP 模式）

```json
{
  "mcpServers": {
    "gdrive": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

### 远程服务器

```json
{
  "mcpServers": {
    "gdrive": {
      "url": "https://your-domain.com/mcp"
    }
  }
}
```

## 命令行参数

```bash
# 查看帮助
python gdrive_mcp_server.py --help

# 认证
python gdrive_mcp_server.py auth

# stdio 模式（默认）
python gdrive_mcp_server.py
python gdrive_mcp_server.py --transport stdio

# HTTP 模式
python gdrive_mcp_server.py --transport http
python gdrive_mcp_server.py --transport http --host 0.0.0.0 --port 8000
```

## API 端点（HTTP 模式）

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/mcp` | POST | MCP JSON-RPC 端点 |
| `/sse` | GET | SSE 事件流（可选） |

## 环境变量

| 变量 | 说明 | 默认值 |
|-----|------|-------|
| `GDRIVE_CREDS_DIR` | 凭证文件目录 | `~/.config/gdrive-mcp` |
| `PORT` | HTTP 端口（Docker） | `8000` |

## 项目结构

```
gdrive-mcp-server/
├── gdrive_mcp_server.py      # 主服务器代码
├── requirements.txt          # Python 依赖
├── Dockerfile               # Docker 镜像定义
├── docker-compose.yml       # Docker Compose 配置
├── .dockerignore            # Docker 忽略文件
├── config/                  # 凭证目录（不提交到 git）
│   ├── gcp-oauth.keys.json  # OAuth 密钥
│   └── credentials.json     # 认证后的凭证
└── README.md
```

## 扩展开发

### 添加新工具

```python
# 在 tools/list 中添加
{
    "name": "create_file",
    "description": "Create a new file",
    "inputSchema": {...}
}

# 在 tools/call 中实现
if tool_name == "create_file":
    # 实现逻辑
```

### 添加向量数据库（语义搜索）

```yaml
# docker-compose.yml 中添加
services:
  chroma:
    image: chromadb/chroma:latest
    ports:
      - "8001:8000"
```

## 常见问题

### Q: Docker 容器启动失败

检查凭证文件是否正确挂载：
```bash
docker exec gdrive-mcp ls -la /config
```

### Q: 认证报错 "redirect_uri_mismatch"

确保 Google Cloud OAuth 设置中添加了正确的重定向 URI。

### Q: HTTP 模式连接被拒绝

- 检查防火墙/安全组是否开放端口
- 确认容器正在运行：`docker ps`
- 查看日志：`docker logs gdrive-mcp`

## License

MIT
