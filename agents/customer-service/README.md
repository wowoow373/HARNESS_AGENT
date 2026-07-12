# customer-service — Multi-Hop QA Customer Service Agent

A customer service agent system demonstrating multi-hop verified question answering,
built on the Harness Agent Template framework with the topic_code verified QA approach.

## Architecture

6 Runtime-level agents orchestrated via Kernel workflow:

- **Router** — Intent classification (qa / task / fallback)
- **Direction** — Candidate direction generation
- **Evidence** — Retrieval + triple confirmation
- **Validation** — Global graph scoring + loop termination
- **Task (stub)** — Business operation placeholder
- **Fallback (stub)** — Out-of-scope handler

## Quick Start

```bash
# Terminal 1: Start WebSocket server
python agents/customer-service/server.py

# Terminal 2: Start Runtime workflow
python -c "
from harness.runtime.cli_console import CliConsole
from harness.runtime.runtime import Runtime
console = CliConsole(mode='mode_b')
Runtime(console).run_from_script('agents/customer-service/customer_service_workflow.py')
"
```

Then open http://localhost:8000 in browser, or type in terminal:
```
/talk router 改签规则是什么？
```

## Testing

```bash
# Unit tests (per-agent, no LLM needed)
pytest agents/customer-service/tests/unit/ -v

# Integration tests (topology, no LLM needed)
pytest agents/customer-service/tests/integration/ -v
```
