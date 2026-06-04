"""Harness Agent Template — Hook 生命周期事件名常量。

11 个生命周期事件名，与 architecture.md 中定义的 Hook 预留点一一对应。
使用常量避免字符串硬编码导致的拼写错误。
"""

# Phase 1: 会话初始化
EVENT_BEFORE_GUIDE_GENERATION = "before_guide_generation"
"""GuideProvider.get_guides() 之前触发，data 类型: AssemblyContext。"""

EVENT_AFTER_GUIDE_GENERATION = "after_guide_generation"
"""GuideProvider.get_guides() 返回后触发，data 类型: GuidesBundle。"""

# Phase 2: 多轮对话循环 — 外层（assemble）
EVENT_BEFORE_ASSEMBLE = "before_assemble"
"""ContextAssembler.assemble() 之前触发，data 类型: AssemblyContext。"""

EVENT_AFTER_ASSEMBLE = "after_assemble"
"""ContextAssembler.assemble() 返回后触发，data 类型: List[Message]。"""

# Phase 2: 多轮对话循环 — 内层（LLM 调用）
EVENT_BEFORE_LLM_CALL = "before_llm_call"
"""call_llm() 之前触发，data 类型: List[Message]。"""

EVENT_AFTER_LLM_CALL = "after_llm_call"
"""call_llm() 成功返回后触发，data 类型: Response。"""

# Phase 2: 多轮对话循环 — 内层（tool 执行）
EVENT_BEFORE_TOOL_EXECUTE = "before_tool_execute"
"""每个 tool 执行前触发，data 类型: ToolCall。"""

EVENT_AFTER_TOOL_EXECUTE = "after_tool_execute"
"""每个 tool 执行后触发，data 类型: ToolResult。"""

# Phase 3: 会话结束
EVENT_ON_SESSION_END = "on_session_end"
"""会话结束开始时触发（Sensor 之前），data 类型: Trajectory。"""

EVENT_AFTER_SENSOR = "after_sensor"
"""Sensor.sense() 之后触发，data 类型: Trajectory（语义上为只读观察点）。"""

# 异常处理
EVENT_ON_ERROR = "on_error"
"""run() 捕获异常后触发（raise 之前），data 类型: Exception。"""
