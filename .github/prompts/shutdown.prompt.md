---
name: shutdown
description: "Stop the local Algalon app while preserving all collected data and configuration."
agent: agent
---

Shut down the existing local Algalon installation without deleting data.

1. Resolve the installation repository by checking the current workspace first, then its immediate sibling directories, for `.copilot-value/.docker-install`.
2. Require exactly one installation. If none or multiple are found, stop and report the candidates instead of guessing.
3. From that repository root, run the installed CLI `stop` command:
   - Windows: `python .\.copilot-value\scripts\copilot_value.py stop`
   - macOS/Linux: `python3 .copilot-value/scripts/copilot_value.py stop`
4. Report that the app is stopped and that persistent telemetry, sessions, SQLite data, metrics, configuration, and credentials were retained.

Do not invoke Docker Compose directly and do not remove volumes.