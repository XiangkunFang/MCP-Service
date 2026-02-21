#!/usr/bin/env python3
"""
Google Calendar MCP Server - Python

支持读取与修改 Google Calendar：
- 列出日历、列出/查询事件、获取单个事件
- 创建、更新、删除事件

传输模式：stdio（本地）、http（远程/Docker）
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

# 优先从本目录 .env 加载环境变量（便于本地指定 GCAL_CREDS_DIR）
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, CallToolResult, ListToolsResult

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("gcal-mcp")

SCOPES = ["https://www.googleapis.com/auth/calendar"]
_default_creds_dir = Path.home() / ".config" / "gcal-mcp"
_creds_dir_env = os.getenv("GCAL_CREDS_DIR")
CREDENTIALS_DIR = Path(_creds_dir_env).resolve() if _creds_dir_env else _default_creds_dir
OAUTH_KEYS_FILE = CREDENTIALS_DIR / "gcp-oauth.keys.json"
CREDENTIALS_FILE = CREDENTIALS_DIR / "credentials.json"


def _ensure_rfc3339_tz(dt_str: str | None) -> str | None:
    """
    确保日期时间字符串带时区，符合 Google Calendar API 要求的 RFC3339。
    若无时区（无 Z 或 ±HH:MM），则按 UTC 补全（追加 Z）。
    """
    if not dt_str or not (s := dt_str.strip()):
        return None
    # 已有 Z 或 ±HH:MM 时区
    if s.endswith("Z") or re.search(r"[+-]\d{2}:?\d{2}$", s):
        return s
    # 含 T 但无时区：补 Z
    if "T" in s:
        return s + "Z"
    # 仅日期 YYYY-MM-DD：视为当日 00:00 UTC
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s + "T00:00:00Z"
    return s


def _parse_start_end(start_str: str | None, end_str: str | None) -> tuple[dict, dict]:
    """
    将 ISO 8601 日期/日期时间字符串转为 Calendar API 的 start/end 结构。
    - 仅日期 (YYYY-MM-DD)：全天事件，使用 "date"
    - 含时间 (YYYY-MM-DDTHH:mm:ss...)：使用 "dateTime" 和 "timeZone"（从偏移解析或默认 UTC）
    """
    def one(s: str) -> dict:
        s = (s or "").strip()
        if not s:
            return {}
        if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
            return {"date": s}
        # dateTime: 尽量带 timeZone
        if "T" in s:
            if "+" in s or s.endswith("Z"):
                # 有偏移或 Z -> 用 dateTime + timeZone 表示
                tz = "UTC"
                if "+" in s:
                    m = re.search(r"\+(\d{2}):?(\d{2})$", s)
                    if m:
                        tz = f"UTC+{m.group(1)}:{m.group(2)}"
                return {"dateTime": s.replace("Z", "+00:00"), "timeZone": tz}
            return {"dateTime": s, "timeZone": "UTC"}
        return {"date": s}

    return one(start_str), one(end_str)


class GoogleCalendarClient:
    """Google Calendar API 客户端封装"""

    def __init__(self):
        self.service = None

    def authenticate(self) -> bool:
        """加载或刷新凭证"""
        creds = None
        if CREDENTIALS_FILE.exists():
            try:
                with open(CREDENTIALS_FILE, "r") as f:
                    creds_data = json.load(f)
                creds = Credentials.from_authorized_user_info(creds_data, SCOPES)
                logger.info("已加载保存的凭证")
            except Exception as e:
                logger.warning(f"加载凭证失败: {e}")

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
                logger.error("凭证无效，请先运行 'python gcal_mcp_server.py auth' 进行认证")
                return False
            self._save_credentials(creds)

        self.service = build("calendar", "v3", credentials=creds)
        logger.info("Google Calendar 客户端初始化成功")
        return True

    def run_auth_flow(self) -> bool:
        """运行 OAuth 认证流程"""
        if not OAUTH_KEYS_FILE.exists():
            logger.error(f"OAuth 密钥文件不存在: {OAUTH_KEYS_FILE}")
            return False
        try:
            CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
            flow = InstalledAppFlow.from_client_secrets_file(
                str(OAUTH_KEYS_FILE), SCOPES, redirect_uri="http://localhost"
            )
            auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
            print("\n" + "=" * 60)
            print("Google Calendar MCP - 认证")
            print("=" * 60)
            print("1. 复制下面的 URL 到浏览器打开：")
            print(auth_url)
            print("2. 登录 Google 并授权")
            print("3. 授权后从浏览器地址栏复制完整重定向 URL（以 http://localhost... 开头）")
            print("4. 将完整 URL 粘贴到下面")
            print("=" * 60)
            redirect_url = input("\n请粘贴完整的重定向 URL: ").strip()
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(redirect_url)
            code = parse_qs(parsed.query).get("code", [None])[0]
            if not code:
                logger.error("无法从 URL 中提取授权码")
                return False
            flow.fetch_token(code=code)
            creds = flow.credentials
            self._save_credentials(creds)
            logger.info("认证成功，凭证已保存")
            return True
        except Exception as e:
            logger.error(f"认证失败: {e}")
            return False

    def _save_credentials(self, creds: Credentials) -> None:
        CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
        with open(CREDENTIALS_FILE, "w") as f:
            f.write(creds.to_json())

    async def list_calendars(self) -> list[dict]:
        """列出用户可访问的日历"""
        try:
            result = self.service.calendarList().list().execute()
            return result.get("items", [])
        except HttpError as e:
            logger.error(f"列出日历失败: {e}")
            raise

    async def list_events(
        self,
        calendar_id: str = "primary",
        time_min: str | None = None,
        time_max: str | None = None,
        max_results: int = 50,
        q: str | None = None,
    ) -> list[dict]:
        """列出指定时间范围内的事件"""
        try:
            params = {
                "calendarId": calendar_id,
                "singleEvents": True,
                "orderBy": "startTime",
                "maxResults": min(max_results, 250),
            }
            if time_min:
                params["timeMin"] = _ensure_rfc3339_tz(time_min)
            if time_max:
                params["timeMax"] = _ensure_rfc3339_tz(time_max)
            if q:
                params["q"] = q
            result = self.service.events().list(**params).execute()
            return result.get("items", [])
        except HttpError as e:
            logger.error(f"列出事件失败: {e}")
            raise

    async def get_event(self, calendar_id: str, event_id: str) -> dict:
        """获取单个事件"""
        try:
            return self.service.events().get(
                calendarId=calendar_id, eventId=event_id
            ).execute()
        except HttpError as e:
            logger.error(f"获取事件失败: {e}")
            raise

    async def create_event(
        self,
        calendar_id: str,
        summary: str,
        start: dict,
        end: dict,
        description: str | None = None,
        location: str | None = None,
    ) -> dict:
        """创建事件"""
        try:
            body = {"summary": summary, "start": start, "end": end}
            if description:
                body["description"] = description
            if location:
                body["location"] = location
            return self.service.events().insert(
                calendarId=calendar_id, body=body
            ).execute()
        except HttpError as e:
            logger.error(f"创建事件失败: {e}")
            raise

    async def update_event(
        self,
        calendar_id: str,
        event_id: str,
        summary: str | None = None,
        start: dict | None = None,
        end: dict | None = None,
        description: str | None = None,
        location: str | None = None,
    ) -> dict:
        """更新事件（仅传要改的字段）"""
        try:
            existing = await self.get_event(calendar_id, event_id)
            if summary is not None:
                existing["summary"] = summary
            if start is not None:
                existing["start"] = start
            if end is not None:
                existing["end"] = end
            if description is not None:
                existing["description"] = description
            if location is not None:
                existing["location"] = location
            return self.service.events().update(
                calendarId=calendar_id, eventId=event_id, body=existing
            ).execute()
        except HttpError as e:
            logger.error(f"更新事件失败: {e}")
            raise

    async def delete_event(self, calendar_id: str, event_id: str) -> None:
        """删除事件"""
        try:
            self.service.events().delete(
                calendarId=calendar_id, eventId=event_id
            ).execute()
        except HttpError as e:
            logger.error(f"删除事件失败: {e}")
            raise


gcal_client = GoogleCalendarClient()

# 工具定义（供 list_tools 与 HTTP tools/list 共用）
GCAL_TOOLS_SCHEMA = [
    {
        "name": "list_calendars",
        "description": "List all calendars the user can access, including shared calendars (e.g. from work/school like NYU). Returns each calendar's id and summary; use these ids in list_events to query a specific calendar. Use 'primary' for the user's main calendar.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_events",
        "description": "List events in a calendar within an optional time range. Use time_min/time_max in ISO 8601. Use calendar_id='all' to get events from ALL calendars (primary + shared e.g. NYU, holidays); use a specific id to query one calendar only. Default: primary.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "calendar_id": {"type": "string", "description": "Calendar ID from list_calendars, or 'all' to aggregate events from every calendar (primary + shared). Default: primary.", "default": "primary"},
                "time_min": {"type": "string", "description": "Start of time range (ISO 8601, RFC3339). Must include timezone (e.g. Z or +08:00); if omitted, UTC is assumed."},
                "time_max": {"type": "string", "description": "End of time range (ISO 8601, RFC3339). Must include timezone (e.g. Z or +08:00); if omitted, UTC is assumed."},
                "max_results": {"type": "integer", "description": "Max events to return (default 50).", "default": 50},
                "q": {"type": "string", "description": "Search query for event summary."},
            },
            "required": [],
        },
    },
    {
        "name": "get_event",
        "description": "Get a single event by calendar ID and event ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "calendar_id": {"type": "string", "description": "Calendar ID (default: primary).", "default": "primary"},
                "event_id": {"type": "string", "description": "Event ID."},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "create_event",
        "description": "Create a new event. start/end: ISO date (YYYY-MM-DD) for all-day, or ISO datetime with timezone (e.g. 2025-02-21T14:00:00+08:00).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "calendar_id": {"type": "string", "default": "primary"},
                "summary": {"type": "string", "description": "Event title."},
                "start": {"type": "string", "description": "Start date or datetime (ISO 8601)."},
                "end": {"type": "string", "description": "End date or datetime (ISO 8601)."},
                "description": {"type": "string"},
                "location": {"type": "string"},
            },
            "required": ["summary", "start", "end"],
        },
    },
    {
        "name": "update_event",
        "description": "Update an existing event. Only include fields to change.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "calendar_id": {"type": "string", "default": "primary"},
                "event_id": {"type": "string"},
                "summary": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
                "description": {"type": "string"},
                "location": {"type": "string"},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "delete_event",
        "description": "Delete an event by calendar ID and event ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "calendar_id": {"type": "string", "default": "primary"},
                "event_id": {"type": "string"},
            },
            "required": ["event_id"],
        },
    },
]


async def execute_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
    """执行工具调用（供 stdio call_tool 与 HTTP tools/call 共用）"""
    if name == "list_calendars":
        try:
            calendars = await gcal_client.list_calendars()
            lines = ["日历列表:", ""]
            for c in calendars:
                lines.append(f"- {c.get('summary', 'N/A')} (id: {c.get('id', 'N/A')})")
            return CallToolResult(content=[TextContent(type="text", text="\n".join(lines))])
        except Exception as e:
            return CallToolResult(content=[TextContent(type="text", text=f"列出日历失败: {e}")], isError=True)

    elif name == "list_events":
        try:
            calendar_id = (arguments.get("calendar_id") or "primary").strip()
            time_min = arguments.get("time_min")
            time_max = arguments.get("time_max")
            max_results = arguments.get("max_results", 50)
            q = arguments.get("q")

            if calendar_id.lower() == "all":
                # 聚合所有日历的事件（含主日历与共享日历如 NYU）
                calendars = await gcal_client.list_calendars()
                per_calendar = max(20, (max_results or 50) // max(1, len(calendars)))
                all_lines = ["以下为所有日历在指定时间范围内的事件汇总：", ""]
                total = 0
                for c in calendars:
                    cid = c.get("id") or ""
                    csummary = c.get("summary", "N/A")
                    try:
                        events = await gcal_client.list_events(
                            calendar_id=cid, time_min=time_min, time_max=time_max,
                            max_results=per_calendar, q=q,
                        )
                    except Exception:
                        events = []
                    if events:
                        all_lines.append(f"【{csummary}】(id: {cid}) 共 {len(events)} 条:")
                        for ev in events:
                            start = ev.get("start", {}) or {}
                            start_str = start.get("dateTime") or start.get("date", "?")
                            summary = ev.get("summary", "(无标题)")
                            eid = ev.get("id", "?")
                            all_lines.append(f"  - {summary} | 开始: {start_str} [event_id: {eid}]")
                        all_lines.append("")
                        total += len(events)
                if total == 0:
                    all_lines.append("（无事件）")
                return CallToolResult(content=[TextContent(type="text", text="\n".join(all_lines).strip())])
            else:
                events = await gcal_client.list_events(
                    calendar_id=calendar_id, time_min=time_min, time_max=time_max,
                    max_results=max_results, q=q,
                )
                lines = [f"日历 {calendar_id} 中的事件 (共 {len(events)} 条):", ""]
                for ev in events:
                    start = ev.get("start", {}) or {}
                    start_str = start.get("dateTime") or start.get("date", "?")
                    summary = ev.get("summary", "(无标题)")
                    eid = ev.get("id", "?")
                    lines.append(f"- {summary} | 开始: {start_str} [event_id: {eid}]")
                return CallToolResult(content=[TextContent(type="text", text="\n".join(lines))])
        except Exception as e:
            return CallToolResult(content=[TextContent(type="text", text=f"列出事件失败: {e}")], isError=True)

    elif name == "get_event":
        try:
            calendar_id = arguments.get("calendar_id") or "primary"
            event_id = arguments.get("event_id")
            if not event_id:
                return CallToolResult(content=[TextContent(type="text", text="Error: event_id is required")], isError=True)
            ev = await gcal_client.get_event(calendar_id, event_id)
            start = (ev.get("start") or {}).get("dateTime") or (ev.get("start") or {}).get("date", "?")
            end = (ev.get("end") or {}).get("dateTime") or (ev.get("end") or {}).get("date", "?")
            text = (
                f"摘要: {ev.get('summary', 'N/A')}\n"
                f"开始: {start}\n"
                f"结束: {end}\n"
                f"描述: {ev.get('description', '') or 'N/A'}\n"
                f"地点: {ev.get('location', '') or 'N/A'}\n"
                f"ID: {ev.get('id', 'N/A')}"
            )
            return CallToolResult(content=[TextContent(type="text", text=text)])
        except Exception as e:
            return CallToolResult(content=[TextContent(type="text", text=f"获取事件失败: {e}")], isError=True)

    elif name == "create_event":
        try:
            calendar_id = arguments.get("calendar_id") or "primary"
            summary = arguments.get("summary", "")
            start_str = arguments.get("start")
            end_str = arguments.get("end")
            description = arguments.get("description")
            location = arguments.get("location")
            if not summary:
                return CallToolResult(content=[TextContent(type="text", text="Error: summary is required")], isError=True)
            if not start_str or not end_str:
                return CallToolResult(content=[TextContent(type="text", text="Error: start and end are required")], isError=True)
            start_obj, end_obj = _parse_start_end(start_str, end_str)
            ev = await gcal_client.create_event(
                calendar_id=calendar_id, summary=summary, start=start_obj, end=end_obj,
                description=description, location=location,
            )
            return CallToolResult(
                content=[TextContent(type="text", text=f"✅ 事件已创建\n   ID: {ev.get('id')}\n   链接: {ev.get('htmlLink', 'N/A')}")]
            )
        except Exception as e:
            return CallToolResult(content=[TextContent(type="text", text=f"创建事件失败: {e}")], isError=True)

    elif name == "update_event":
        try:
            calendar_id = arguments.get("calendar_id") or "primary"
            event_id = arguments.get("event_id")
            if not event_id:
                return CallToolResult(content=[TextContent(type="text", text="Error: event_id is required")], isError=True)
            summary = arguments.get("summary")
            start_str = arguments.get("start")
            end_str = arguments.get("end")
            description = arguments.get("description")
            location = arguments.get("location")
            start_obj, end_obj = None, None
            if start_str is not None or end_str is not None:
                start_obj, end_obj = _parse_start_end(start_str, end_str)
            ev = await gcal_client.update_event(
                calendar_id=calendar_id, event_id=event_id,
                summary=summary, start=start_obj, end=end_obj,
                description=description, location=location,
            )
            return CallToolResult(
                content=[TextContent(type="text", text=f"✅ 事件已更新\n   ID: {ev.get('id')}\n   链接: {ev.get('htmlLink', 'N/A')}")]
            )
        except Exception as e:
            return CallToolResult(content=[TextContent(type="text", text=f"更新事件失败: {e}")], isError=True)

    elif name == "delete_event":
        try:
            calendar_id = arguments.get("calendar_id") or "primary"
            event_id = arguments.get("event_id")
            if not event_id:
                return CallToolResult(content=[TextContent(type="text", text="Error: event_id is required")], isError=True)
            await gcal_client.delete_event(calendar_id, event_id)
            return CallToolResult(content=[TextContent(type="text", text=f"✅ 事件已删除 (event_id: {event_id})")])
        except Exception as e:
            return CallToolResult(content=[TextContent(type="text", text=f"删除事件失败: {e}")], isError=True)

    else:
        return CallToolResult(content=[TextContent(type="text", text=f"Unknown tool: {name}")], isError=True)


def create_server() -> Server:
    """创建 MCP Server"""
    server = Server("gcal-mcp-server")

    @server.list_tools()
    async def list_tools() -> ListToolsResult:
        return ListToolsResult(tools=[Tool(**t) for t in GCAL_TOOLS_SCHEMA])

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
        return await execute_tool(name, arguments)

    return server


async def run_server_stdio() -> None:
    """运行 MCP Server (stdio 模式)"""
    if not gcal_client.authenticate():
        logger.error("认证失败，服务器无法启动")
        sys.exit(1)
    server = create_server()
    logger.info("启动 Google Calendar MCP Server (stdio 模式)...")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


async def run_server_http(host: str, port: int) -> None:
    """运行 MCP Server (HTTP 模式)"""
    if not gcal_client.authenticate():
        logger.error("认证失败，服务器无法启动")
        sys.exit(1)
    try:
        from starlette.applications import Starlette
        from starlette.routing import Route
        from starlette.responses import JSONResponse, Response
        from starlette.requests import Request
        from sse_starlette.sse import EventSourceResponse
        import uvicorn
    except ImportError:
        logger.error("HTTP 模式需要: pip install uvicorn starlette sse-starlette")
        sys.exit(1)

    server = create_server()

    async def handle_sse(request: Request):
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

    connections: dict[str, asyncio.Queue] = {}

    async def handle_message(request: Request):
        try:
            body = await request.json()
            client_id = request.query_params.get("client_id", "default")
            method = body.get("method", "")
            params = body.get("params", {})
            request_id = body.get("id")
            result = None
            error = None
            try:
                if method == "initialize":
                    result = {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"resources": {"listChanged": False}, "tools": {"listChanged": False}},
                        "serverInfo": {"name": "gcal-mcp-server", "version": "1.0.0"},
                    }
                elif method == "tools/list":
                    result = {"tools": GCAL_TOOLS_SCHEMA}
                elif method == "tools/call":
                    name = params.get("name", "")
                    arguments = params.get("arguments", {}) or {}
                    call_result = await execute_tool(name, arguments)
                    result = {
                        "content": [{"type": c.type, "text": getattr(c, "text", "")} for c in call_result.content],
                        "isError": getattr(call_result, "isError", False),
                    }
                elif method == "notifications/initialized":
                    return Response(status_code=204)
                else:
                    error = {"code": -32601, "message": f"Method not found: {method}"}
            except Exception as e:
                logger.exception(f"处理请求时出错: {e}")
                error = {"code": -32603, "message": str(e)}
            response = {"jsonrpc": "2.0", "id": request_id}
            if error:
                response["error"] = error
            else:
                response["result"] = result
            return JSONResponse(response)
        except Exception as e:
            logger.exception(f"解析请求失败: {e}")
            return JSONResponse({"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}}, status_code=400)

    async def health_check(request: Request):
        return JSONResponse({"status": "healthy", "server": "gcal-mcp-server"})

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
    logger.info(f"启动 Google Calendar MCP Server (HTTP) - http://{host}:{port}")
    logger.info(f"  健康检查: http://{host}:{port}/health  MCP: http://{host}:{port}/mcp")
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    await uvicorn.Server(config).serve()


def main() -> None:
    parser = argparse.ArgumentParser(description="Google Calendar MCP Server")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["auth", "serve"],
        default="serve",
        help="auth: 认证; serve: 启动服务 (默认)",
    )
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio", help="stdio 或 http")
    parser.add_argument("--host", default="0.0.0.0", help="HTTP 监听地址")
    parser.add_argument("--port", type=int, default=8000, help="HTTP 端口")
    args = parser.parse_args()

    if args.command == "auth":
        print("启动 OAuth 认证...")
        if gcal_client.run_auth_flow():
            print("\n✅ 认证成功。运行: python gcal_mcp_server.py [--transport http]")
        else:
            print("\n❌ 认证失败")
            sys.exit(1)
    else:
        if args.transport == "stdio":
            asyncio.run(run_server_stdio())
        else:
            asyncio.run(run_server_http(args.host, args.port))


if __name__ == "__main__":
    main()
