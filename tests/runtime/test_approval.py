"""ApprovalBroker 单元测试。"""

import asyncio

from harness.runtime.approval import ApprovalBroker


class _MockConsole:
    def __init__(self):
        self.events = []
        self.send_called = 0

    async def send(self, event):
        self.send_called += 1
        self.events.append(event)


def run(coro):
    return asyncio.run(coro)


def _new_broker():
    console = _MockConsole()
    return console, ApprovalBroker(console=console)


def test_request_creates_pending_and_event():
    async def _t():
        console, broker = _new_broker()
        aid, fut = broker.request("root", "delete_file", {"path": "/x"})
        assert len(aid) == 4
        assert not fut.done()
        assert broker.pending_count == 1
        await asyncio.sleep(0)  # 让事件发送 task 执行
        assert console.send_called == 1
        ev = console.events[0]
        assert ev.approval_id == aid
        assert ev.pid == "root"
        assert ev.tool_name == "delete_file"
        assert ev.arguments == {"path": "/x"}
    run(_t())


def test_resolve_approve():
    async def _t():
        _, broker = _new_broker()
        aid, fut = broker.request("root", "t", {})
        assert broker.resolve(aid, True) is True
        assert await fut is True
        assert broker.pending_count == 0
    run(_t())


def test_resolve_deny():
    async def _t():
        _, broker = _new_broker()
        aid, fut = broker.request("root", "t", {})
        assert broker.resolve(aid, False) is True
        assert await fut is False
        assert broker.pending_count == 0
    run(_t())


def test_resolve_unknown_returns_false():
    _, broker = _new_broker()
    assert broker.resolve("ffff", True) is False


def test_resolve_twice_returns_false_second():
    async def _t():
        _, broker = _new_broker()
        aid, fut = broker.request("root", "t", {})
        assert broker.resolve(aid, True) is True
        assert broker.resolve(aid, True) is False  # 已裁决
        assert await fut is True
    run(_t())


def test_cancel_cleans_pending():
    async def _t():
        _, broker = _new_broker()
        aid, fut = broker.request("root", "t", {})
        broker.cancel(aid)
        assert broker.pending_count == 0
        assert fut.cancelled()
    run(_t())


def test_cancel_unknown_is_noop():
    _, broker = _new_broker()
    broker.cancel("ffff")  # 不抛异常
    assert broker.pending_count == 0
