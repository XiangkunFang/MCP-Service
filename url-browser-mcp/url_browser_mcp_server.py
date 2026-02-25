#!/usr/bin/env python3
"""
URL Browser MCP Server

为 AI Agent 提供网页浏览能力，支持：
- 浏览任意 URL，提取页面内容（Tools）
- 将 HTML 转为 Markdown，便于 LLM 理解
- 提取页面中的所有链接

传输模式：
- stdio: 本地使用，Claude Desktop 直接调用
- http: Docker/远程部署，支持多客户端访问

核心场景：
- Agent 读取用户提供的 URL 内容
- Agent 对网页内容进行总结、回答问题
- Agent 提取页面链接进行深度浏览
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify as md

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    Tool,
    TextContent,
    CallToolResult,
    ListToolsResult,
)

# 配置日志 - 注意：stdio 模式下必须输出到 stderr
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("url-browser-mcp")

# 默认配置
DEFAULT_TIMEOUT = int(os.getenv("URL_BROWSER_TIMEOUT", "30"))
DEFAULT_MAX_CONTENT_LENGTH = int(os.getenv("URL_BROWSER_MAX_LENGTH", "500000"))
USER_AGENT = os.getenv(
    "URL_BROWSER_USER_AGENT",
    "Mozilla/5.0 (compatible; URLBrowserMCP/1.0; +https://github.com/your-repo/url-browser-mcp)",
)


class URLBrowserClient:
    """URL 浏览器客户端封装"""

    def __init__(self):
        self.timeout = DEFAULT_TIMEOUT
        self.max_content_length = DEFAULT_MAX_CONTENT_LENGTH
        self.client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """获取或创建 HTTP 客户端"""
        if self.client is None or self.client.is_closed:
            self.client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                follow_redirects=True,
                max_redirects=10,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    "Accept-Encoding": "gzip, deflate",
                },
            )
        return self.client

    async def close(self):
        """关闭 HTTP 客户端"""
        if self.client and not self.client.is_closed:
            await self.client.aclose()

    async def fetch_url(self, url: str) -> dict:
        """
        获取 URL 内容

        Returns:
            dict: {
                "url": 最终 URL（可能经过重定向），
                "status_code": HTTP 状态码,
                "content_type": 内容类型,
                "content": 内容文本,
                "title": 页面标题（仅 HTML）,
                "encoding": 字符编码,
            }
        """
        client = await self._get_client()

        try:
            response = await client.get(url)
            response.raise_for_status()

            content_type = response.headers.get("content-type", "")
            final_url = str(response.url)

            # 判断内容类型
            if "text/html" in content_type or "application/xhtml" in content_type:
                return self._process_html(response, final_url)
            elif "application/json" in content_type:
                return self._process_json(response, final_url)
            elif "text/" in content_type:
                return self._process_text(response, final_url)
            elif "application/xml" in content_type or "text/xml" in content_type:
                return self._process_text(response, final_url)
            else:
                return {
                    "url": final_url,
                    "status_code": response.status_code,
                    "content_type": content_type,
                    "content": f"[不支持的内容类型: {content_type}，内容大小: {len(response.content)} bytes]",
                    "title": "",
                    "encoding": response.encoding or "unknown",
                }

        except httpx.TimeoutException:
            raise Exception(f"请求超时（{self.timeout}秒）: {url}")
        except httpx.TooManyRedirects:
            raise Exception(f"重定向次数过多: {url}")
        except httpx.HTTPStatusError as e:
            raise Exception(f"HTTP 错误 {e.response.status_code}: {url}")
        except httpx.RequestError as e:
            raise Exception(f"请求失败: {url} - {type(e).__name__}: {e}")

    def _process_html(self, response: httpx.Response, final_url: str) -> dict:
        """处理 HTML 内容"""
        html = response.text

        # 截断过长内容
        if len(html) > self.max_content_length:
            html = html[: self.max_content_length]

        soup = BeautifulSoup(html, "html.parser")

        # 提取标题
        title = ""
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True)

        # 移除不需要的标签
        for tag in soup.find_all(["script", "style", "nav", "footer", "header", "noscript", "iframe"]):
            tag.decompose()

        # 转换为 Markdown
        markdown_content = md(
            str(soup),
            heading_style="ATX",
            strip=["img"],
            convert=["p", "h1", "h2", "h3", "h4", "h5", "h6", "a", "ul", "ol", "li",
                      "table", "thead", "tbody", "tr", "th", "td", "blockquote",
                      "pre", "code", "strong", "em", "br", "hr"],
        )

        # 清理多余空行
        lines = markdown_content.split("\n")
        cleaned_lines = []
        prev_empty = False
        for line in lines:
            stripped = line.strip()
            if not stripped:
                if not prev_empty:
                    cleaned_lines.append("")
                prev_empty = True
            else:
                cleaned_lines.append(stripped)
                prev_empty = False
        markdown_content = "\n".join(cleaned_lines).strip()

        return {
            "url": final_url,
            "status_code": response.status_code,
            "content_type": response.headers.get("content-type", ""),
            "content": markdown_content,
            "title": title,
            "encoding": response.encoding or "utf-8",
        }

    def _process_json(self, response: httpx.Response, final_url: str) -> dict:
        """处理 JSON 内容"""
        text = response.text
        # 尝试格式化 JSON
        try:
            parsed = json.loads(text)
            formatted = json.dumps(parsed, indent=2, ensure_ascii=False)
            if len(formatted) > self.max_content_length:
                formatted = formatted[: self.max_content_length] + "\n... [内容已截断]"
            content = formatted
        except json.JSONDecodeError:
            content = text[: self.max_content_length]

        return {
            "url": final_url,
            "status_code": response.status_code,
            "content_type": response.headers.get("content-type", ""),
            "content": content,
            "title": "",
            "encoding": response.encoding or "utf-8",
        }

    def _process_text(self, response: httpx.Response, final_url: str) -> dict:
        """处理纯文本内容"""
        text = response.text
        if len(text) > self.max_content_length:
            text = text[: self.max_content_length] + "\n... [内容已截断]"

        return {
            "url": final_url,
            "status_code": response.status_code,
            "content_type": response.headers.get("content-type", ""),
            "content": text,
            "title": "",
            "encoding": response.encoding or "utf-8",
        }

    async def extract_links(self, url: str) -> list[dict]:
        """
        提取页面中的所有链接

        Returns:
            list[dict]: [{"text": 链接文本, "url": 完整 URL}, ...]
        """
        client = await self._get_client()

        response = await client.get(url)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        links = []
        seen_urls = set()

        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            text = a_tag.get_text(strip=True) or href

            # 转换为绝对 URL
            absolute_url = urljoin(str(response.url), href)

            # 跳过非 HTTP 链接和重复链接
            parsed = urlparse(absolute_url)
            if parsed.scheme not in ("http", "https"):
                continue
            if absolute_url in seen_urls:
                continue

            seen_urls.add(absolute_url)
            links.append({"text": text, "url": absolute_url})

        return links


# 全局客户端实例
browser_client = URLBrowserClient()


def create_server() -> Server:
    """创建 MCP Server"""
    server = Server("url-browser-mcp-server")

    # ==================== Tools ====================

    @server.list_tools()
    async def list_tools() -> ListToolsResult:
        """列出可用工具"""
        return ListToolsResult(
            tools=[
                Tool(
                    name="browse_url",
                    description=(
                        "Browse a URL and return its content as readable Markdown text. "
                        "Supports HTML pages, JSON APIs, and plain text. "
                        "HTML is automatically converted to clean Markdown for easy reading. "
                        "Use this tool to read and understand web page content."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "url": {
                                "type": "string",
                                "description": "The URL to browse (must start with http:// or https://)",
                            },
                        },
                        "required": ["url"],
                    },
                ),
                Tool(
                    name="extract_links",
                    description=(
                        "Extract all hyperlinks from a web page. "
                        "Returns a list of links with their display text and full URLs. "
                        "Useful for discovering related pages and navigating websites."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "url": {
                                "type": "string",
                                "description": "The URL to extract links from (must start with http:// or https://)",
                            },
                        },
                        "required": ["url"],
                    },
                ),
            ]
        )

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
        """执行工具调用"""
        if name == "browse_url":
            url = arguments.get("url", "")
            if not url:
                return CallToolResult(
                    content=[TextContent(type="text", text="Error: url is required")],
                    isError=True,
                )

            # 验证 URL 格式
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https"):
                return CallToolResult(
                    content=[TextContent(type="text", text="Error: URL must start with http:// or https://")],
                    isError=True,
                )

            try:
                result = await browser_client.fetch_url(url)

                # 构建输出
                output_parts = []
                if result["title"]:
                    output_parts.append(f"# {result['title']}")
                    output_parts.append("")
                output_parts.append(f"**URL:** {result['url']}")
                output_parts.append(f"**Status:** {result['status_code']}")
                output_parts.append(f"**Content-Type:** {result['content_type']}")
                output_parts.append("")
                output_parts.append("---")
                output_parts.append("")
                output_parts.append(result["content"])

                return CallToolResult(
                    content=[TextContent(type="text", text="\n".join(output_parts))]
                )
            except Exception as e:
                return CallToolResult(
                    content=[TextContent(type="text", text=f"Error browsing URL: {e}")],
                    isError=True,
                )

        elif name == "extract_links":
            url = arguments.get("url", "")
            if not url:
                return CallToolResult(
                    content=[TextContent(type="text", text="Error: url is required")],
                    isError=True,
                )

            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https"):
                return CallToolResult(
                    content=[TextContent(type="text", text="Error: URL must start with http:// or https://")],
                    isError=True,
                )

            try:
                links = await browser_client.extract_links(url)

                if not links:
                    return CallToolResult(
                        content=[TextContent(type="text", text="No links found on this page.")]
                    )

                link_list = "\n".join(
                    f"- [{link['text']}]({link['url']})"
                    for link in links
                )
                return CallToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=f"Found {len(links)} links:\n\n{link_list}",
                        )
                    ]
                )
            except Exception as e:
                return CallToolResult(
                    content=[TextContent(type="text", text=f"Error extracting links: {e}")],
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
    server = create_server()
    logger.info("启动 URL Browser MCP Server (stdio 模式)...")

    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    finally:
        await browser_client.close()


async def run_server_http(host: str, port: int):
    """运行 MCP Server (HTTP/SSE 模式)"""
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
                            "tools": {"listChanged": False},
                        },
                        "serverInfo": {
                            "name": "url-browser-mcp-server",
                            "version": "1.0.0",
                        },
                    }

                elif method == "tools/list":
                    result = {
                        "tools": [
                            {
                                "name": "browse_url",
                                "description": (
                                    "Browse a URL and return its content as readable Markdown text. "
                                    "Supports HTML pages, JSON APIs, and plain text. "
                                    "HTML is automatically converted to clean Markdown for easy reading. "
                                    "Use this tool to read and understand web page content."
                                ),
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "url": {
                                            "type": "string",
                                            "description": "The URL to browse (must start with http:// or https://)",
                                        },
                                    },
                                    "required": ["url"],
                                },
                            },
                            {
                                "name": "extract_links",
                                "description": (
                                    "Extract all hyperlinks from a web page. "
                                    "Returns a list of links with their display text and full URLs. "
                                    "Useful for discovering related pages and navigating websites."
                                ),
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "url": {
                                            "type": "string",
                                            "description": "The URL to extract links from (must start with http:// or https://)",
                                        },
                                    },
                                    "required": ["url"],
                                },
                            },
                        ]
                    }

                elif method == "tools/call":
                    tool_name = params.get("name", "")
                    arguments = params.get("arguments", {})

                    if tool_name == "browse_url":
                        url = arguments.get("url", "")
                        if not url:
                            result = {
                                "content": [{"type": "text", "text": "Error: url is required"}],
                                "isError": True,
                            }
                        else:
                            parsed = urlparse(url)
                            if parsed.scheme not in ("http", "https"):
                                result = {
                                    "content": [{"type": "text", "text": "Error: URL must start with http:// or https://"}],
                                    "isError": True,
                                }
                            else:
                                fetch_result = await browser_client.fetch_url(url)
                                output_parts = []
                                if fetch_result["title"]:
                                    output_parts.append(f"# {fetch_result['title']}")
                                    output_parts.append("")
                                output_parts.append(f"**URL:** {fetch_result['url']}")
                                output_parts.append(f"**Status:** {fetch_result['status_code']}")
                                output_parts.append(f"**Content-Type:** {fetch_result['content_type']}")
                                output_parts.append("")
                                output_parts.append("---")
                                output_parts.append("")
                                output_parts.append(fetch_result["content"])
                                result = {
                                    "content": [{"type": "text", "text": "\n".join(output_parts)}]
                                }

                    elif tool_name == "extract_links":
                        url = arguments.get("url", "")
                        if not url:
                            result = {
                                "content": [{"type": "text", "text": "Error: url is required"}],
                                "isError": True,
                            }
                        else:
                            links = await browser_client.extract_links(url)
                            if not links:
                                result = {"content": [{"type": "text", "text": "No links found on this page."}]}
                            else:
                                link_list = "\n".join(
                                    f"- [{link['text']}]({link['url']})"
                                    for link in links
                                )
                                result = {
                                    "content": [
                                        {"type": "text", "text": f"Found {len(links)} links:\n\n{link_list}"}
                                    ]
                                }

                    else:
                        result = {
                            "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                            "isError": True,
                        }

                elif method == "notifications/initialized":
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
        return JSONResponse({"status": "healthy", "server": "url-browser-mcp-server"})

    # 创建 Starlette 应用
    app = Starlette(
        debug=False,
        routes=[
            Route("/health", health_check, methods=["GET"]),
            Route("/sse", handle_sse, methods=["GET"]),
            Route("/mcp", handle_message, methods=["POST"]),
            Route("/message", handle_message, methods=["POST"]),
            Route("/", handle_message, methods=["POST"]),
            Route("/", health_check, methods=["GET"]),
        ],
    )

    logger.info(f"启动 URL Browser MCP Server (HTTP 模式) - http://{host}:{port}")
    logger.info(f"  - 健康检查: http://{host}:{port}/health")
    logger.info(f"  - MCP 端点: http://{host}:{port}/mcp")
    logger.info(f"  - SSE 端点: http://{host}:{port}/sse")

    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    uvi_server = uvicorn.Server(config)
    await uvi_server.serve()


def main():
    """主入口"""
    parser = argparse.ArgumentParser(description="URL Browser MCP Server")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["serve"],
        default="serve",
        help="运行命令: serve (启动服务器)",
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

    if args.transport == "stdio":
        asyncio.run(run_server_stdio())
    else:
        asyncio.run(run_server_http(args.host, args.port))


if __name__ == "__main__":
    main()
