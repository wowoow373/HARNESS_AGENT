"""ToolGovernanceLayer — 统一工具接入层的弹性策略与 Gate 编排。

每 agent 一个实例，包裹 ToolRouter。execute() 顺序：
  1. lookup 策略
  2. Gate：policy.gate 为 True 时经 ApprovalBroker 等待人工审批
  3. 执行：executor="thread" 用 asyncio.to_thread + wait_for 超时；
     executor="direct" 在事件循环内同步调用（runtime tools）
  4. 重试：retry_on 匹配的失败类别按 backoff 重试
  5. 收敛：所有故障（超时/异常/拒绝/审批超时/重试耗尽）→ ToolResult(success=False)

唯一向上传播的异常是 asyncio.CancelledError（kill 语义必须能打断工具执行）。
"""

from __future__ import annotations

import asyncio
import logging

from ...interfaces.types import ToolResult

logger = logging.getLogger(__name__)


class _ToolTimeout(Exception):
    """内部异常：单次执行超时（用于 retry_on="timeout" 判定）。"""

    def __init__(self, tool_name: str, timeout: float):
        super().__init__(f"tool '{tool_name}' timeout after {timeout}s")
        self.tool_name = tool_name
        self.timeout = timeout


class ToolGovernanceLayer:
    """工具治理层：Gate + 超时 + 重试。

    Args:
        tool_router: 已装配的 ToolRouter。
        policy_registry: PolicyRegistry 实例。
        approval_broker: ApprovalBroker 实例或 None（None 时 gate 工具拒绝执行）。
        pid: 当前 agent pid（审批事件展示用）。
    """

    def __init__(self, tool_router, policy_registry, approval_broker, *, pid: str = ""):
        self._router = tool_router
        self._registry = policy_registry
        self._broker = approval_broker
        self._pid = pid

    def has_tool(self, name: str) -> bool:
        return self._router.has_tool(name)

    async def execute(self, name: str, args: dict) -> ToolResult:
        policy = self._registry.lookup(name)

        gate_result = await self._gate(name, args, policy)
        if gate_result is not None:
            return gate_result

        return await self._execute_with_resilience(name, args, policy)

    # ------------------------------------------------------------------
    # Gate
    # ------------------------------------------------------------------

    async def _gate(self, name, args, policy) -> ToolResult | None:
        if not policy.gate:
            return None
        if self._broker is None:
            return ToolResult(
                success=False,
                error=f"tool '{name}' requires approval but no broker configured",
            )
        approval_id, future = self._broker.request(self._pid, name, args)
        try:
            approved = await asyncio.wait_for(future, timeout=policy.approval_timeout)
        except asyncio.TimeoutError:
            self._broker.cancel(approval_id)
            return ToolResult(
                success=False,
                error=f"approval timeout after {policy.approval_timeout}s",
            )
        except asyncio.CancelledError:
            self._broker.cancel(approval_id)
            raise
        if approved:
            return None
        return ToolResult(success=False, error="denied by operator")

    # ------------------------------------------------------------------
    # 执行 + 重试
    # ------------------------------------------------------------------

    async def _execute_with_resilience(self, name, args, policy) -> ToolResult:
        attempts = max(1, policy.retry.max_attempts)
        last_error: str | None = None

        for attempt in range(1, attempts + 1):
            try:
                result = await self._run_once(name, args, policy)
                return result  # success 或 success=False（业务失败不重试）
            except _ToolTimeout as e:
                last_error = str(e)
                if "timeout" not in policy.retry.retry_on or attempt == attempts:
                    break
                await asyncio.sleep(self._delay(policy.retry, attempt))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                if "exception" not in policy.retry.retry_on or attempt == attempts:
                    break
                await asyncio.sleep(self._delay(policy.retry, attempt))

        return ToolResult(success=False, error=last_error)

    async def _run_once(self, name, args, policy) -> ToolResult:
        if policy.executor == "direct":
            return self._router.execute(name, args)
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._router.execute, name, args),
                timeout=policy.timeout,
            )
        except asyncio.TimeoutError:
            raise _ToolTimeout(name, policy.timeout)

    @staticmethod
    def _delay(retry, attempt: int) -> float:
        if retry.backoff == "fixed":
            return retry.base_delay
        # exponential: base_delay * 2^(attempt-1)
        return retry.base_delay * (2 ** (attempt - 1))
