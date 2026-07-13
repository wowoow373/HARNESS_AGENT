# coding-assistant — Harness Agent Profile

A modular coding assistant profile built with the Harness Agent Template framework.

## Quick Start

```bash
# From the harness_agent project root:
python main.py run --config harness.yaml
```

## Customization

### Replace a Component

Edit `harness.yaml` → change the `implementation` field to your custom class:

```yaml
- interface: GuideProvider
  implementation: my_project.MyCustomGuideProvider  # ← your implementation
  params:
    custom_option: value
```

### Add a Lifecycle Hook

Edit `harness.yaml` → add entries to the `hooks` list:

```yaml
hooks:
  - event: before_llm_call
    handler: my_project.hooks.log_request
  - event: on_error
    handler: my_project.hooks.notify_error
```

Available events (see `harness/hooks/events.py`):
- `before_guide_generation`, `after_guide_generation`
- `before_assemble`, `after_assemble`
- `before_llm_call`, `after_llm_call`
- `before_tool_execute`, `after_tool_execute`
- `on_session_end`, `after_sensor`
- `on_error`

### Switch LLM Provider

Edit `harness.yaml` → change the `llm` section:

```yaml
llm:
  provider: custom
  model: my-local-model
  base_url: http://localhost:11434/v1
  api_key: ollama
```

### Use the Python API (Advanced)

For complex scenarios beyond YAML, edit `main.py` and use the Python assembly API directly:

```python
from harness.core.container import DIContainer
from harness.di import Harness

container = DIContainer()
container.register(InputAdapter, MyCustomAdapter())
# ... register more components
harness = Harness.from_container(container, call_llm=my_llm)
harness.run()
```

## File Structure

```
my-agent/
├── harness.yaml    # DI assembly declaration
├── AGENTS.md      # Agent persona and behavior rules
└── README.md      # This file
```

## Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `LLM_BASE_URL` | LLM API endpoint | OpenAI default |
| `OPENAI_API_KEY` | API key | (required) |
| `LLM_MODEL` | Model name | gpt-4o |
