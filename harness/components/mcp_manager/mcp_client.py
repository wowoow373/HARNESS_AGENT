"""MCPClient — MCP 协议客户端。

管理与外部 MCP Server 的 stdio 连接，提供 list_tools() 和 call_tool() 方法。
基于 MCP 协议（JSON-RPC over stdio）与子进程通信。

注意：这是一个轻量实现，用于框架的默认 MCP 集成。
生产环境可替换为使用官方 mcp SDK 的实现。
"""

import json
import logging
import subprocess
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MCPServerConfig
# ---------------------------------------------------------------------------


@dataclass
class MCPServerConfig:
    """单个 MCP Server 的连接配置。

    Attributes:
        name: 逻辑名称（用于日志和标识）。
        command: 启动命令（如 "npx"、"python"）。
        args: 命令行参数。
        env: 额外环境变量。
        timeout: 连接超时（秒）。
    """

    name: str = ""
    command: str = ""
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    timeout: float = 30.0


# ---------------------------------------------------------------------------
# MCPClient
# ---------------------------------------------------------------------------


class MCPClient:
    """MCP 协议客户端。

    通过 stdio 与外部 MCP Server 子进程通信。
    支持 tools/list 和 tools/call 两个核心方法。

    用法::

        config = MCPServerConfig(
            name="fs",
            command="npx",
            args=["-y", "@anthropic/mcp-filesystem", "/tmp"],
        )
        client = MCPClient(config)
        client.start()
        tools = client.list_tools()
        result = client.call_tool("read", {"path": "/tmp/test.txt"})
        client.stop()
    """

    def __init__(self, config: MCPServerConfig):
        """初始化 MCP 客户端。

        Args:
            config: MCP Server 连接配置。
        """
        self._config = config
        self._process: Optional[subprocess.Popen] = None
        self._started = False

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self) -> None:
        """启动 MCP Server 子进程并完成初始化握手。

        Raises:
            RuntimeError: 子进程启动失败。
        """
        if self._started:
            return

        try:
            cmd = [self._config.command] + self._config.args
            env = {**__import__("os").environ, **self._config.env}

            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            self._started = True

            # 初始化握手：发送 initialize 请求
            self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "harness-agent", "version": "0.1.0"},
            })
            # 读取响应（简化：不验证响应内容）
            self._read_response()

            # 发送 initialized 通知
            self._send_notification("notifications/initialized", {})

            logger.debug(f"MCP Server '{self._config.name}' started successfully")
        except Exception as e:
            self._started = False
            self._cleanup_process()
            raise RuntimeError(
                f"Failed to start MCP Server '{self._config.name}': {e}"
            ) from e

    def stop(self) -> None:
        """停止 MCP Server 子进程。"""
        self._started = False
        self._cleanup_process()
        logger.debug(f"MCP Server '{self._config.name}' stopped")

    def is_running(self) -> bool:
        """检查 MCP Server 是否在运行。"""
        return self._started and self._process is not None and self._process.poll() is None

    # ------------------------------------------------------------------
    # MCP 协议方法
    # ------------------------------------------------------------------

    def list_tools(self) -> List[Dict[str, Any]]:
        """获取 MCP Server 暴露的工具列表。

        发送 tools/list 请求，返回原始工具定义列表。
        每个工具包含 name、description、inputSchema 字段。

        Returns:
            List[Dict]: MCP 原始工具定义列表。
        """
        if not self._started:
            return []

        result = self._send_request("tools/list", {})
        # tools 在 result["tools"] 中
        if isinstance(result, dict) and "tools" in result:
            return result["tools"]
        return []

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        """调用 MCP Server 上的工具。

        发送 tools/call 请求并返回结果。

        Args:
            name: 工具名称（MCP 原始名称）。
            arguments: 工具参数。

        Returns:
            MCP Server 返回的结果内容。
        """
        if not self._started:
            raise RuntimeError(
                f"MCP Server '{self._config.name}' is not running"
            )

        result = self._send_request("tools/call", {
            "name": name,
            "arguments": arguments,
        })

        # MCP 协议：result 在 content 数组中
        if isinstance(result, dict):
            if "content" in result:
                contents = result["content"]
                if isinstance(contents, list) and len(contents) > 0:
                    # 合并所有 text 类型 content
                    texts = [
                        c.get("text", "")
                        for c in contents
                        if isinstance(c, dict) and c.get("type") == "text"
                    ]
                    if texts:
                        return "\n".join(texts)
                    return contents
            # 直接返回字符串类型的 result
            if "result" in result:
                return result["result"]

        return result

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _send_request(self, method: str, params: Dict[str, Any]) -> Any:
        """发送 JSON-RPC 请求并读取响应。

        Args:
            method: MCP 方法名。
            params: 请求参数。

        Returns:
            解析后的响应 result。
        """
        request_id = str(uuid.uuid4())
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        self._write_json(request)
        return self._read_response()

    def _send_notification(self, method: str, params: Dict[str, Any]) -> None:
        """发送 JSON-RPC 通知（无 id，无响应）。

        Args:
            method: 通知方法名。
            params: 通知参数。
        """
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        self._write_json(notification)

    def _write_json(self, data: Dict[str, Any]) -> None:
        """向 MCP Server stdin 写入 JSON 行。

        Args:
            data: 要发送的 JSON 数据。
        """
        if not self._process or not self._process.stdin:
            raise RuntimeError("MCP process stdin is not available")
        line = json.dumps(data)
        self._process.stdin.write(line + "\n")
        self._process.stdin.flush()

    def _read_response(self) -> Any:
        """从 MCP Server stdout 读取一行 JSON 响应。

        Returns:
            解析后的响应 result 字段。

        Raises:
            RuntimeError: 读取失败或进程已退出。
        """
        if not self._process or not self._process.stdout:
            raise RuntimeError("MCP process stdout is not available")

        # 检查进程是否还在运行
        if self._process.poll() is not None:
            stderr = ""
            if self._process.stderr:
                stderr = self._process.stderr.read()
            raise RuntimeError(
                f"MCP Server '{self._config.name}' exited with code "
                f"{self._process.returncode}. stderr: {stderr[:500]}"
            )

        try:
            line = self._process.stdout.readline()
            if not line:
                raise RuntimeError(
                    f"MCP Server '{self._config.name}' closed stdout unexpectedly"
                )
            response = json.loads(line)

            if "error" in response:
                error = response["error"]
                raise RuntimeError(
                    f"MCP error: {error.get('message', str(error))}"
                )

            return response.get("result", response)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Invalid JSON from MCP Server: {e}") from e

    def _cleanup_process(self) -> None:
        """清理子进程资源。"""
        if self._process:
            try:
                if self._process.poll() is None:
                    self._process.terminate()
                    try:
                        self._process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self._process.kill()
                        self._process.wait()
            except Exception as e:
                logger.warning(
                    f"Error cleaning up MCP process '{self._config.name}': {e}"
                )
            finally:
                self._process = None
