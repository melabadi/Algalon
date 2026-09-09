---
name: local-deployment
description: "Install, upgrade, start, stop, verify, or troubleshoot the central local Algalon collector. Use for .pyz deployment, Docker/WSL runtime issues, ports, VS Code OTel setup, and local evidence retention across repositories."
argument-hint: "Installation repository and desired deployment action"
---
# Local deployment

Use [the deployment guide](../../../docs/local-deployment/README.md) as the operational source of truth.

## Procedure

1. Identify the repository that will hold the one local installation and whether a published `.pyz` or source checkout is available.
2. Confirm Python 3.11+ and Docker Compose. Check whether another Algalon installation owns the fixed ports; one running stack covers all local repositories.
3. If building from source, run the documented dependency install and `python scripts/copilot_value.py bundle`.
4. Verify the `.pyz` checksum, enter the target repository root, and run the artifact there.
5. Confirm `.copilot-value/` is ignored, local configuration exists, and VS Code user settings were backed up and updated. Never put application-scoped OTel settings in repository `.vscode/settings.json`.
6. Ask the user to reload VS Code and use Copilot in any local repository after the stack is healthy.
7. Run the installed `status` command and check app `3000`, Collector health `13133`, and VictoriaMetrics `8428`.
8. On Windows WSL-hosted Docker, verify the repository keepalive is active. Use the installed CLI for detached operation and release it with `stop`.
9. Preserve named volumes, prompt retention settings, credentials, and local assumptions during upgrades unless the user explicitly requests a reset.

## Safety boundaries

- Do not expose ports beyond `127.0.0.1`.
- Do not request a GitHub token or call GitHub APIs.
- Do not print passwords, prompt text, raw session IDs, or local telemetry.
- Do not delete named volumes while diagnosing startup or upgrade failures.