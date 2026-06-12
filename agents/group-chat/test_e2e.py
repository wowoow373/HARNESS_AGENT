"""end-to-end test: group chat with real LLM via DeepSeek."""
import asyncio, sys, time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from harness.runtime.kernel import Kernel
from harness.runtime.cli_console import CliConsole
from harness.interfaces.types import TextEvent, UserRequest
from harness.runtime.types import AgentOutput, RuntimeStarted, RuntimeStopped


class TestConsole:
    """Console that doesn't read stdin — prevents EOF/CommandExit."""
    def __init__(self):
        self.events = []
    async def send(self, event):
        self.events.append(event)
        if isinstance(event, AgentOutput):
            print(f"  [📩 {event.pid}] {event.content}")
        elif hasattr(event, 'pid') and hasattr(event, 'content'):
            print(f"  [SYSTEM] {event}")
    async def receive(self):
        # Never return — we publish user messages programmatically
        while True:
            await asyncio.sleep(3600)


async def main():
    console = TestConsole()
    kernel = Kernel(console)

    # Spawn agents from workflow script
    script = str(Path(__file__).parent / "group_chat_demo.py")
    result = kernel.spawn_from_script(script, parent=None)
    print(f"Spawned: {[a['pid'] for a in result['agents']]}")

    # Agent tasks already started by spawn_from_script.
    # Wait for agents to initialize and receive entry_prompts.
    await asyncio.sleep(2.0)

    print("\n=== Round 1: user sends a message ===")
    await kernel.message_bus.publish(
        from_pid="user",
        event=TextEvent(content="大家好！今天天气真好，我们去公园玩吧？"),
    )
    print("  [👤 user] 大家好！今天天气真好，我们去公园玩吧？")

    # Wait for agents to respond
    await asyncio.sleep(6.0)

    print("\n=== Round 2: user replies ===")
    await kernel.message_bus.publish(
        from_pid="user",
        event=TextEvent(content="带飞盘怎么样？"),
    )
    print("  [👤 user] 带飞盘怎么样？")

    await asyncio.sleep(6.0)

    print("\n=== Round 3: user speaks again ===")
    await kernel.message_bus.publish(
        from_pid="user",
        event=TextEvent(content="那三点半在门口见？"),
    )
    print("  [👤 user] 那三点半在门口见？")

    await asyncio.sleep(6.0)

    # Cleanup
    print("\n=== Ending workflow ===")
    kernel.end_workflow(result['workflow_flag'])
    await asyncio.sleep(1.0)

    # Summary
    print(f"\n=== Results ===")
    for pid, rt in kernel.runtime_table.items():
        print(f"  {pid}: {rt.round_count} rounds, error={rt.error}")
        if rt.error:
            print(f"    ERROR: {rt.error}")


if __name__ == "__main__":
    asyncio.run(main())
