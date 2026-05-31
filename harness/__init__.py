"""Harness Agent Template — 模块化 Agent 框架模板。

一个面向个人开发者和小型团队的模块化 Agent 框架模板。
框架只定义接口契约与编排流程，所有具体行为由用户通过
"实现接口 + 依赖注入"来自定义。
"""

__version__ = "0.1.0"

from .di import Harness
