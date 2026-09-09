---
name: clean
description: "Permanently wipe this Algalon installation's collected data, then restore its prior running state."
agent: agent
---

Permanently wipe the collected data for the existing local Algalon installation. Invoking this prompt is confirmation to perform the destructive reset.

1. Resolve the installation repository by checking the current workspace first, then its immediate sibling directories, for `.copilot-value/.docker-install`.
2. Require exactly one installation. If none or multiple are found, stop and report the candidates instead of guessing.
3. From that repository root, run the installed CLI cleanup command:
   - Windows: `python .\.copilot-value\scripts\copilot_value.py clean-data --yes`
   - macOS/Linux: `python3 .copilot-value/scripts/copilot_value.py clean-data --yes`
4. If the installation was running, let the CLI restore its prior default services and optional Grafana state, then run `status` and verify health.
5. Report what was deleted and whether the app is running.

Use only `clean-data --yes`. Do not delete files, containers, or volumes manually. The command must remove only this installation's telemetry, sessions, SQLite read model, metrics, and Grafana volumes while preserving configuration, credentials, VS Code backups, and installation files.