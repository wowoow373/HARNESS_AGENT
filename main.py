"""Harness Agent Template — CLI 入口。

提供两个子命令：

- ``init`` — 从领域模板生成新项目
- ``run``  — 按装配配置启动 Agent

用法::

    python main.py init --profile coding-assistant my-agent
    python main.py run --config harness.yaml
    python main.py run --config harness.yaml --debug
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys


def _setup_logging(debug: bool = False) -> None:
    """配置全局日志。

    Args:
        debug: True 时启用 DEBUG 级别，否则 INFO。
    """
    level = logging.DEBUG if debug else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(levelname)s | %(name)s | %(message)s",
    )


# ---------------------------------------------------------------------------
# init 子命令
# ---------------------------------------------------------------------------


def _cmd_init(args: argparse.Namespace) -> int:
    """从领域模板生成新项目。

    Args:
        args: 解析后的命令行参数（含 profile、output_dir、force）。

    Returns:
        int: 退出码（0 成功，1 失败）。
    """
    profile = args.profile
    output_dir = args.output_dir
    force = args.force

    # 查找模板目录
    project_root = os.path.dirname(os.path.abspath(__file__))
    template_dir = os.path.join(project_root, "profiles", profile)

    if not os.path.isdir(template_dir):
        print(f"Error: Profile '{profile}' not found.")
        # 列出可用模板
        profiles_root = os.path.join(project_root, "profiles")
        if os.path.isdir(profiles_root):
            available = [
                d for d in os.listdir(profiles_root)
                if os.path.isdir(os.path.join(profiles_root, d))
                and not d.startswith(".")
            ]
            if available:
                print(f"Available profiles: {', '.join(sorted(available))}")
        return 1

    # 检查输出目录
    if os.path.exists(output_dir):
        if os.listdir(output_dir) and not force:
            print(
                f"Error: Directory '{output_dir}' already exists and is not empty.\n"
                f"Use --force to overwrite."
            )
            return 1
        if force:
            print(f"Overwriting existing directory '{output_dir}'...")

    # 复制模板文件
    os.makedirs(output_dir, exist_ok=True)
    copied_files: list[str] = []
    for item in os.listdir(template_dir):
        src = os.path.join(template_dir, item)
        dst = os.path.join(output_dir, item)
        if os.path.isfile(src):
            shutil.copy2(src, dst)
            copied_files.append(item)

    print(f"Project initialized in '{output_dir}'")
    print(f"Profile: {profile}")
    print(f"Files created:")
    for f in sorted(copied_files):
        print(f"  - {f}")
    print()
    print("Next steps:")
    print(f"  1. cd {output_dir}")
    print(f"  2. Edit harness.yaml to customize components")
    print(f"  3. Edit AGENTS.md to configure agent behavior")
    print(f"  4. Run: python {os.path.relpath(__file__, output_dir)} run")

    return 0


# ---------------------------------------------------------------------------
# run 子命令
# ---------------------------------------------------------------------------


def _cmd_run(args: argparse.Namespace) -> int:
    """按装配配置启动 Agent。

    Args:
        args: 解析后的命令行参数（含 config、debug）。

    Returns:
        int: 退出码（0 成功，1 失败）。
    """
    config_path = args.config

    # 延迟导入，使命令响应更快
    from harness.config.yaml_assembler import (
        AssemblyError,
        YamlAssembler,
    )

    # 检查配置文件
    if not os.path.isfile(config_path):
        print(f"Warning: Config file '{config_path}' not found.")
        print("Falling back to default assembly (similar to examples/minimal_agent.py).")
        print()

        # 降级：使用全默认组件装配
        try:
            harness = _fallback_assemble()
        except Exception as e:
            print(f"Error: Fallback assembly failed: {e}")
            return 1
    else:
        # 正常路径：YAML 装配
        try:
            assembler = YamlAssembler()
            harness = assembler.load(config_path).assemble()
        except AssemblyError as e:
            print(f"Assembly error: {e}")
            return 1
        except FileNotFoundError as e:
            print(f"Error: {e}")
            return 1
        except Exception as e:
            print(f"Error: Failed to load config '{config_path}': {e}")
            return 1

    # 启动会话
    if args.runtime:
        return _run_with_runtime(
            harness, config_path,
            resume=getattr(args, "resume", None),
            force=getattr(args, "force", False),
        )
    else:
        try:
            harness.run()
        except KeyboardInterrupt:
            print("\n[系统] 收到中断信号，正在退出...")
        except Exception as e:
            print(f"Error: {e}")
            return 1

        print("\n[系统] Agent 已退出。")
        return 0


def _run_with_runtime(harness, config_path, resume=None, force=False) -> int:
    """使用 Runtime 层启动 agent（Mode A 交互式对话）。"""
    from harness.runtime.cli_console import CliConsole
    from harness.runtime.runtime import Runtime
    from harness.core.session.config import load_session_config
    from harness.core.session.exceptions import BootError, SessionOwnerConflict

    session_config = load_session_config(config_path)

    console = CliConsole(mode="mode_a")
    runtime = Runtime(console, session_config=session_config)

    logger = logging.getLogger(__name__)
    logger.info("Starting agent with Runtime layer (Mode A)")

    try:
        runtime.run(harness, resume=resume, force=force)
    except KeyboardInterrupt:
        print("\n[系统] 收到中断信号，正在退出...")
    except (SessionOwnerConflict, BootError) as e:
        print(f"恢复失败: {e}")
        return 2
    except Exception as e:
        print(f"Error: {e}")
        return 1

    return 0


def _cmd_workflow(args: argparse.Namespace) -> int:
    """Mode B: 直接启动 workflow 脚本。

    Args:
        args: 解析后的命令行参数（含 script_path、debug）。

    Returns:
        int: 退出码。
    """
    script_path = args.script_path

    if not os.path.isfile(script_path):
        print(f"Error: Workflow script '{script_path}' not found.")
        return 1

    from harness.runtime.cli_console import CliConsole
    from harness.runtime.runtime import Runtime
    from harness.core.session.config import load_session_config
    from harness.core.session.exceptions import BootError, SessionOwnerConflict

    session_config = load_session_config(getattr(args, "config", None))

    console = CliConsole(mode="mode_b")
    runtime = Runtime(console, session_config=session_config)

    logger = logging.getLogger(__name__)
    logger.info("Starting workflow (Mode B): %s", script_path)

    try:
        runtime.run_from_script(
            os.path.abspath(script_path),
            resume=getattr(args, "resume", None),
            force=getattr(args, "force", False),
        )
    except KeyboardInterrupt:
        print("\n[系统] 收到中断信号，正在退出...")
    except (SessionOwnerConflict, BootError) as e:
        print(f"恢复失败: {e}")
        return 2
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


def _fallback_assemble():
    """无 YAML 配置文件时的降级装配。

    使用所有默认组件，类似 ``examples/minimal_agent.py`` 的行为。
    注意：降级装配时 LLM 调用被跳过（call_llm=None），此模式仅供测试/演示。

    Returns:
        Harness: 使用默认组件装配的框架实例。
    """
    from harness.core.container import DIContainer
    from harness.di import Harness
    from harness.components.context_assembler.simple_assembler import SimpleAssembler
    from harness.components.guide_provider.file_guide_provider import FileGuideProvider
    from harness.components.input_adapter.cli_adapter import CliAdapter
    from harness.components.memory_backend.md_memory import MdMemory
    from harness.components.sensor.logging_sensor import LoggingSensor
    from harness.components.tool.default_system_tool_provider import (
        DefaultSystemToolProvider,
    )
    from harness.adapters.llm_adapter import MinimalLLMAdapter
    from harness.interfaces import (
        ContextAssembler,
        GuideProvider,
        InputAdapter,
        MemoryBackend,
        Sensor,
        SystemToolProvider,
    )

    container = DIContainer()

    # MemoryBackend
    memory = MdMemory(path="./memory")
    container.register(MemoryBackend, memory)

    # InputAdapter
    container.register(InputAdapter, CliAdapter())

    # GuideProvider — 自动发现当前目录下的指导文件
    guide_paths = []
    for candidate in ["AGENTS.md", "CLAUDE.md"]:
        if os.path.isfile(candidate):
            guide_paths.append(candidate)
    if guide_paths:
        container.register(GuideProvider, FileGuideProvider(guide_paths))

    # ContextAssembler
    container.register(
        ContextAssembler,
        SimpleAssembler(max_history=50, memory=memory),
    )

    # Sensor
    container.register(Sensor, LoggingSensor(memory=memory))

    # SystemToolProvider
    container.register(
        SystemToolProvider, DefaultSystemToolProvider()
    )

    # LLM Adapter — 自动从 .env 或环境变量读取配置
    llm = MinimalLLMAdapter()

    logger = logging.getLogger(__name__)
    logger.info(
        "Fallback assembly: %s @ %s (model=%s)",
        llm.__class__.__name__, llm.base_url, llm.model,
    )

    return Harness.from_container(container, call_llm=llm)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """构建 CLI 参数解析器（含 init/run/workflow 三个子命令）。"""
    parser = argparse.ArgumentParser(
        prog="harness",
        description="Harness Agent Template — modular agent framework",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # ---- init ----
    init_parser = subparsers.add_parser(
        "init",
        help="Initialize a new project from a profile template",
    )
    init_parser.add_argument(
        "--profile", "-p",
        default="coding-assistant",
        help="Profile template name (default: coding-assistant)",
    )
    init_parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Overwrite output directory if it already exists",
    )
    init_parser.add_argument(
        "output_dir",
        help="Output directory for the new project",
    )

    # ---- run ----
    run_parser = subparsers.add_parser(
        "run",
        help="Start the agent with the given assembly config",
    )
    run_parser.add_argument(
        "--config", "-c",
        default="./harness.yaml",
        help="Path to harness.yaml assembly config (default: ./harness.yaml)",
    )
    run_parser.add_argument(
        "--debug", "-d",
        action="store_true",
        help="Enable DEBUG log level",
    )
    run_parser.add_argument(
        "--runtime", "-r",
        action="store_true",
        help="Use Runtime layer (Mode A interactive with /commands support)",
    )
    run_parser.add_argument(
        "--resume",
        metavar="CONV_ID",
        default=None,
        help="恢复指定会话；无则全新启动",
    )
    run_parser.add_argument(
        "--force",
        action="store_true",
        help="配合 --resume 强制接管所有权 / 降级 manifest 硬校验",
    )

    # ---- workflow ----
    workflow_parser = subparsers.add_parser(
        "workflow",
        help="Run a workflow script directly (Mode B)",
    )
    workflow_parser.add_argument(
        "script_path",
        help="Path to workflow script (.py file with @agent declarations)",
    )
    workflow_parser.add_argument(
        "--debug", "-d",
        action="store_true",
        help="Enable DEBUG log level",
    )
    workflow_parser.add_argument(
        "--resume",
        metavar="CONV_ID",
        default=None,
        help="恢复指定会话；无则全新启动",
    )
    workflow_parser.add_argument(
        "--force",
        action="store_true",
        help="配合 --resume 强制接管所有权 / 降级 manifest 硬校验",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口。

    Args:
        argv: 命令行参数列表。为 None 时使用 sys.argv[1:]。

    Returns:
        int: 退出码。
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 1

    # 配置日志
    debug = getattr(args, "debug", False)
    _setup_logging(debug=debug)

    if args.command == "init":
        return _cmd_init(args)
    elif args.command == "run":
        return _cmd_run(args)
    elif args.command == "workflow":
        return _cmd_workflow(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
