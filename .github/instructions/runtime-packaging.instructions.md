---
description: "Use when changing the Python installer, Docker Compose, images, Collector configuration, CI, release bundles, ports, upgrades, or Windows WSL behavior."
applyTo: "scripts/**/*.py,test/**/*.py,docker/**,config/**,.github/workflows/**"
---
# Runtime and packaging instructions

- Preserve Windows, macOS, Linux, `linux/amd64`, and `linux/arm64` support. Prefer native Docker; on Windows, support the `wsl.exe -- docker` fallback.
- A detached WSL-hosted stack requires the repository-scoped keepalive. `start`, install, status, and Grafana may ensure it exists; `stop` must release its marker.
- Detect VS Code `User/workspaceStorage` cross-platform and mount one existing directory read-only for exact local prompt-turn evidence. Use an empty installation-owned directory when none exists.
- Do not mount the installation repository into the central worker. One stack on the fixed loopback ports must aggregate sessions from all local repositories.
- Keep upgrades staged and transactional. Stop bind-mounted containers before replacement and preserve local config, credentials, and named volumes.
- Never package `docker/.env`, `config/value-model.local.json`, runtime `data/`, tests, development dependencies, or user-specific absolute paths.
- Keep host bindings loopback-only and fixed to the documented ports. Internal container listeners may use `0.0.0.0`.
- The Collector owns privacy enforcement and standard OTLP ports. Release validation must reject obsolete Aspire references and alternate Aspire ports.
- Add installer behavior tests under `test/`; validate Compose/Collector changes, construct the bundle, and smoke the exact artifact.