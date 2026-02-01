#!/usr/bin/env python3
"""
Google Drive MCP Server - Python 版本

基于官方 TypeScript 版本重写，支持：
- 列出 Google Drive 文件（Resources）
- 读取文件内容（Resources）
- 搜索文件（Tools）

传输模式：
- stdio: 本地使用，Claude Desktop 直接调用
- http: Docker/远程部署，支持多客户端访问

后续可扩展：
- 创建文件
- 语义搜索
- Agent 集成
"""

import argparse
import asyncio
import base64
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    Resource,
    Tool,
    TextContent,
    BlobContent,
    CallToolResult,
    ReadResourceResult,
    ListResourcesResult,
    ListToolsResult,
)

# 配置日志 - 注意：stdio 模式下必须输出到 stderr
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("gdrive-mcp")

# Google Drive API 配置
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# 凭证文件路径（可通过环境变量配置）
CREDENTIALS_DIR = Path(os.getenv("GDRIVE_CREDS_DIR", Path.home() / ".config" / "gdrive-mcp"))
OAUTH_KEYS_FILE = CREDENTIALS_DIR / "gcp-oauth.keys.json"
CREDENTIALS_FILE = CREDENTIALS_DIR / "credentials.json"


class GoogleDriveClient:
    """Google Drive API 客户端封装"""

    def __init__(self):
        self.service = None

    def authenticate(self) -> bool:
        """加载或刷新凭证"""
        creds = None

        # 尝试加载已保存的凭证
        if CREDENTIALS_FILE.exists():
            try:
                with open(CREDENTIALS_FILE, "r") as f:
                    creds_data = json.load(f)
                creds = Credentials.from_authorized_user_info(creds_data, SCOPES)
                logger.info("已加载保存的凭证")
            except Exception as e:
                logger.warning(f"加载凭证失败: {e}")

        # 如果凭证无效，需要重新认证
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    from google.auth.transport.requests import Request
                    creds.refresh(Request())
                    logger.info("凭证已刷新")
                except Exception as e:
                    logger.warning(f"刷新凭证失败: {e}")
                    creds = None

            if not creds:
                logger.error("凭证无效，请先运行 'python gdrive_mcp_server.py auth' 进行认证")
                return False

            # 保存刷新后的凭证
            self._save_credentials(creds)

        self.service = build("drive", "v3", credentials=creds)
        logger.info("Google Drive 客户端初始化成功")
        return True

    def run_auth_flow(self) -> bool:
        """运行 OAuth 认证流程"""
        if not OAUTH_KEYS_FILE.exists():
            logger.error(f"OAuth 密钥文件不存在: {OAUTH_KEYS_FILE}")
            logger.error("请从 Google Cloud Console 下载 OAuth 2.0 凭证并保存到该路径")
            return False

        try:
            # 确保目录存在
            CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)

            flow = InstalledAppFlow.from_client_secrets_file(
                str(OAUTH_KEYS_FILE), SCOPES
            )
            creds = flow.run_local_server(port=0)
            self._save_credentials(creds)
            logger.info("认证成功！凭证已保存")
            return True
        except Exception as e:
            logger.error(f"认证失败: {e}")
            return False

    def _save_credentials(self, creds: Credentials):
        """保存凭证到文件"""
        CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
        with open(CREDENTIALS_FILE, "w") as f:
            f.write(creds.to_json())
        logger.info(f"凭证已保存到: {CREDENTIALS_FILE}")

    async def list_files(self, page_token: str | None = None, page_size: int = 10) -> dict:
        """列出文件"""
        try:
            params = {
                "pageSize": page_size,
                "fields": "nextPageToken, files(id, name, mimeType)",
            }
            if page_token:
                params["pageToken"] = page_token

            result = self.service.files().list(**params).execute()
            return result
        except HttpError as e:
            logger.error(f"列出文件失败: {e}")
            raise

    async def get_file_metadata(self, file_id: str) -> dict:
        """获取文件元数据"""
        try:
            return self.service.files().get(
                fileId=file_id, fields="mimeType, name"
            ).execute()
        except HttpError as e:
            logger.error(f"获取文件元数据失败: {e}")
            raise

    async def read_file(self, file_id: str) -> tuple[str, str | bytes]:
        """
        读取文件内容

        Returns:
            tuple: (mime_type, content)
        """
        try:
            # 获取文件元数据
            metadata = await self.get_file_metadata(file_id)
            mime_type = metadata.get("mimeType", "application/octet-stream")

            # Google Workspace 文件需要导出
            if mime_type.startswith("application/vnd.google-apps"):
                return await self._export_google_file(file_id, mime_type)

            # 普通文件直接下载
            return await self._download_file(file_id, mime_type)

        except HttpError as e:
            logger.error(f"读取文件失败: {e}")
            raise

    async def _export_google_file(self, file_id: str, mime_type: str) -> tuple[str, str]:
        """导出 Google Workspace 文件"""
        # 根据文件类型选择导出格式
        export_mime_map = {
            "application/vnd.google-apps.document": "text/markdown",
            "application/vnd.google-apps.spreadsheet": "text/csv",
            "application/vnd.google-apps.presentation": "text/plain",
            "application/vnd.google-apps.drawing": "image/png",
        }
        export_mime = export_mime_map.get(mime_type, "text/plain")

        result = self.service.files().export(
            fileId=file_id, mimeType=export_mime
        ).execute()

        # 结果可能是 bytes 或 str
        if isinstance(result, bytes):
            result = result.decode("utf-8")

        return export_mime, result

    async def _download_file(self, file_id: str, mime_type: str) -> tuple[str, str | bytes]:
        """下载普通文件"""
        result = self.service.files().get_media(fileId=file_id).execute()

        # 文本文件返回字符串
        if mime_type.startswith("text/") or mime_type == "application/json":
            if isinstance(result, bytes):
                return mime_type, result.decode("utf-8")
            return mime_type, result

        # 二进制文件返回 base64
        if isinstance(result, bytes):
            return mime_type, base64.b64encode(result).decode("utf-8")
        return mime_type, result

    async def search_files(self, query: str, page_size: int = 10) -> list[dict]:
        """搜索文件"""
        try:
            # 转义查询字符串
            escaped_query = query.replace("\\", "\\\\").replace("'", "\\'")
            formatted_query = f"fullText contains '{escaped_query}'"

            result = self.service.files().list(
                q=formatted_query,
                pageSize=page_size,
                fields="files(id, name, mimeType, modifiedTime, size)",
            ).execute()

            return result.get("files", [])
        except HttpError as e:
            logger.error(f"搜索文件失败: {e}")
            raise


# 全局客户端实例
drive_client = GoogleDriveClient()


def create_server() -> Server:
    """创建 MCP Server"""
    server = Server("gdrive-mcp-server")

    # ==================== Resources ====================

    @server.list_resources()
    async def list_resources(cursor: str | None = None) -> ListResourcesResult:
        """列出 Google Drive 文件作为资源"""
        result = await drive_client.list_files(page_token=cursor)
        files = result.get("files", [])

        resources = [
            Resource(
                uri=f"gdrive:///{file['id']}",
                name=file["name"],
                mimeType=file.get("mimeType"),
            )
            for file in files
        ]

        return ListResourcesResult(
            resources=resources,
            nextCursor=result.get("nextPageToken"),
        )

    @server.read_resource()
    async def read_resource(uri: str) -> ReadResourceResult:
        """读取文件内容"""
        # 解析 URI: gdrive:///file_id
        file_id = uri.replace("gdrive:///", "")

        mime_type, content = await drive_client.read_file(file_id)

        # 根据内容类型返回不同格式
        if mime_type.startswith("text/") or mime_type == "application/json":
            return ReadResourceResult(
                contents=[
                    TextContent(
                        type="text",
                        text=content if isinstance(content, str) else str(content),
                    )
                ]
            )
        else:
            return ReadResourceResult(
                contents=[
                    BlobContent(
                        type="blob",
                        blob=content if isinstance(content, str) else base64.b64encode(content).decode(),
                    )
                ]
            )

    # ==================== Tools ====================

    @server.list_tools()
    async def list_tools() -> ListToolsResult:
        """列出可用工具"""
        return ListToolsResult(
            tools=[
                Tool(
                    name="search",
                    description="Search for files in Google Drive",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Search query",
                            },
                        },
                        "required": ["query"],
                    },
                ),
                Tool(
                    name="read_file",
                    description="Read the content of a file by its ID",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "file_id": {
                                "type": "string",
                                "description": "Google Drive file ID",
                            },
                        },
                        "required": ["file_id"],
                    },
                ),
            ]
        )

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
        """执行工具调用"""
        if name == "search":
            query = arguments.get("query", "")
            files = await drive_client.search_files(query)

            if not files:
                return CallToolResult(
                    content=[TextContent(type="text", text="No files found.")]
                )

            file_list = "\n".join(
                f"- {f['name']} ({f['mimeType']}) [ID: {f['id']}]"
                for f in files
            )
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=f"Found {len(files)} files:\n{file_list}",
                    )
                ]
            )

        elif name == "read_file":
            file_id = arguments.get("file_id", "")
            if not file_id:
                return CallToolResult(
                    content=[TextContent(type="text", text="Error: file_id is required")],
                    isError=True,
                )

            try:
                mime_type, content = await drive_client.read_file(file_id)
                return CallToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=content if isinstance(content, str) else f"[Binary content, {len(content)} bytes]",
                        )
                    ]
                )
            except Exception as e:
                return CallToolResult(
                    content=[TextContent(type="text", text=f"Error reading file: {e}")],
                    isError=True,
                )

        else:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Unknown tool: {name}")],
                isError=True,
            )

    return server


async def run_server_stdio():
    """运行 MCP Server (stdio 模式)"""
    if not drive_client.authenticate():
        logger.error("认证失败，服务器无法启动")
        sys.exit(1)

    server = create_server()
    logger.info("启动 Google Drive MCP Server (stdio 模式)...")

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


async def run_server_http(host: str, port: int):
    """运行 MCP Server (HTTP/SSE 模式)"""
    if not drive_client.authenticate():
        logger.error("认证失败，服务器无法启动")
        sys.exit(1)

    # 动态导入 HTTP 相关依赖
    try:
        from starlette.applications import Starlette
        from starlette.routing import Route
        from starlette.responses import JSONResponse, Response
        from starlette.requests import Request
        from sse_starlette.sse import EventSourceResponse
        import uvicorn
    except ImportError:
        logger.error("HTTP 模式需要额外依赖，请安装: pip install uvicorn starlette sse-starlette")
        sys.exit(1)

    server = create_server()

    # 存储 SSE 连接
    connections: dict[str, asyncio.Queue] = {}

    async def handle_sse(request: Request):
        """处理 SSE 连接"""
        client_id = request.query_params.get("client_id", "default")
        queue = asyncio.Queue()
        connections[client_id] = queue

        async def event_generator():
            try:
                while True:
                    data = await queue.get()
                    yield {"event": "message", "data": json.dumps(data)}
            except asyncio.CancelledError:
                pass
            finally:
                connections.pop(client_id, None)

        return EventSourceResponse(event_generator())

    async def handle_message(request: Request):
        """处理 MCP 消息"""
        try:
            body = await request.json()
            client_id = request.query_params.get("client_id", "default")

            # 简单的请求路由
            method = body.get("method", "")
            params = body.get("params", {})
            request_id = body.get("id")

            result = None
            error = None

            try:
                if method == "initialize":
                    result = {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {
                            "resources": {"listChanged": False},
                            "tools": {"listChanged": False},
                        },
                        "serverInfo": {
                            "name": "gdrive-mcp-server",
                            "version": "1.0.0",
                        },
                    }

                elif method == "resources/list":
                    cursor = params.get("cursor")
                    list_result = await drive_client.list_files(page_token=cursor)
                    files = list_result.get("files", [])
                    result = {
                        "resources": [
                            {
                                "uri": f"gdrive:///{f['id']}",
                                "name": f["name"],
                                "mimeType": f.get("mimeType"),
                            }
                            for f in files
                        ],
                        "nextCursor": list_result.get("nextPageToken"),
                    }

                elif method == "resources/read":
                    uri = params.get("uri", "")
                    file_id = uri.replace("gdrive:///", "")
                    mime_type, content = await drive_client.read_file(file_id)

                    if mime_type.startswith("text/") or mime_type == "application/json":
                        result = {
                            "contents": [
                                {
                                    "uri": uri,
                                    "mimeType": mime_type,
                                    "text": content if isinstance(content, str) else str(content),
                                }
                            ]
                        }
                    else:
                        result = {
                            "contents": [
                                {
                                    "uri": uri,
                                    "mimeType": mime_type,
                                    "blob": content if isinstance(content, str) else base64.b64encode(content).decode(),
                                }
                            ]
                        }

                elif method == "tools/list":
                    result = {
                        "tools": [
                            {
                                "name": "search",
                                "description": "Search for files in Google Drive",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "query": {
                                            "type": "string",
                                            "description": "Search query",
                                        },
                                    },
                                    "required": ["query"],
                                },
                            },
                            {
                                "name": "read_file",
                                "description": "Read the content of a file by its ID",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "file_id": {
                                            "type": "string",
                                            "description": "Google Drive file ID",
                                        },
                                    },
                                    "required": ["file_id"],
                                },
                            },
                        ]
                    }

                elif method == "tools/call":
                    tool_name = params.get("name", "")
                    arguments = params.get("arguments", {})

                    if tool_name == "search":
                        query = arguments.get("query", "")
                        files = await drive_client.search_files(query)
                        if not files:
                            result = {"content": [{"type": "text", "text": "No files found."}]}
                        else:
                            file_list = "\n".join(
                                f"- {f['name']} ({f['mimeType']}) [ID: {f['id']}]"
                                for f in files
                            )
                            result = {
                                "content": [
                                    {"type": "text", "text": f"Found {len(files)} files:\n{file_list}"}
                                ]
                            }

                    elif tool_name == "read_file":
                        file_id = arguments.get("file_id", "")
                        if not file_id:
                            result = {
                                "content": [{"type": "text", "text": "Error: file_id is required"}],
                                "isError": True,
                            }
                        else:
                            mime_type, content = await drive_client.read_file(file_id)
                            result = {
                                "content": [
                                    {
                                        "type": "text",
                                        "text": content if isinstance(content, str) else f"[Binary content]",
                                    }
                                ]
                            }

                    else:
                        result = {
                            "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                            "isError": True,
                        }

                elif method == "notifications/initialized":
                    # 客户端初始化完成通知，不需要响应
                    return Response(status_code=204)

                else:
                    error = {"code": -32601, "message": f"Method not found: {method}"}

            except Exception as e:
                logger.exception(f"处理请求时出错: {e}")
                error = {"code": -32603, "message": str(e)}

            # 构建响应
            response = {"jsonrpc": "2.0", "id": request_id}
            if error:
                response["error"] = error
            else:
                response["result"] = result

            return JSONResponse(response)

        except Exception as e:
            logger.exception(f"解析请求失败: {e}")
            return JSONResponse(
                {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}},
                status_code=400,
            )

    async def health_check(request: Request):
        """健康检查端点"""
        return JSONResponse({"status": "healthy", "server": "gdrive-mcp-server"})

    # 创建 Starlette 应用
    app = Starlette(
        debug=False,
        routes=[
            Route("/", health_check, methods=["GET"]),
            Route("/health", health_check, methods=["GET"]),
            Route("/sse", handle_sse, methods=["GET"]),
            Route("/mcp", handle_message, methods=["POST"]),
            Route("/message", handle_message, methods=["POST"]),  # 兼容别名
        ],
    )

    logger.info(f"启动 Google Drive MCP Server (HTTP 模式) - http://{host}:{port}")
    logger.info(f"  - 健康检查: http://{host}:{port}/health")
    logger.info(f"  - MCP 端点: http://{host}:{port}/mcp")
    logger.info(f"  - SSE 端点: http://{host}:{port}/sse")

    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


def main():
    """主入口"""
    parser = argparse.ArgumentParser(description="Google Drive MCP Server")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["auth", "serve"],
        default="serve",
        help="运行命令: auth (认证) 或 serve (启动服务器)",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="传输模式: stdio (本地) 或 http (远程)",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="HTTP 模式监听地址 (默认: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="HTTP 模式监听端口 (默认: 8000)",
    )

    args = parser.parse_args()

    if args.command == "auth":
        # 认证模式
        print("启动 OAuth 认证流程...")
        print(f"OAuth 密钥文件路径: {OAUTH_KEYS_FILE}")
        print(f"凭证保存路径: {CREDENTIALS_FILE}")

        if drive_client.run_auth_flow():
            print("\n✅ 认证成功！现在可以运行服务器了。")
            print(f"  stdio 模式: python {sys.argv[0]}")
            print(f"  HTTP 模式:  python {sys.argv[0]} --transport http")
        else:
            print("\n❌ 认证失败，请检查 OAuth 密钥文件。")
            sys.exit(1)
    else:
        # 服务器模式
        if args.transport == "stdio":
            asyncio.run(run_server_stdio())
        else:
            asyncio.run(run_server_http(args.host, args.port))


if __name__ == "__main__":
    main()
