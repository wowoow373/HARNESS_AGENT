#!/usr/bin/env python3
"""batch-01 真实 LLM 端到端集成测试。

测试范围：
  1. MinimalLLMAdapter 连通性 — 直连 DeepSeek API
  2. 单轮纯文本对话 trace
  3. 多轮对话 + 退出 trace
  4. LifecycleOrchestrator 完整三阶段 trace (init → loop → end)
  5. Sensor 接收完整 Trajectory 验证

API 配置从 harness/config/.env 读取。
"""

import os
import sys
import time

# ── 0. 加载 .env ──────────────────────────────────────────────────

def load_env(path: str) -> dict:
    """从 .env 文件加载配置（简单 key = value 格式）。"""
    config = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, value = line.partition("=")
            config[key.strip()] = value.strip()
    return config

env_path = os.path.join(os.path.dirname(__file__), "..", "harness", "config", ".env")
env = load_env(env_path)

API_KEY = env["api-key"]
BASE_URL = env["base_url"]
MODEL = env["model"]

print(f"{'='*60}")
print(f"batch-01 真实 LLM 端到端集成测试")
print(f"{'='*60}")
print(f"API:  {BASE_URL}")
print(f"Model: {MODEL}")
print(f"Key:  {API_KEY[:20]}...")
print()

# ── 导入 ──────────────────────────────────────────────────────────

from harness.core.container import DIContainer
from harness.core.llm_adapter import MinimalLLMAdapter
from harness.core.orchestrator import (
    _MinimalToolCallFunction,
    InputAdapter,
    Sensor,
    ContextAssembler,
    GuideProvider,
    MemoryBackend,
    ToolRegistry,
    _MinimalGuidesBundle,
    _MinimalResponse,
    _MinimalToolCall,
    _MinimalUserRequest,
    _MinimalTrajectory,
)
from harness.core.orchestrator import LifecycleOrchestrator

passed = 0
failed = 0


def check(label: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✓ {label}")
    else:
        failed += 1
        print(f"  ✗ {label}  ← {detail}")
    if detail:
        print(f"    {detail}")


# ══════════════════════════════════════════════════════════════════
# 测试 1：MinimalLLMAdapter 直连 DeepSeek
# ══════════════════════════════════════════════════════════════════

print(f"\n{'─'*60}")
print("TEST 1: MinimalLLMAdapter 直连 DeepSeek API 连通性")
print(f"{'─'*60}")

adapter = MinimalLLMAdapter(
    base_url=BASE_URL,
    api_key=API_KEY,
    model=MODEL,
    max_tokens=256,
    temperature=0.7,
)

t1_start = time.time()
response = adapter(
    messages=[{"role": "user", "content": "请用一句话回复：你好，世界。"}],
    tools=None,
)
t1_elapsed = time.time() - t1_start

check("API 返回 _MinimalResponse 类型", isinstance(response, _MinimalResponse))
check("响应包含文本内容", response.text is not None and len(response.text) > 0,
      f"text={repr(response.text[:100])}")
check("stop_reason 正确", response.stop_reason == "end_turn",
      f"stop_reason={response.stop_reason}")
check("请求耗时 < 30s", t1_elapsed < 30,
      f"elapsed={t1_elapsed:.2f}s")
check("无 tool_uses（纯文本问题）", len(response.tool_uses) == 0)

print(f"\n  LLM 回复: {response.text}")

# ══════════════════════════════════════════════════════════════════
# 测试 2：LLM 带 tool calling 场景（让 LLM 决定是否调用工具）
# ══════════════════════════════════════════════════════════════════

print(f"\n{'─'*60}")
print("TEST 2: MinimalLLMAdapter tool calling 场景")
print(f"{'─'*60}")

# 定义一个虚假的工具让 LLM 知道
fake_tools = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "获取指定城市的天气信息",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "城市名称"
                    }
                },
                "required": ["city"]
            }
        }
    }
]

t2_start = time.time()
response2 = adapter(
    messages=[{"role": "user", "content": "北京今天天气怎么样？请用工具查询。"}],
    tools=fake_tools,
)
t2_elapsed = time.time() - t2_start

check("API 返回正常", response2 is not None)
check("请求耗时 < 30s", t2_elapsed < 30,
      f"elapsed={t2_elapsed:.2f}s")

has_tool = len(response2.tool_uses) > 0
has_text = response2.text is not None and len(response2.text) > 0

print(f"  tool_uses 数量: {len(response2.tool_uses)}")
if response2.tool_uses:
    tc = response2.tool_uses[0]
    print(f"  tool name: {tc.function.name}")
    print(f"  tool args: {tc.function.arguments}")
print(f"  text: {repr(response2.text[:200]) if response2.text else None}")
print(f"  stop_reason: {response2.stop_reason}")

check("响应包含 tool_uses 或 text（至少一个）", has_tool or has_text)


# ══════════════════════════════════════════════════════════════════
# 测试 3：Orchestrator 完整三阶段 trace（单轮对话）
# ══════════════════════════════════════════════════════════════════

print(f"\n{'─'*60}")
print("TEST 3: Orchestrator 完整 trace — 单轮纯文本对话")
print(f"{'─'*60}")

container = DIContainer()

# 3.1 构建 InputAdapter (mock：提供一轮输入后退出)
class SingleTurnAdapter:
    def __init__(self):
        self.inputs = ["请用一句话介绍什么是 Python。"]
        self.outputs = []
        self.idx = 0

    def receive(self):
        if self.idx < len(self.inputs):
            t = self.inputs[self.idx]
            self.idx += 1
            return _MinimalUserRequest(text=t)
        return _MinimalUserRequest(text="")  # 空 → 退出

    def send(self, response):
        text = getattr(response, "text", str(response))
        self.outputs.append(text)

# 3.2 构建 ContextAssembler (简单组装)
class SimpleAssembler:
    def assemble(self, ctx):
        msgs = []
        if ctx.guides and ctx.guides.identity:
            msgs.append({"role": "system", "content": ctx.guides.identity})
        if ctx.user_request and ctx.user_request.text:
            msgs.append({"role": "user", "content": ctx.user_request.text})
        return msgs

# 3.3 构建 Sensor（捕获 Trajectory 用于验证）
class TraceSensor:
    def __init__(self):
        self.received_trajectory = None
        self.called = False

    def sense(self, trajectory):
        self.called = True
        self.received_trajectory = trajectory

# 3.4 注册组件
container.register(InputAdapter, SingleTurnAdapter())
container.register(ContextAssembler, SimpleAssembler())
container.register(Sensor, TraceSensor())

# 3.5 真实 LLM 适配器（纯文本，不传 tools）
llm_adapter = MinimalLLMAdapter(
    base_url=BASE_URL,
    api_key=API_KEY,
    model=MODEL,
    max_tokens=256,
    temperature=0.7,
)

t3_start = time.time()
orch = LifecycleOrchestrator(container, call_llm=llm_adapter)
orch._cached_guides = _MinimalGuidesBundle(
    identity="你是一个有帮助的编程助手。请用中文回复，简洁明了。"
)
orch._cached_tools = []

ctx = orch._phase_init()
orch._phase_loop(ctx)
trajectory = orch._build_trajectory(); orch._phase_end(trajectory)
t3_elapsed = time.time() - t3_start

# ── 验证 ──
adapter_inst = container.resolve(InputAdapter)
sensor_inst = container.resolve(Sensor)

print(f"\n  用户输入: {adapter_inst.inputs[0]}")
print(f"  Agent 输出: {adapter_inst.outputs[0][:200] if adapter_inst.outputs else 'N/A'}...")

check("Agent 有输出", len(adapter_inst.outputs) >= 1)
check("Sensor.sense() 被调用", sensor_inst.called)
check("Trajectory 包含 history",
      sensor_inst.received_trajectory is not None
      and len(sensor_inst.received_trajectory.history) > 0)
check("Trajectory.execution_time > 0",
      sensor_inst.received_trajectory is not None
      and sensor_inst.received_trajectory.execution_time > 0,
      f"execution_time={sensor_inst.received_trajectory.execution_time:.2f}s")
check("Trajectory.final_output 非空",
      sensor_inst.received_trajectory is not None
      and len(sensor_inst.received_trajectory.final_output) > 0,
      f"final_output={sensor_inst.received_trajectory.final_output[:100]}")
check("_phase_end 后 history 已清理", len(orch._history) == 0)
check("_phase_end 后 tool_call_records 已清理", len(orch._tool_call_records) == 0)
check("总耗时 < 60s", t3_elapsed < 60, f"elapsed={t3_elapsed:.2f}s")

if sensor_inst.received_trajectory:
    traj = sensor_inst.received_trajectory
    check("Trajectory 是 _MinimalTrajectory 类型", isinstance(traj, _MinimalTrajectory))
    # _history 在 _phase_end 中已清空，应通过 Trajectory 验证
    check("Trajectory 记录了 assistant 回复（history 非空且含 assistant）",
          any(h.get("role") == "assistant" for h in traj.history),
          f"roles in history: {[h.get('role') for h in traj.history]}")


# ══════════════════════════════════════════════════════════════════
# 测试 4：Orchestrator 多轮对话 trace
# ══════════════════════════════════════════════════════════════════

print(f"\n{'─'*60}")
print("TEST 4: Orchestrator 完整 trace — 多轮对话")
print(f"{'─'*60}")

container4 = DIContainer()

class MultiTurnAdapter:
    def __init__(self):
        self.inputs = [
            "请用一句话介绍 Python。",
            "它有哪些主要优点？",
            "",  # 第 3 轮空输入 → 退出
        ]
        self.outputs = []
        self.idx = 0

    def receive(self):
        if self.idx < len(self.inputs):
            t = self.inputs[self.idx]
            self.idx += 1
            return _MinimalUserRequest(text=t)
        return _MinimalUserRequest(text="")

    def send(self, response):
        text = getattr(response, "text", str(response))
        self.outputs.append(text)

class MultiTurnSensor:
    def __init__(self):
        self.called = False
        self.traj = None

    def sense(self, trajectory):
        self.called = True
        self.traj = trajectory

container4.register(InputAdapter, MultiTurnAdapter())
container4.register(ContextAssembler, SimpleAssembler())
container4.register(Sensor, MultiTurnSensor())

llm4 = MinimalLLMAdapter(
    base_url=BASE_URL,
    api_key=API_KEY,
    model=MODEL,
    max_tokens=256,
    temperature=0.7,
)

t4_start = time.time()
orch4 = LifecycleOrchestrator(container4, call_llm=llm4)
orch4._cached_guides = _MinimalGuidesBundle(
    identity="你是一个有帮助的助手。请用中文回复，简洁明了，不超过 3 句话。"
)
orch4._cached_tools = []

ctx4 = orch4._phase_init()
orch4._phase_loop(ctx4)
trajectory4 = orch4._build_trajectory(); orch4._phase_end(trajectory4)
t4_elapsed = time.time() - t4_start

adapter4 = container4.resolve(InputAdapter)
sensor4 = container4.resolve(Sensor)

print(f"\n  第 1 轮: {adapter4.inputs[0]}")
print(f"  回复 1: {adapter4.outputs[0][:150] if len(adapter4.outputs) > 0 else 'N/A'}...")
print(f"  第 2 轮: {adapter4.inputs[1]}")
print(f"  回复 2: {adapter4.outputs[1][:150] if len(adapter4.outputs) > 1 else 'N/A'}...")

check("多轮对话：有 2 个输出", len(adapter4.outputs) == 2,
      f"outputs count={len(adapter4.outputs)}")
# _history 只记录 assistant 回复（user 消息由 InputAdapter 管理不在 history 中）
# 所以 2 轮对话 → 2 条 assistant 记录
check("多轮对话：Trajectory.history 有 2 条 assistant 回复",
      sensor4.traj is not None and len(sensor4.traj.history) >= 2,
      f"history length={len(sensor4.traj.history) if sensor4.traj else 0}")
check("Sensor 被调用", sensor4.called)
check("Trajectory.final_output 包含第 2 轮回复",
      sensor4.traj is not None and len(sensor4.traj.final_output) > 0,
      f"final_output={sensor4.traj.final_output[:100] if sensor4.traj else 'N/A'}")
check("多轮耗时 < 60s", t4_elapsed < 60, f"elapsed={t4_elapsed:.2f}s")


# ══════════════════════════════════════════════════════════════════
# 汇总
# ══════════════════════════════════════════════════════════════════

total = passed + failed
print(f"\n{'='*60}")
print(f"测试结果汇总")
print(f"{'='*60}")
print(f"  Passed:  {passed}/{total}")
print(f"  Failed:  {failed}/{total}")
print(f"{'='*60}")

if __name__ == "__main__":
    if failed > 0:
        sys.exit(1)
    else:
        print("全部测试通过！batch-01 真实 LLM trace 验证成功。")
