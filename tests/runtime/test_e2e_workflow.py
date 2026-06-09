"""Batch 2 最小端到端测试：workflow 脚本加载 → 子 agent 执行 → FINISHED。

验证路径：spawn_from_script → asyncio Task 启动 → entry_prompt 投递
→ 子 agent INIT → RUNNING → oneshot 自动 FINISHED。
"""

import asyncio
import os
import sys
import tempfile
import pytest
from harness.core.container import DIContainer
from harness.di import Harness
from harness.interfaces.input_adapter import InputAdapter
from harness.runtime.kernel import Kernel
from harness.runtime.agent_runtime import AgentRuntime, AgentState


# ── Helpers ────────────────────────────────────────────────────────────────


class _MockConsole:
    """Mock SystemConsole — 记录事件，receive 返回空。"""

    def __init__(self):
        self.events = []

    async def receive(self):
        from harness.runtime.types import CommandTalk
        return CommandTalk(pid="root", text="")

    async def send(self, event):
        self.events.append(event)


def _write_workflow_script(agent_specs: list[dict]) -> str:
    """创建临时 workflow 脚本，返回路径。

    Args:
        agent_specs: 每个元素 {"name": str, "entry_prompt": str, "subscribe_to": str|None}

    Returns:
        脚本文件绝对路径。
    """
    agent_blocks = []
    for spec in agent_specs:
        agent_blocks.append(f'''
@agent("{spec['name']}", entry_prompt="{spec['entry_prompt']}")
def assemble_{spec['name']}():
    container = DIContainer()
    container.register(InputAdapter, object())  # dummy，不会被调用
    return Harness.from_container(container, call_llm=None)
''')

    subscribe_blocks = []
    for spec in agent_specs:
        if spec.get("subscribe_to"):
            subscribe_blocks.append(
                f'subscribe("{spec["name"]}").to("{spec["subscribe_to"]}")'
            )

    content = f'''from harness.core.container import DIContainer
from harness.di import Harness
from harness.interfaces.input_adapter import InputAdapter
from harness.runtime.decorators import agent, subscribe
{"".join(agent_blocks)}
{"\n".join(subscribe_blocks)}
'''
    with tempfile.NamedTemporaryFile(
        mode='w', suffix='.py', delete=False,
    ) as f:
        f.write(content)
        path = f.name
    return path


# ── E2E Tests ──────────────────────────────────────────────────────────────


class TestMinimalE2EWorkflow:
    """最小端到端：spawn → agent 执行 → FINISHED。"""

    def test_single_oneshot_agent_runs_to_finished(self):
        """单个 oneshot agent：spawn → 执行 → FINISHED。"""
        path = _write_workflow_script([
            {"name": "worker", "entry_prompt": "do one thing"},
        ])

        async def _run():
            kernel = Kernel(_MockConsole())
            # spawn_from_script 在 async 上下文中调用，_has_loop=True
            result = kernel.spawn_from_script(path)

            assert result["workflow_flag"].startswith("wf_")
            assert len(result["agents"]) == 1
            assert result["agents"][0]["pid"] == "worker"

            # 等待 agent 完成
            worker = kernel.runtime_table["worker"]

            # yield 控制权给 event loop，让 Task 有机会启动
            # spawn_from_script 创建了 asyncio Task，但 Task 需要
            # event loop 的调度才能从 CREATED 进入 INIT→RUNNING
            await asyncio.sleep(0)
            # agent 执行很快（call_llm=None → 立即 StopEvent），
            # 可能已进入 TERMINATING 甚至 FINISHED
            assert worker.state in (
                AgentState.RUNNING, AgentState.TERMINATING, AgentState.FINISHED
            )

            # gather 所有 agent tasks
            tasks = list(kernel._tasks.values())
            assert len(tasks) == 1
            await asyncio.gather(*tasks)

            # 验证终态
            assert worker.state == AgentState.FINISHED
            assert worker.round_count >= 1
            assert worker.error is None

        try:
            asyncio.run(_run())
        finally:
            os.unlink(path)

    def test_two_agents_run_concurrently(self):
        """两个 oneshot agent 并发执行，都到达 FINISHED。"""
        path = _write_workflow_script([
            {"name": "collector", "entry_prompt": "collect"},
            {"name": "reporter", "entry_prompt": "report"},
        ])

        async def _run():
            kernel = Kernel(_MockConsole())
            result = kernel.spawn_from_script(path)

            assert len(result["agents"]) == 2

            # 等待全部完成
            tasks = list(kernel._tasks.values())
            assert len(tasks) == 2
            await asyncio.gather(*tasks)

            # 验证
            for pid in ["collector", "reporter"]:
                rt = kernel.runtime_table[pid]
                assert rt.state == AgentState.FINISHED, (
                    f"{pid} state={rt.state}, expected FINISHED"
                )
                assert rt.error is None

        try:
            asyncio.run(_run())
        finally:
            os.unlink(path)

    def test_entry_prompt_received_by_agent(self):
        """子 agent 的 _phase_init 收到了 entry_prompt。"""
        path = _write_workflow_script([
            {"name": "echo", "entry_prompt": "hello world"},
        ])

        async def _run():
            kernel = Kernel(_MockConsole())
            kernel.spawn_from_script(path)

            # entry_prompt 在步骤 9 投递，agent 应该在 receive() 中取到
            msg = kernel.input_queues["echo"].get_nowait()
            assert msg.text == "hello world"
            assert "workflow_flag" in msg.metadata

        try:
            asyncio.run(_run())
        finally:
            os.unlink(path)

    def test_agents_appear_in_list_agents(self):
        """spawn 后 list_agents 可看到子 agent，完成后状态为 finished。"""
        path = _write_workflow_script([
            {"name": "tasker", "entry_prompt": "task"},
        ])

        async def _run():
            kernel = Kernel(_MockConsole())
            kernel.spawn_from_script(path)

            # yield 控制权，让 Task 启动
            await asyncio.sleep(0)

            # 完成前查询
            snapshot_before = kernel.list_agents()
            assert snapshot_before["tasker"]["state"] in (
                "running", "terminating", "finished"
            )

            # 等待完成
            tasks = list(kernel._tasks.values())
            await asyncio.gather(*tasks)

            # 完成后查询
            snapshot_after = kernel.list_agents()
            assert snapshot_after["tasker"]["state"] == "finished"

        try:
            asyncio.run(_run())
        finally:
            os.unlink(path)

    def test_end_workflow_terminates_running_agent(self):
        """end_workflow 可终止 continuous agent。"""
        path = _write_workflow_script([
            {"name": "listener", "entry_prompt": "stay alive",
             "subscribe_to": None},  # 显式 None 但没 subscribe → oneshot
        ])

        # 用 subscribe 让它变成 continuous
        path2 = _write_workflow_script([
            {"name": "talker", "entry_prompt": "talk"},
            {"name": "listener", "entry_prompt": "listen",
             "subscribe_to": "talker"},
        ])

        async def _run():
            kernel = Kernel(_MockConsole())
            result = kernel.spawn_from_script(path2)

            # listener 是 continuous（有 subscribe），一轮后在 receive() 阻塞
            listener = kernel.runtime_table["listener"]
            assert listener.mode == "continuous"

            # 给 listener 一点时间完成第一轮（_phase_loop 后到达 receive() 阻塞）
            # talker 是 oneshot，会先完成
            await asyncio.sleep(0.05)

            # end_workflow 终止
            killed = kernel.end_workflow(result["workflow_flag"])
            assert "listener" in killed or "talker" in killed

            # 等待全部任务完成
            tasks = list(kernel._tasks.values())
            await asyncio.gather(*tasks)

            # 全部 FINISHED
            for pid in ["talker", "listener"]:
                assert kernel.runtime_table[pid].state == AgentState.FINISHED

        try:
            asyncio.run(_run())
        finally:
            os.unlink(path)
            os.unlink(path2)

    def test_parent_tracks_children(self):
        """父 agent 的 children 列表正确追踪子 agent。"""
        path = _write_workflow_script([
            {"name": "child1", "entry_prompt": "work"},
            {"name": "child2", "entry_prompt": "work"},
        ])

        async def _run():
            kernel = Kernel(_MockConsole())
            # 创建父 agent
            parent_container = DIContainer()
            parent_container.register(InputAdapter, object())  # dummy
            parent = AgentRuntime(
                pid="parent", mode="continuous",
                harness=Harness.from_container(
                    parent_container, call_llm=None
                ),
                kernel=kernel,
            )

            kernel.spawn_from_script(path, parent=parent)

            assert set(parent.children) == {"child1", "child2"}
            assert kernel.runtime_table["child1"].parent is parent
            assert kernel.runtime_table["child2"].parent is parent

            # 等待子 agent 完成
            tasks = list(kernel._tasks.values())
            await asyncio.gather(*tasks)

            # 即使子 agent FINISHED，children 列表仍保留
            assert set(parent.children) == {"child1", "child2"}

        try:
            asyncio.run(_run())
        finally:
            os.unlink(path)
