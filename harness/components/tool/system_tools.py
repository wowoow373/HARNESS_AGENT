"""系统内置工具 — ReadFileTool、WriteFileTool、ShellTool。

这些是框架默认提供的本地工具，由 DefaultSystemToolProvider 管理。
每个工具实现 BaseTool 接口。
"""

import os
import subprocess
from typing import Any, Dict

from ...interfaces.types import ToolDefinition, ToolResult
from .base import BaseTool


# ---------------------------------------------------------------------------
# ReadFileTool
# ---------------------------------------------------------------------------


class ReadFileTool(BaseTool):
    """读取文件内容的工具。

    读取指定路径的文件并返回其内容。
    """

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="read_file",
            description="Read the contents of a file at the given path.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The path to the file to read.",
                    },
                },
                "required": ["path"],
            },
        )

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        path = args.get("path", "")
        if not path:
            return ToolResult(success=False, content=None, error="Missing 'path' argument")

        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            return ToolResult(success=True, content=content)
        except FileNotFoundError:
            return ToolResult(
                success=False, content=None, error=f"File not found: {path}"
            )
        except PermissionError:
            return ToolResult(
                success=False, content=None, error=f"Permission denied: {path}"
            )
        except Exception as e:
            return ToolResult(success=False, content=None, error=str(e))


# ---------------------------------------------------------------------------
# WriteFileTool
# ---------------------------------------------------------------------------


class WriteFileTool(BaseTool):
    """写入文件内容的工具。

    将内容写入指定路径的文件，会覆盖已存在的文件。
    """

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="write_file",
            description="Write content to a file at the given path. Overwrites existing files.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The path to the file to write.",
                    },
                    "content": {
                        "type": "string",
                        "description": "The content to write to the file.",
                    },
                },
                "required": ["path", "content"],
            },
        )

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        path = args.get("path", "")
        content = args.get("content", "")

        if not path:
            return ToolResult(success=False, content=None, error="Missing 'path' argument")

        try:
            # 确保上级目录存在
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return ToolResult(success=True, content=f"File written: {path}")
        except PermissionError:
            return ToolResult(
                success=False, content=None, error=f"Permission denied: {path}"
            )
        except Exception as e:
            return ToolResult(success=False, content=None, error=str(e))


# ---------------------------------------------------------------------------
# ShellTool
# ---------------------------------------------------------------------------


class ShellTool(BaseTool):
    """执行 Shell 命令的工具。

    在子进程中执行 Shell 命令并返回 stdout/stderr。
    """

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="shell",
            description="Execute a shell command and return the output.",
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute.",
                    },
                },
                "required": ["command"],
            },
        )

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        command = args.get("command", "")
        if not command:
            return ToolResult(
                success=False, content=None, error="Missing 'command' argument"
            )

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                output = result.stdout
                if result.stderr:
                    output += f"\n[stderr]\n{result.stderr}"
                return ToolResult(success=True, content=output)
            else:
                return ToolResult(
                    success=False,
                    content=result.stdout,
                    error=result.stderr or f"Exit code: {result.returncode}",
                )
        except subprocess.TimeoutExpired:
            return ToolResult(
                success=False, content=None, error="Command timed out (120s)"
            )
        except Exception as e:
            return ToolResult(success=False, content=None, error=str(e))
