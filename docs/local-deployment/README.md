# Local deployment guide

This guide is for a developer who wants one local stack to aggregate GitHub Copilot OTel sessions from all repositories opened in VS Code. The deployment is loopback-only, requires no GitHub token, and keeps telemetry on the workstation.

For source development and release creation, use the project [README](../../README.md). For the ROI model and its limits, see [ROI Formula Evolution](../roi-formula-evolution.md).

## What gets installed

Running the self-installing `.pyz` from a convenient repository creates `<repository>/.copilot-value` as the installation home and starts four default containers:

- OpenTelemetry Collector on `127.0.0.1:4317`, `4318`, and health port `13133`
- VictoriaMetrics on `127.0.0.1:8428`
- TypeScript session ROI worker
- FastAPI, SQLite, and the React app on <http://127.0.0.1:3000>

Grafana is optional and uses <http://127.0.0.1:3001>. Only one stack can run at a time because these ports are fixed; that stack receives sessions from every local repository.

## Prerequisites

- Windows, macOS, or Linux
- Docker with Compose support
- Python 3.11 or later
- VS Code with GitHub Copilot Chat

Node.js is not required to install a published `.pyz`. It is required only when building that artifact from source.

## Obtain the release

Download the self-installing `copilot-value-dashboard-<version>.pyz` or conventional `.zip` and its matching `.sha256` file from [GitHub Releases](https://github.com/melabadi/Algalon/releases), or build from a source checkout. Publication requires successful CodeQL analysis, dependency and secret scans, coverage checks, and smoke tests of the exact artifacts. The PYZ is recommended for installation and transactional upgrades; the ZIP supports inspection or a fresh manual installation.

Windows:

```powershell
npm ci
python .\scripts\copilot_value.py bundle
```

macOS or Linux:

```sh
npm ci
python3 scripts/copilot_value.py bundle
```

The build writes the application, conventional zip, and checksums under `artifacts/`.

To verify a downloaded artifact on Windows:

```powershell
$expected = (Get-Content .\copilot-value-dashboard-<version>.pyz.sha256).Split()[0]
$actual = (Get-FileHash .\copilot-value-dashboard-<version>.pyz -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "Checksum mismatch" }
```

On Linux:

```sh
sha256sum --check copilot-value-dashboard-<version>.pyz.sha256
```

On macOS:

```sh
shasum -a 256 --check copilot-value-dashboard-<version>.pyz.sha256
```

Use the ZIP filename and checksum instead when verifying the ZIP. With GitHub CLI installed, also verify the build provenance for either downloaded format:

```sh
gh attestation verify copilot-value-dashboard-<version>.pyz --repo melabadi/Algalon
```

New releases are immutable after all assets are attached and the draft is published. A valid checksum and attestation confirm artifact identity and origin, not the absence of all vulnerabilities. See the [security policy](../../SECURITY.md).

## Install once into a repository

Open a terminal at a repository root that will hold the installation, then run the release by absolute or relative path. This location is the runtime home, not the measurement scope.

Windows:

```powershell
Set-Location C:\Repos\my-project
python C:\Downloads\copilot-value-dashboard-<version>.pyz
```

macOS or Linux:

```sh
cd ~/repos/my-project
python3 ~/Downloads/copilot-value-dashboard-<version>.pyz
```

For a fresh ZIP installation, extract the verified ZIP into a new `.copilot-value` directory in the chosen repository. Run `python .copilot-value/scripts/copilot_value.py install` on Windows or `python3 .copilot-value/scripts/copilot_value.py install` on macOS/Linux. Do not extract over an existing installation; use the PYZ for upgrades.

The installer:

1. Creates or transactionally upgrades `.copilot-value/`.
2. Adds `.copilot-value/` to the target repository's `.gitignore`.
3. Creates a local value-model configuration and installation-scoped Docker volumes.
4. Configures both supported Copilot OTel setting families in detected VS Code **user settings**.
5. Backs up changed VS Code settings under `.copilot-value/data/vscode-settings-backups/`.
6. Builds and starts the stack, then waits for all health endpoints.

VS Code's Copilot OTel settings are application-scoped. A repository `.vscode/settings.json` cannot activate them, which is why the installer updates user settings while preserving unrelated values.

### Package downloads

Docker builds use the public npm and PyPI registries:

- npm: `https://registry.npmjs.org/`
- PyPI: `https://pypi.org/simple`

The Docker runtime must be able to reach these registries and `files.pythonhosted.org`. Host npm and pip settings are not automatically inherited by containers. Keep local proxy settings and credentials out of source control and shared artifacts.

For a custom mirror, pass `install --npm-registry <https-url> --pip-index-url <https-url>`. These credential-free HTTPS URLs are stored in the ignored installation environment file and retained on upgrades. URLs containing user credentials, query strings, or fragments are rejected.

### Verify or configure VS Code manually

The installer is the preferred setup path. To audit its changes or repair the configuration manually:

1. Run **Preferences: Open User Settings (JSON)** from the VS Code Command Palette.
2. Add or verify these values in the user-level JSONC object, preserving unrelated settings:

```jsonc
{
  "chat.agentHost.otel.enabled": true,
  "chat.agentHost.otel.exporterType": "otlp-http",
  "chat.agentHost.otel.otlpEndpoint": "http://127.0.0.1:4318",
  "chat.agentHost.otel.captureContent": false,
  "github.copilot.chat.otel.enabled": true,
  "github.copilot.chat.otel.exporterType": "otlp-http",
  "github.copilot.chat.otel.otlpEndpoint": "http://127.0.0.1:4318",
  "github.copilot.chat.otel.captureContent": false
}
```

Both setting families are intentional: they cover the VS Code agent host and the GitHub Copilot Chat compatibility path supported by the installer. Keep `captureContent` false. Do not put these keys in repository workspace settings.

3. Confirm the Algalon stack is running with the installed `status` command.
4. Run **Developer: Reload Window**.
5. Start a new Copilot chat or agent session; an already-open session does not hot-load the OTel configuration.

## Start collecting sessions

1. Run **Developer: Reload Window** in VS Code after installation.
2. Start a Copilot chat or agent session in any local repository.
3. Work normally; do not start or complete an experiment manually.
4. Open <http://127.0.0.1:3000>.

The stack must be running while Copilot emits OTel. The Collector privacy-scrubs each trace batch and sends it directly to the app over the internal Docker network. FastAPI commits each span transactionally to SQLite and deduplicates replay by span identity. The worker persists one integer inbox cursor, reads only later rows, and keeps accumulated session evidence across restarts. Event time is evidence rather than an ingestion filter, so late spans update the same privacy-safe session record.

Standard Copilot OTel does not expose a trustworthy repository root or retained-source delta. The dashboard therefore labels central sessions **Telemetry only**, uses zero retained characters, and gives coding no positive benefit. Time, phases, tools, tokens, credits, and non-coding scenario inputs remain available. The worker never borrows source from the repository containing `.copilot-value`.

## Verify and operate

Run commands from the installation repository root.

| Action | Windows | macOS or Linux |
| --- | --- | --- |
| Status | `python .\.copilot-value\scripts\copilot_value.py status` | `python3 .copilot-value/scripts/copilot_value.py status` |
| Start | `python .\.copilot-value\scripts\copilot_value.py start` | `python3 .copilot-value/scripts/copilot_value.py start` |
| Stop | `python .\.copilot-value\scripts\copilot_value.py stop` | `python3 .copilot-value/scripts/copilot_value.py stop` |
| Restart | `python .\.copilot-value\scripts\copilot_value.py restart` | `python3 .copilot-value/scripts/copilot_value.py restart` |
| Recent logs | `python .\.copilot-value\scripts\copilot_value.py logs` | `python3 .copilot-value/scripts/copilot_value.py logs` |
| Follow worker/app logs | `python .\.copilot-value\scripts\copilot_value.py logs worker app --follow` | `python3 .copilot-value/scripts/copilot_value.py logs worker app --follow` |
| Start Grafana | `python .\.copilot-value\scripts\copilot_value.py grafana` | `python3 .copilot-value/scripts/copilot_value.py grafana` |

`status` should report healthy default services and HTTP endpoints. `stop` removes containers but preserves SQLite, metrics, telemetry, prompt evidence, and session state in named volumes for all collected repositories. `restart` also restores optional Grafana when it was running before the restart.

`logs` shows the latest 200 lines by default. It accepts `victoria-metrics`, `collector`, `worker`, `app`, or `grafana` service filters, plus `--tail <lines|all>`, `--timestamps`, and `--follow`.

The installer mounts the detected VS Code `User/workspaceStorage` directory into the app container read-only. The prompt explorer reads every direct chat log whose folder matches an OTel `gen_ai.conversation.id`/`copilot_chat.chat_session_id` found inside the current resource session, which makes prompt boundaries and credits agree with the Copilot UI. These local logs are not copied into the installation or sent through automatic telemetry export; SQLite stores the bounded prompt read model according to `promptStorage` settings. The complete OTel session remains the fallback when any matching direct log is unavailable.

Use **Export sessions + prompts** on the Overall view to download `algalon-data.csv`. Prompt identity, time, content, request/tool/token/model/cost evidence, and ROI attribution status appear before the supporting session columns. The export has one row per prompt, retains an explicitly labeled session row for sessions without prompts, and flattens nested usage, source, phase, and all scenario evidence into spreadsheet-readable columns. It has no date cutoff. When prompt storage is enabled, retained prompt text is included. The app does not upload the file, but the downloaded copy can contain sensitive local evidence and should be handled accordingly.

The Overall view also groups the currently displayed sessions by exact request-bearing model set. Requests and observed AI cost are summed within each cohort; time gained, net value, and return are medians over valid whole-session results for the selected scenario. A session using multiple models stays in one explicit **Mixed** cohort. Treat the table as descriptive context, not causal model performance or attribution of a mixed session to one model.

The loopback Collector is configured in VS Code user settings and is independent of the current working directory. Keep one Algalon stack running; it receives Copilot OTLP events from every local VS Code workspace, even when the installation lives in a different repository. Do not run a preview app against this stack's writable `telemetry-data` volume: every app owns a SQLite indexer, so previews must use a separate volume.

On Windows machines where Docker is available only through `wsl.exe`, the CLI maintains an installation-scoped WSL keepalive while the stack is running. Use the installed CLI for detached operation; `stop` releases the keepalive. This prevents WSL's VM idle timeout from repeatedly dropping port `3000`.

## Configure assumptions and prompt retention

Edit:

```powershell
code .\.copilot-value\config\value-model.local.json
```

Review the loaded hourly rate, capacity realization, phase assumptions, and pessimistic/base/optimistic calibration before sharing ROI results. The installer acknowledges the bundled defaults for local evaluation; they are not a substitute for measured organizational baselines.

Three keys control behavior introduced by formula version 2:

| Key | Default | Effect |
|---|---:|---|
| `benchmark.maxIdleGapSeconds` | `300` | Idle longer than this is excluded from the engaged time that phase allocation distributes. Elapsed window duration is still reported as observed evidence. |
| `benchmark.capacityRealizationBand` | `[0.25, 0.5, 0.75]` | Every scenario also reports benefit, net value, ROI, and break-even manual time at these capacity assumptions. |
| `benchmark.scenarios.*.{planning,validation}.reasoningTokenWeight` | `0` / `0` / `0.25` | Share of planning or validation reasoning tokens entering the human review term. Research uses uncached input and has no reasoning-token term. Reasoning tokens are never shown to the developer, so the pessimistic and base branches exclude them. |

Sessions benchmarked under an earlier formula version stay visible as observed sessions but are excluded from modeled portfolio totals and counted in `supersededFormulaSessions`. Releases use a separate worker calculation version to trigger retained-session re-evaluation when arithmetic, evidence derivation, or migration behavior changes; that number does not need to equal the formula version. Re-evaluation reconstructs phase evidence from retained inbox spans and runs automatically after upgrade. Do not delete installation data to force it. Pending migrations retry rather than publishing stale phase evidence; if modeled values remain superseded, inspect worker logs for migration diagnostics.

Reinstalling preserves your existing `value-model.local.json`, so keys added by a new release are only filled where the schema has a default. `benchmark.maxIdleGapSeconds`, `benchmark.capacityRealizationBand`, and `reasoningTokenWeight` default automatically; `benchmark.phaseToolPatterns` does not, because it is a complete configured object. After upgrading, compare your `phaseToolPatterns` against [config/value-model.example.json](../../config/value-model.example.json) and merge any newly recognized tool names. Version 2 added `replace_string_in_file`, `create_directory`, `edit_notebook_file`, and a shortened `rename` pattern to coding; `questions` to planning; `usages` to research; and `terminal`, `run_task`, and `task_output` to validation. Without the merge, real editing tools keep falling through to the unclassified phase.

The app's **Methodology > Calibration workspace** exposes every numeric input used by the current mechanistic model. Custom calibration sets can be named and saved in browser-local storage; they remain separate from the current worker-published pessimistic, base, and optimistic results. In Methodology, select the saved set and choose which of its calibrated branches **Custom uses**. The top-bar **Custom** scenario button then recomputes the displayed portfolio, session, phase, and session-level prompt context from FastAPI session evidence. **Pessimistic**, **Base**, and **Optimistic** continue to show the worker results unchanged by that browser-local set.

Custom is disabled until a saved set is selected in Methodology. The app imports the same shared TypeScript benchmark module as the worker, so it does not duplicate the formula. Observed time, cost, tokens, tools, prompts, and source completeness never change.

To use a local calibration against retained session data:

1. Open **Methodology > Calibration workspace**.
2. Edit the set's pessimistic, base, and optimistic inputs.
3. Choose which branch **Custom uses**, name the set, and save it.
4. Select **Custom** in the top scenario control from the overall or session view.
5. Return to **Pessimistic**, **Base**, or **Optimistic** at any time to view the unchanged worker-published results.

Browser-local evaluation is a sensitivity view; it does not rewrite session artifacts or published `copilot_value_*` metrics. To make the worker publish a calibration, copy the exported `benchmark` fragment into `value-model.local.json` and restart the stack. This deliberately replaces the worker's active `scenarios`; the fragment retains the prior branch snapshot in `presetScenarios` for reference.

Calibration sources are maintained with the model configuration, not added by dashboard users. The base-input evidence table maps each curated citation to the exact value it can inform and labels broader studies as context only. Inputs without direct support are explicitly marked **Local evidence needed** rather than inheriting credibility from an unrelated citation.

The ten-source starter register deliberately mixes positive and negative findings rather than selecting only literature favorable to AI. It includes controlled developer-task studies reporting **55.8% faster** completion in one bounded greenfield task and **19% longer** completion across mature-project tasks; three workplace RCTs estimating **26.08% more completed tasks** across 4,867 developers; observed Copilot suggestion-verification times; a systematic mapping of 95 code-comprehension experiments; reading and typing benchmarks; two official U.S. labor-cost sources; and the 2024 Work Trend Index survey. The Methodology table labels each mapping as **Direct support**, **Proxy benchmark**, or **Context only**. See [Literature triangulation for configured assumptions](../roi-formula-evolution.md#literature-triangulation-for-configured-assumptions) for the exact values, calculations, and claim limits.

Only the five-character standardized-word convention has direct literature support. To make the shipped example follow the closest compatible published observations, the example now uses **$92/hour** as a rounded BLS wage-plus-benefits proxy, **52 WPM** as the rounded typing-study mean for the audit-only metric, and **0.12 minutes/tool** as the lower interaction setting rounded from the CHI study's 7.03-second mean. These are still proxies: exact per-tool overhead and loaded labor require local validation, while relevant-token fractions, coding rates and fractions, capacity realization, and the unclassified multiplier remain local calibration questions. Treat the supplied values as literature-informed sensitivity presets, not validated constants.

Direct calibration should come from paired comparable tasks in the local population. Record manual active time by phase, task category, sample size, median, and dispersion. Keep greenfield work, bug fixes, rewrites, and research-only tasks separate instead of pooling unlike work. Configuration owners can then add the resulting reviewed source entry to `benchmark.calibrationSources`.

Prompt content is stored only in the bounded local SQLite index after local direct-turn or OTel fallback processing. It is never used as a metric label. To retain prompt statistics without text, set:

```json
{
  "promptStorage": {
    "enabled": false,
    "retentionDays": 30
  }
}
```

Restart the stack after changing configuration.

## Upgrade

Download the newer `.pyz`, enter the same installation repository, and run it again. The installer stages the new runtime, stops the old bind mounts, replaces files transactionally, recreates containers, and preserves local configuration, credentials, and named volumes. If replacement fails, it restores the previous runtime. When upgrading from archive-based collection, the app streams every retained scrubbed trace segment into the SQLite inbox once. Span deduplication makes the import resumable, and a durable completion marker prevents later archive polling.

Files shipped under `.copilot-value/`, including Dockerfiles, are replaced from the artifact on every upgrade. Do not maintain manual Dockerfile edits or a wheelhouse there. Configure network access in the Docker runtime instead.

## Troubleshooting

### The app briefly reports a connection error

Run `status`. The React app retries transient API failures, but repeated refusal means the container runtime or host port forwarding is unavailable. On WSL-only Windows setups, run the repository `start` command so its keepalive is active rather than invoking detached Compose directly.

### Docker builds cannot reach npm or PyPI

If image construction reports TLS handshake failures for `registry.npmjs.org`, `pypi.org`, or `files.pythonhosted.org`, verify the Docker runtime's network, proxy, and trusted certificate configuration, then rerun `install`.

Do not patch `.copilot-value/docker/` and switch to `start`: a later transactional upgrade correctly restores the artifact and removes those unsupported edits.

### The app has no sessions

Confirm that:

- VS Code was reloaded after OTel settings changed;
- `status` reports the Collector, worker, and app healthy;
- Copilot points to `http://127.0.0.1:4318` with the `otlp-http` exporter;
- a Copilot session has emitted a `session.id` while the Collector was running.

### Ports are already in use

Stop the other Algalon installation. The product intentionally binds all endpoints to `127.0.0.1` and does not auto-select public or alternate ports. One running installation already covers all local repositories.

### Reset local evidence

The normal `stop` and `restart` commands preserve data. To permanently delete only this installation's telemetry, sessions, SQLite read model, metrics, and optional Grafana state while preserving configuration, credentials, VS Code backups, and installation files, run:

Windows:

```powershell
python .\.copilot-value\scripts\copilot_value.py clean-data --yes
```

macOS or Linux:

```sh
python3 .copilot-value/scripts/copilot_value.py clean-data --yes
```

If the stack was running, it starts again with empty named volumes and restores optional Grafana when it was previously active. Add `--no-start` to leave it stopped. The command is scoped by the installation's Compose project name and does not delete another Algalon installation or unrelated Docker volumes.

To remove the installation itself, run `stop`, remove `.copilot-value/`, and delete its named volumes only if `clean-data` was not run first.

## Privacy boundary

The Collector removes response and reasoning content, system instructions, tool definitions and payloads, commands, file paths, repository metadata, hook payloads, and MCP server names before SQLite ingestion or metrics export. User request text remains local only when prompt storage is enabled. No repository source tree is mounted; the telemetry inbox, scrubbed logs, SQLite read model, VictoriaMetrics, and optional Grafana state never leave the workstation through this application. The explicit CSV download writes a user-requested copy to the browser's local download location; it does not cross the loopback boundary through Algalon.
