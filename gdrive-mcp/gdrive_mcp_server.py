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
# 使用完整的 drive 权限以支持读写操作
SCOPES = ["https://www.googleapis.com/auth/drive"]

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
        """运行 OAuth 认证流程（支持无浏览器的服务器环境）"""
        if not OAUTH_KEYS_FILE.exists():
            logger.error(f"OAuth 密钥文件不存在: {OAUTH_KEYS_FILE}")
            logger.error("请从 Google Cloud Console 下载 OAuth 2.0 凭证并保存到该路径")
            return False

        try:
            # 确保目录存在
            CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)

            # 使用 localhost 回调（用户需要从重定向 URL 中复制授权码）
            flow = InstalledAppFlow.from_client_secrets_file(
                str(OAUTH_KEYS_FILE), SCOPES,
                redirect_uri='http://localhost'
            )
            
            # 生成授权 URL
            auth_url, _ = flow.authorization_url(prompt='consent', access_type='offline')
            
            print("\n" + "="*60)
            print("服务器环境认证模式")
            print("="*60)
            print("1. 复制下面的 URL 到浏览器打开：")
            print()
            print(auth_url)
            print()
            print("2. 登录 Google 账号并授权")
            print("3. 授权后浏览器会跳转到一个打不开的页面（这是正常的）")
            print("4. 从浏览器地址栏复制完整的 URL（以 http://localhost:8085/?... 开头）")
            print("5. 将完整 URL 粘贴到下面")
            print("="*60)
            
            redirect_url = input("\n请粘贴完整的重定向 URL: ").strip()
            
            # 从 URL 中提取授权码
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(redirect_url)
            code = parse_qs(parsed.query).get('code', [None])[0]
            
            if not code:
                logger.error("无法从 URL 中提取授权码")
                return False
            
            # 交换授权码获取凭证
            flow.fetch_token(code=code)
            creds = flow.credentials
            
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

    async def list_folder(self, folder_id: str | None = None, page_size: int = 50) -> list[dict]:
        """
        列出指定文件夹中的文件和子文件夹
        
        Args:
            folder_id: 文件夹ID，None或"root"表示根目录
            page_size: 返回的最大文件数
            
        Returns:
            文件列表，每个文件包含 id, name, mimeType, 以及是否为文件夹的标识
        """
        try:
            # 如果没有指定folder_id或为"root"，则列出根目录
            if not folder_id or folder_id == "root":
                query = "'root' in parents and trashed = false"
            else:
                query = f"'{folder_id}' in parents and trashed = false"
            
            result = self.service.files().list(
                q=query,
                pageSize=page_size,
                fields="files(id, name, mimeType, size, modifiedTime)",
                orderBy="folder, name",  # 文件夹优先，然后按名称排序
            ).execute()
            
            files = result.get("files", [])
            
            # 添加是否为文件夹的标识
            for f in files:
                f["isFolder"] = f.get("mimeType") == "application/vnd.google-apps.folder"
            
            return files
        except HttpError as e:
            logger.error(f"列出文件夹内容失败: {e}")
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

    async def get_file_tree(self, max_files: int = 500) -> dict:
        """
        获取完整的文件系统树状结构快照
        
        Args:
            max_files: 最大获取文件数（避免超大Drive耗时过长）
            
        Returns:
            树状结构的字典，包含完整的目录层级
        """
        try:
            all_files = []
            page_token = None
            
            # 获取所有文件和文件夹（包含parents信息）
            while len(all_files) < max_files:
                params = {
                    "pageSize": min(100, max_files - len(all_files)),
                    "fields": "nextPageToken, files(id, name, mimeType, parents, size, modifiedTime)",
                    "q": "trashed = false",
                }
                if page_token:
                    params["pageToken"] = page_token
                
                result = self.service.files().list(**params).execute()
                files = result.get("files", [])
                all_files.extend(files)
                
                page_token = result.get("nextPageToken")
                if not page_token:
                    break
            
            # 构建 id -> file 的映射
            file_map = {f["id"]: f for f in all_files}
            
            # 构建树状结构
            root_children = []
            children_map = {}  # parent_id -> [children]
            
            for f in all_files:
                f["isFolder"] = f.get("mimeType") == "application/vnd.google-apps.folder"
                parents = f.get("parents", [])
                
                if not parents or "root" in [p for p in parents if p not in file_map]:
                    # 没有父文件夹，或父文件夹是root
                    root_children.append(f)
                else:
                    # 有父文件夹
                    for parent_id in parents:
                        if parent_id not in children_map:
                            children_map[parent_id] = []
                        children_map[parent_id].append(f)
            
            def build_tree(file_list: list, depth: int = 0, max_depth: int = 10) -> list:
                """递归构建树"""
                if depth > max_depth:
                    return []
                
                result = []
                # 先排序：文件夹优先，然后按名称
                sorted_files = sorted(file_list, key=lambda x: (not x.get("isFolder"), x.get("name", "")))
                
                for f in sorted_files:
                    node = {
                        "name": f["name"],
                        "id": f["id"],
                        "type": "folder" if f.get("isFolder") else "file",
                    }
                    if not f.get("isFolder"):
                        node["mimeType"] = f.get("mimeType", "unknown")
                        if f.get("size"):
                            node["size"] = f["size"]
                    
                    # 如果是文件夹，递归获取子项
                    if f.get("isFolder") and f["id"] in children_map:
                        node["children"] = build_tree(children_map[f["id"]], depth + 1, max_depth)
                    
                    result.append(node)
                
                return result
            
            tree = build_tree(root_children)
            
            # 统计信息
            folder_count = sum(1 for f in all_files if f.get("isFolder"))
            file_count = len(all_files) - folder_count
            
            return {
                "tree": tree,
                "stats": {
                    "total_items": len(all_files),
                    "folders": folder_count,
                    "files": file_count,
                    "truncated": len(all_files) >= max_files,
                }
            }
            
        except HttpError as e:
            logger.error(f"获取文件树失败: {e}")
            raise

    async def search_files(self, query: str, folder_id: str | None = None, page_size: int = 10) -> list[dict]:
        """
        搜索文件
        
        Args:
            query: 搜索关键词
            folder_id: 可选，限制搜索范围到指定文件夹
            page_size: 返回的最大文件数
        """
        try:
            # 转义查询字符串
            escaped_query = query.replace("\\", "\\\\").replace("'", "\\'")
            formatted_query = f"fullText contains '{escaped_query}'"
            
            # 如果指定了文件夹，添加父文件夹限制
            if folder_id:
                if folder_id == "root":
                    formatted_query += " and 'root' in parents"
                else:
                    formatted_query += f" and '{folder_id}' in parents"
            
            # 排除已删除的文件
            formatted_query += " and trashed = false"

            result = self.service.files().list(
                q=formatted_query,
                pageSize=page_size,
                fields="files(id, name, mimeType, modifiedTime, size)",
            ).execute()

            return result.get("files", [])
        except HttpError as e:
            logger.error(f"搜索文件失败: {e}")
            raise

    async def create_file(self, name: str, content: str, folder_id: str | None = None, mime_type: str = "text/plain") -> dict:
        """
        创建新文件
        
        Args:
            name: 文件名
            content: 文件内容
            folder_id: 父文件夹ID，None 表示根目录
            mime_type: 文件MIME类型，默认为纯文本
            
        Returns:
            创建的文件信息
        """
        try:
            from googleapiclient.http import MediaInMemoryUpload
            
            file_metadata = {"name": name}
            if folder_id and folder_id != "root":
                file_metadata["parents"] = [folder_id]
            
            # 根据文件扩展名自动设置 MIME 类型
            if name.endswith(".md"):
                mime_type = "text/markdown"
            elif name.endswith(".json"):
                mime_type = "application/json"
            elif name.endswith(".html"):
                mime_type = "text/html"
            elif name.endswith(".csv"):
                mime_type = "text/csv"
            
            media = MediaInMemoryUpload(
                content.encode("utf-8"),
                mimetype=mime_type,
                resumable=True
            )
            
            file = self.service.files().create(
                body=file_metadata,
                media_body=media,
                fields="id, name, mimeType, webViewLink"
            ).execute()
            
            logger.info(f"文件创建成功: {file['name']} (ID: {file['id']})")
            return file
        except HttpError as e:
            logger.error(f"创建文件失败: {e}")
            raise

    async def update_file(self, file_id: str, content: str, mime_type: str | None = None) -> dict:
        """
        更新文件内容
        
        Args:
            file_id: 文件ID
            content: 新的文件内容
            mime_type: 可选，文件MIME类型
            
        Returns:
            更新后的文件信息
        """
        try:
            from googleapiclient.http import MediaInMemoryUpload
            
            # 先获取文件信息以确定 MIME 类型
            if not mime_type:
                file_info = await self.get_file_metadata(file_id)
                mime_type = file_info.get("mimeType", "text/plain")
            
            media = MediaInMemoryUpload(
                content.encode("utf-8"),
                mimetype=mime_type,
                resumable=True
            )
            
            file = self.service.files().update(
                fileId=file_id,
                media_body=media,
                fields="id, name, mimeType, modifiedTime"
            ).execute()
            
            logger.info(f"文件更新成功: {file['name']} (ID: {file['id']})")
            return file
        except HttpError as e:
            logger.error(f"更新文件失败: {e}")
            raise

    async def create_folder(self, name: str, parent_folder_id: str | None = None) -> dict:
        """
        创建新文件夹
        
        Args:
            name: 文件夹名称
            parent_folder_id: 父文件夹ID，None 表示根目录
            
        Returns:
            创建的文件夹信息
        """
        try:
            file_metadata = {
                "name": name,
                "mimeType": "application/vnd.google-apps.folder"
            }
            if parent_folder_id and parent_folder_id != "root":
                file_metadata["parents"] = [parent_folder_id]
            
            folder = self.service.files().create(
                body=file_metadata,
                fields="id, name, webViewLink"
            ).execute()
            
            logger.info(f"文件夹创建成功: {folder['name']} (ID: {folder['id']})")
            return folder
        except HttpError as e:
            logger.error(f"创建文件夹失败: {e}")
            raise

    async def delete_file(self, file_id: str) -> bool:
        """
        删除文件或文件夹（移动到回收站）
        
        Args:
            file_id: 文件或文件夹ID
            
        Returns:
            是否删除成功
        """
        try:
            # 使用 trash 而不是永久删除，更安全
            self.service.files().update(
                fileId=file_id,
                body={"trashed": True}
            ).execute()
            
            logger.info(f"文件已移至回收站: {file_id}")
            return True
        except HttpError as e:
            logger.error(f"删除文件失败: {e}")
            raise

    async def move_file(self, file_id: str, new_parent_id: str) -> dict:
        """
        移动文件到新文件夹
        
        Args:
            file_id: 文件ID
            new_parent_id: 新的父文件夹ID
            
        Returns:
            更新后的文件信息
        """
        try:
            # 获取当前父文件夹
            file = self.service.files().get(
                fileId=file_id,
                fields="parents"
            ).execute()
            
            previous_parents = ",".join(file.get("parents", []))
            
            # 移动文件
            file = self.service.files().update(
                fileId=file_id,
                addParents=new_parent_id,
                removeParents=previous_parents,
                fields="id, name, parents"
            ).execute()
            
            logger.info(f"文件移动成功: {file['name']}")
            return file
        except HttpError as e:
            logger.error(f"移动文件失败: {e}")
            raise


# 全局客户端实例
drive_client = GoogleDriveClient()


async def get_root_folders_description() -> str:
    """
    获取根目录文件夹列表，用于动态生成 inputSchema 的 description
    这样 LLM 在获取工具列表时就能直接看到可用的文件夹
    """
    try:
        files = await drive_client.list_folder(folder_id="root")
        folders = [f for f in files if f.get("isFolder")]
        
        if not folders:
            return "Use 'root' for root directory, or omit to list root."
        
        # 构建文件夹列表描述
        folder_list = ", ".join([f"'{f['name']}' (id: {f['id']})" for f in folders[:10]])  # 最多显示10个
        
        if len(folders) > 10:
            folder_list += f", ... and {len(folders) - 10} more folders"
        
        return f"Available root folders: {folder_list}. Use 'root' to list root directory contents, or use a folder_id to explore subfolders."
    except Exception as e:
        logger.warning(f"获取根目录文件夹失败: {e}")
        return "Use 'root' for root directory, or a folder ID."


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
            # 二进制内容用 base64 编码作为文本返回
            blob_data = content if isinstance(content, str) else base64.b64encode(content).decode()
            return ReadResourceResult(
                contents=[
                    TextContent(
                        type="text",
                        text=f"[Base64 Encoded Binary Content]\n{blob_data}",
                    )
                ]
            )

    # ==================== Tools ====================

    @server.list_tools()
    async def list_tools() -> ListToolsResult:
        """列出可用工具 - 动态包含根目录文件夹信息"""
        # 动态获取根目录文件夹描述
        folder_description = await get_root_folders_description()
        
        return ListToolsResult(
            tools=[
                Tool(
                    name="get_file_tree",
                    description="Get a complete snapshot of the entire Google Drive file system structure as a tree. This is the BEST way to understand what files and folders exist. Returns a hierarchical tree view with all folders and files, including their IDs. Use this first to get a comprehensive overview before performing other operations.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "max_files": {
                                "type": "integer",
                                "description": "Maximum number of files to retrieve. Default is 500. Use smaller values for faster response.",
                                "default": 500,
                            },
                        },
                        "required": [],
                    },
                ),
                Tool(
                    name="list_folder",
                    description="List files and subfolders in a specific Google Drive folder. Use get_file_tree first for a complete overview, then use this for exploring specific folders in detail.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "folder_id": {
                                "type": "string",
                                "description": folder_description,
                            },
                        },
                        "required": [],
                    },
                ),
                Tool(
                    name="search",
                    description="Search for files in Google Drive by keywords. Can optionally limit search to a specific folder.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Search keywords to find in file names and contents",
                            },
                            "folder_id": {
                                "type": "string",
                                "description": f"Optional: Limit search scope. {folder_description}",
                            },
                        },
                        "required": ["query"],
                    },
                ),
                Tool(
                    name="read_file",
                    description="Read the content of a file by its ID. Get file IDs from get_file_tree, list_folder, or search results.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "file_id": {
                                "type": "string",
                                "description": "Google Drive file ID (get this from get_file_tree, list_folder, or search results)",
                            },
                        },
                        "required": ["file_id"],
                    },
                ),
                # ==================== 写入工具 ====================
                Tool(
                    name="create_file",
                    description="Create a new file in Google Drive with the specified content.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "File name (e.g., 'notes.txt', 'data.json', 'readme.md')",
                            },
                            "content": {
                                "type": "string",
                                "description": "The text content to write to the file",
                            },
                            "folder_id": {
                                "type": "string",
                                "description": f"Optional: Parent folder ID. {folder_description}",
                            },
                        },
                        "required": ["name", "content"],
                    },
                ),
                Tool(
                    name="update_file",
                    description="Update the content of an existing file. Use this to modify files you've read or created.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "file_id": {
                                "type": "string",
                                "description": "Google Drive file ID to update",
                            },
                            "content": {
                                "type": "string",
                                "description": "The new content to write to the file",
                            },
                        },
                        "required": ["file_id", "content"],
                    },
                ),
                Tool(
                    name="create_folder",
                    description="Create a new folder in Google Drive.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "Folder name",
                            },
                            "parent_folder_id": {
                                "type": "string",
                                "description": f"Optional: Parent folder ID. {folder_description}",
                            },
                        },
                        "required": ["name"],
                    },
                ),
                Tool(
                    name="delete_file",
                    description="Delete a file or folder by moving it to trash. This is reversible - files can be restored from trash.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "file_id": {
                                "type": "string",
                                "description": "Google Drive file or folder ID to delete",
                            },
                        },
                        "required": ["file_id"],
                    },
                ),
                Tool(
                    name="move_file",
                    description="Move a file or folder to a different location.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "file_id": {
                                "type": "string",
                                "description": "Google Drive file or folder ID to move",
                            },
                            "new_parent_id": {
                                "type": "string",
                                "description": f"Destination folder ID. {folder_description}",
                            },
                        },
                        "required": ["file_id", "new_parent_id"],
                    },
                ),
            ]
        )

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
        """执行工具调用"""
        if name == "get_file_tree":
            max_files = arguments.get("max_files", 500)
            
            try:
                result = await drive_client.get_file_tree(max_files=max_files)
                tree = result["tree"]
                stats = result["stats"]
                
                def format_tree(nodes: list, indent: str = "") -> list[str]:
                    """格式化树状结构为文本"""
                    lines = []
                    for i, node in enumerate(nodes):
                        is_last = i == len(nodes) - 1
                        prefix = "└── " if is_last else "├── "
                        
                        if node["type"] == "folder":
                            lines.append(f"{indent}{prefix}📁 {node['name']} [folder_id: {node['id']}]")
                            if "children" in node and node["children"]:
                                child_indent = indent + ("    " if is_last else "│   ")
                                lines.extend(format_tree(node["children"], child_indent))
                        else:
                            size_info = f", {node.get('size', 'N/A')} bytes" if node.get('size') else ""
                            lines.append(f"{indent}{prefix}📄 {node['name']} [file_id: {node['id']}]")
                    return lines
                
                output_lines = [
                    f"📊 Google Drive 文件系统快照",
                    f"   总计: {stats['total_items']} 项 ({stats['folders']} 个文件夹, {stats['files']} 个文件)",
                ]
                if stats.get("truncated"):
                    output_lines.append(f"   ⚠️ 结果已截断（达到 {max_files} 上限）")
                output_lines.append("")
                output_lines.append("📂 根目录")
                output_lines.extend(format_tree(tree))
                
                return CallToolResult(
                    content=[TextContent(type="text", text="\n".join(output_lines))]
                )
            except Exception as e:
                return CallToolResult(
                    content=[TextContent(type="text", text=f"获取文件树失败: {e}")],
                    isError=True,
                )
        
        elif name == "list_folder":
            folder_id = arguments.get("folder_id")
            
            try:
                files = await drive_client.list_folder(folder_id)
                
                if not files:
                    folder_desc = "根目录" if not folder_id or folder_id == "root" else f"文件夹 {folder_id}"
                    return CallToolResult(
                        content=[TextContent(type="text", text=f"{folder_desc} 是空的，没有文件或子文件夹。")]
                    )
                
                # 分类显示：先文件夹，后文件
                folders = [f for f in files if f.get("isFolder")]
                regular_files = [f for f in files if not f.get("isFolder")]
                
                result_lines = []
                folder_desc = "根目录" if not folder_id or folder_id == "root" else f"文件夹"
                result_lines.append(f"📂 {folder_desc} 内容 ({len(files)} 项):\n")
                
                if folders:
                    result_lines.append("📁 子文件夹:")
                    for f in folders:
                        result_lines.append(f"  - {f['name']} [folder_id: {f['id']}]")
                    result_lines.append("")
                
                if regular_files:
                    result_lines.append("📄 文件:")
                    for f in regular_files:
                        size = f.get('size', 'N/A')
                        if size != 'N/A':
                            size = f"{int(size):,} bytes"
                        result_lines.append(f"  - {f['name']} ({f['mimeType']}) [file_id: {f['id']}]")
                
                return CallToolResult(
                    content=[TextContent(type="text", text="\n".join(result_lines))]
                )
            except Exception as e:
                return CallToolResult(
                    content=[TextContent(type="text", text=f"列出文件夹内容失败: {e}")],
                    isError=True,
                )
        
        elif name == "search":
            query = arguments.get("query", "")
            folder_id = arguments.get("folder_id")  # 新增：获取可选的folder_id参数
            files = await drive_client.search_files(query, folder_id=folder_id)

            if not files:
                scope = "整个 Google Drive" if not folder_id else f"文件夹 {folder_id}"
                return CallToolResult(
                    content=[TextContent(type="text", text=f"在 {scope} 中没有找到匹配的文件。")]
                )

            file_list = "\n".join(
                f"- {f['name']} ({f['mimeType']}) [file_id: {f['id']}]"
                for f in files
            )
            scope = "整个 Drive" if not folder_id else f"文件夹 {folder_id}"
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=f"在 {scope} 中找到 {len(files)} 个文件:\n{file_list}",
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

        # ==================== 写入工具处理 ====================
        elif name == "create_file":
            file_name = arguments.get("name", "")
            content = arguments.get("content", "")
            folder_id = arguments.get("folder_id")
            
            if not file_name:
                return CallToolResult(
                    content=[TextContent(type="text", text="Error: name is required")],
                    isError=True,
                )
            
            try:
                result = await drive_client.create_file(file_name, content, folder_id)
                return CallToolResult(
                    content=[TextContent(
                        type="text",
                        text=f"✅ 文件创建成功!\n"
                             f"   名称: {result['name']}\n"
                             f"   ID: {result['id']}\n"
                             f"   类型: {result.get('mimeType', 'unknown')}\n"
                             f"   链接: {result.get('webViewLink', 'N/A')}"
                    )]
                )
            except Exception as e:
                return CallToolResult(
                    content=[TextContent(type="text", text=f"创建文件失败: {e}")],
                    isError=True,
                )

        elif name == "update_file":
            file_id = arguments.get("file_id", "")
            content = arguments.get("content", "")
            
            if not file_id:
                return CallToolResult(
                    content=[TextContent(type="text", text="Error: file_id is required")],
                    isError=True,
                )
            
            try:
                result = await drive_client.update_file(file_id, content)
                return CallToolResult(
                    content=[TextContent(
                        type="text",
                        text=f"✅ 文件更新成功!\n"
                             f"   名称: {result['name']}\n"
                             f"   ID: {result['id']}\n"
                             f"   修改时间: {result.get('modifiedTime', 'unknown')}"
                    )]
                )
            except Exception as e:
                return CallToolResult(
                    content=[TextContent(type="text", text=f"更新文件失败: {e}")],
                    isError=True,
                )

        elif name == "create_folder":
            folder_name = arguments.get("name", "")
            parent_folder_id = arguments.get("parent_folder_id")
            
            if not folder_name:
                return CallToolResult(
                    content=[TextContent(type="text", text="Error: name is required")],
                    isError=True,
                )
            
            try:
                result = await drive_client.create_folder(folder_name, parent_folder_id)
                return CallToolResult(
                    content=[TextContent(
                        type="text",
                        text=f"✅ 文件夹创建成功!\n"
                             f"   名称: {result['name']}\n"
                             f"   ID: {result['id']}\n"
                             f"   链接: {result.get('webViewLink', 'N/A')}"
                    )]
                )
            except Exception as e:
                return CallToolResult(
                    content=[TextContent(type="text", text=f"创建文件夹失败: {e}")],
                    isError=True,
                )

        elif name == "delete_file":
            file_id = arguments.get("file_id", "")
            
            if not file_id:
                return CallToolResult(
                    content=[TextContent(type="text", text="Error: file_id is required")],
                    isError=True,
                )
            
            try:
                await drive_client.delete_file(file_id)
                return CallToolResult(
                    content=[TextContent(
                        type="text",
                        text=f"✅ 文件已移至回收站 (ID: {file_id})\n"
                             f"   提示: 可在 Google Drive 回收站中恢复"
                    )]
                )
            except Exception as e:
                return CallToolResult(
                    content=[TextContent(type="text", text=f"删除文件失败: {e}")],
                    isError=True,
                )

        elif name == "move_file":
            file_id = arguments.get("file_id", "")
            new_parent_id = arguments.get("new_parent_id", "")
            
            if not file_id or not new_parent_id:
                return CallToolResult(
                    content=[TextContent(type="text", text="Error: file_id and new_parent_id are required")],
                    isError=True,
                )
            
            try:
                result = await drive_client.move_file(file_id, new_parent_id)
                return CallToolResult(
                    content=[TextContent(
                        type="text",
                        text=f"✅ 文件移动成功!\n"
                             f"   名称: {result['name']}\n"
                             f"   ID: {result['id']}\n"
                             f"   新位置: {new_parent_id}"
                    )]
                )
            except Exception as e:
                return CallToolResult(
                    content=[TextContent(type="text", text=f"移动文件失败: {e}")],
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
                    # 动态获取根目录文件夹描述
                    folder_description = await get_root_folders_description()
                    
                    result = {
                        "tools": [
                            {
                                "name": "get_file_tree",
                                "description": "Get a complete snapshot of the entire Google Drive file system structure as a tree. This is the BEST way to understand what files and folders exist. Returns a hierarchical tree view with all folders and files, including their IDs.",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "max_files": {
                                            "type": "integer",
                                            "description": "Maximum number of files to retrieve. Default is 500.",
                                            "default": 500,
                                        },
                                    },
                                    "required": [],
                                },
                            },
                            {
                                "name": "list_folder",
                                "description": "List files and subfolders in a specific Google Drive folder.",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "folder_id": {
                                            "type": "string",
                                            "description": folder_description,
                                        },
                                    },
                                    "required": [],
                                },
                            },
                            {
                                "name": "search",
                                "description": "Search for files in Google Drive by keywords. Can optionally limit search to a specific folder.",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "query": {
                                            "type": "string",
                                            "description": "Search keywords to find in file names and contents",
                                        },
                                        "folder_id": {
                                            "type": "string",
                                            "description": f"Optional: Limit search scope. {folder_description}",
                                        },
                                    },
                                    "required": ["query"],
                                },
                            },
                            {
                                "name": "read_file",
                                "description": "Read the content of a file by its ID. Get file IDs from get_file_tree, list_folder, or search results.",
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
                            # 写入工具
                            {
                                "name": "create_file",
                                "description": "Create a new file in Google Drive with the specified content.",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string", "description": "File name"},
                                        "content": {"type": "string", "description": "File content"},
                                        "folder_id": {"type": "string", "description": f"Optional: Parent folder ID. {folder_description}"},
                                    },
                                    "required": ["name", "content"],
                                },
                            },
                            {
                                "name": "update_file",
                                "description": "Update the content of an existing file.",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "file_id": {"type": "string", "description": "File ID to update"},
                                        "content": {"type": "string", "description": "New content"},
                                    },
                                    "required": ["file_id", "content"],
                                },
                            },
                            {
                                "name": "create_folder",
                                "description": "Create a new folder in Google Drive.",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string", "description": "Folder name"},
                                        "parent_folder_id": {"type": "string", "description": f"Optional: Parent folder. {folder_description}"},
                                    },
                                    "required": ["name"],
                                },
                            },
                            {
                                "name": "delete_file",
                                "description": "Delete a file or folder (moves to trash).",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "file_id": {"type": "string", "description": "File or folder ID to delete"},
                                    },
                                    "required": ["file_id"],
                                },
                            },
                            {
                                "name": "move_file",
                                "description": "Move a file or folder to a different location.",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "file_id": {"type": "string", "description": "File or folder ID to move"},
                                        "new_parent_id": {"type": "string", "description": f"Destination folder. {folder_description}"},
                                    },
                                    "required": ["file_id", "new_parent_id"],
                                },
                            },
                        ]
                    }

                elif method == "tools/call":
                    tool_name = params.get("name", "")
                    arguments = params.get("arguments", {})

                    if tool_name == "get_file_tree":
                        max_files = arguments.get("max_files", 500)
                        try:
                            tree_result = await drive_client.get_file_tree(max_files=max_files)
                            tree = tree_result["tree"]
                            stats = tree_result["stats"]
                            
                            def format_tree(nodes: list, indent: str = "") -> list[str]:
                                lines = []
                                for i, node in enumerate(nodes):
                                    is_last = i == len(nodes) - 1
                                    prefix = "└── " if is_last else "├── "
                                    if node["type"] == "folder":
                                        lines.append(f"{indent}{prefix}📁 {node['name']} [folder_id: {node['id']}]")
                                        if "children" in node and node["children"]:
                                            child_indent = indent + ("    " if is_last else "│   ")
                                            lines.extend(format_tree(node["children"], child_indent))
                                    else:
                                        lines.append(f"{indent}{prefix}📄 {node['name']} [file_id: {node['id']}]")
                                return lines
                            
                            output_lines = [
                                f"📊 Google Drive 文件系统快照",
                                f"   总计: {stats['total_items']} 项 ({stats['folders']} 个文件夹, {stats['files']} 个文件)",
                            ]
                            if stats.get("truncated"):
                                output_lines.append(f"   ⚠️ 结果已截断（达到 {max_files} 上限）")
                            output_lines.append("")
                            output_lines.append("📂 根目录")
                            output_lines.extend(format_tree(tree))
                            
                            result = {"content": [{"type": "text", "text": "\n".join(output_lines)}]}
                        except Exception as e:
                            result = {
                                "content": [{"type": "text", "text": f"获取文件树失败: {e}"}],
                                "isError": True,
                            }

                    elif tool_name == "list_folder":
                        folder_id = arguments.get("folder_id")
                        try:
                            files = await drive_client.list_folder(folder_id)
                            if not files:
                                folder_desc = "根目录" if not folder_id or folder_id == "root" else f"文件夹 {folder_id}"
                                result = {"content": [{"type": "text", "text": f"{folder_desc} 是空的。"}]}
                            else:
                                folders = [f for f in files if f.get("isFolder")]
                                regular_files = [f for f in files if not f.get("isFolder")]
                                
                                result_lines = []
                                folder_desc = "根目录" if not folder_id or folder_id == "root" else "文件夹"
                                result_lines.append(f"📂 {folder_desc} 内容 ({len(files)} 项):\n")
                                
                                if folders:
                                    result_lines.append("📁 子文件夹:")
                                    for f in folders:
                                        result_lines.append(f"  - {f['name']} [folder_id: {f['id']}]")
                                    result_lines.append("")
                                
                                if regular_files:
                                    result_lines.append("📄 文件:")
                                    for f in regular_files:
                                        result_lines.append(f"  - {f['name']} ({f['mimeType']}) [file_id: {f['id']}]")
                                
                                result = {"content": [{"type": "text", "text": "\n".join(result_lines)}]}
                        except Exception as e:
                            result = {
                                "content": [{"type": "text", "text": f"列出文件夹失败: {e}"}],
                                "isError": True,
                            }

                    elif tool_name == "search":
                        query = arguments.get("query", "")
                        folder_id = arguments.get("folder_id")
                        files = await drive_client.search_files(query, folder_id=folder_id)
                        if not files:
                            scope = "整个 Drive" if not folder_id else f"文件夹 {folder_id}"
                            result = {"content": [{"type": "text", "text": f"在 {scope} 中没有找到匹配的文件。"}]}
                        else:
                            file_list = "\n".join(
                                f"- {f['name']} ({f['mimeType']}) [file_id: {f['id']}]"
                                for f in files
                            )
                            scope = "整个 Drive" if not folder_id else f"文件夹 {folder_id}"
                            result = {
                                "content": [
                                    {"type": "text", "text": f"在 {scope} 中找到 {len(files)} 个文件:\n{file_list}"}
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

                    # 写入工具处理
                    elif tool_name == "create_file":
                        file_name = arguments.get("name", "")
                        content = arguments.get("content", "")
                        folder_id = arguments.get("folder_id")
                        if not file_name:
                            result = {"content": [{"type": "text", "text": "Error: name is required"}], "isError": True}
                        else:
                            try:
                                file_result = await drive_client.create_file(file_name, content, folder_id)
                                result = {"content": [{"type": "text", "text": f"✅ 文件创建成功! 名称: {file_result['name']}, ID: {file_result['id']}"}]}
                            except Exception as e:
                                result = {"content": [{"type": "text", "text": f"创建文件失败: {e}"}], "isError": True}

                    elif tool_name == "update_file":
                        file_id = arguments.get("file_id", "")
                        content = arguments.get("content", "")
                        if not file_id:
                            result = {"content": [{"type": "text", "text": "Error: file_id is required"}], "isError": True}
                        else:
                            try:
                                file_result = await drive_client.update_file(file_id, content)
                                result = {"content": [{"type": "text", "text": f"✅ 文件更新成功! 名称: {file_result['name']}, ID: {file_result['id']}"}]}
                            except Exception as e:
                                result = {"content": [{"type": "text", "text": f"更新文件失败: {e}"}], "isError": True}

                    elif tool_name == "create_folder":
                        folder_name = arguments.get("name", "")
                        parent_id = arguments.get("parent_folder_id")
                        if not folder_name:
                            result = {"content": [{"type": "text", "text": "Error: name is required"}], "isError": True}
                        else:
                            try:
                                folder_result = await drive_client.create_folder(folder_name, parent_id)
                                result = {"content": [{"type": "text", "text": f"✅ 文件夹创建成功! 名称: {folder_result['name']}, ID: {folder_result['id']}"}]}
                            except Exception as e:
                                result = {"content": [{"type": "text", "text": f"创建文件夹失败: {e}"}], "isError": True}

                    elif tool_name == "delete_file":
                        file_id = arguments.get("file_id", "")
                        if not file_id:
                            result = {"content": [{"type": "text", "text": "Error: file_id is required"}], "isError": True}
                        else:
                            try:
                                await drive_client.delete_file(file_id)
                                result = {"content": [{"type": "text", "text": f"✅ 文件已移至回收站 (ID: {file_id})"}]}
                            except Exception as e:
                                result = {"content": [{"type": "text", "text": f"删除文件失败: {e}"}], "isError": True}

                    elif tool_name == "move_file":
                        file_id = arguments.get("file_id", "")
                        new_parent_id = arguments.get("new_parent_id", "")
                        if not file_id or not new_parent_id:
                            result = {"content": [{"type": "text", "text": "Error: file_id and new_parent_id are required"}], "isError": True}
                        else:
                            try:
                                move_result = await drive_client.move_file(file_id, new_parent_id)
                                result = {"content": [{"type": "text", "text": f"✅ 文件移动成功! 名称: {move_result['name']}"}]}
                            except Exception as e:
                                result = {"content": [{"type": "text", "text": f"移动文件失败: {e}"}], "isError": True}

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
            Route("/health", health_check, methods=["GET"]),
            Route("/sse", handle_sse, methods=["GET"]),
            Route("/mcp", handle_message, methods=["POST"]),
            Route("/message", handle_message, methods=["POST"]),  # 兼容别名
            Route("/", handle_message, methods=["POST"]),  # POST到根路径
            Route("/", health_check, methods=["GET"]),     # GET根路径仍返回健康检查
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
