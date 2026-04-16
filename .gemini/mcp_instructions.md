# Setup Instructions for MCP (Model Context Protocol)

To get the most out of the Antigravity IDE with this project, ensure that you have your MCPs configured properly and that you instruct the agent to utilize them effectively.

## Recommended MCPs
For this Python CLI structure, we recommend having:
1. **`sequential-thinking`**: Used heavily by the AI to break down complex architectural CLI workflows or debugging stack traces in Python.
2. **`github` / git**: Helpful for automatically drafting PRs or committing logical chunks of work.

## Integrating Sequential Thinking
The template's `.gemini/rules.md` explicitly instructs the AI to use `sequential-thinking` for complex logic loops.
If you need to ensure the AI uses it, prefix your complex requests with an explicit trigger (e.g. "Use thinking...").
