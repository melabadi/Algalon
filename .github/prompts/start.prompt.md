---
name: start
description: "Start the local Algalon app and verify every service and endpoint is healthy."
agent: agent
---

Start the existing local Algalon installation.

1. Resolve the installation repository by checking the current workspace first, then its immediate sibling directories, for `.copilot-value/.docker-install`.
2. Require exactly one installation. If none or multiple are found, stop and report the candidates instead of guessing.
3. From that repository root, run the installed CLI `start` command using `python` on Windows or `python3` on macOS/Linux:
   - Windows: `python .\.copilot-value\scripts\copilot_value.py start`
   - macOS/Linux: `python3 .copilot-value/scripts/copilot_value.py start`
4. Run the corresponding `status` command and verify all services and HTTP endpoints are healthy.
5. Report the result and the app URL: http://127.0.0.1:3000/

Do not invoke Docker Compose directly and do not use the obsolete Aspire dashboard scripts.