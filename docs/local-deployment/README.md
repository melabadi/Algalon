# Local deployment guide

This guide is for a developer who wants one local stack to aggregate GitHub Copilot runtime OTel sessions from every local repository: Copilot CLI, Copilot SDK applications, the Copilot app, and Copilot-harness sessions in VS Code. The deployment is loopback-only, requires no GitHub token, and keeps telemetry on the workstation.

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
- At least one GitHub Copilot runtime client: Copilot CLI 1.0.64 or later (1.0.100 or later recommended), a Copilot SDK application, the Copilot app, or VS Code with GitHub Copilot using the **Copilot** harness

Copilot CLI 1.0.64 introduced the cache-token attribute names Algalon reads, and 1.0.100 flushes telemetry when a session exits. VS Code sessions that use the **Local** harness emit the extension's own telemetry dialect, which Algalon does not capture.

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
4. Sets `COPILOT_OTEL_ENABLED=true` for the current user so Copilot CLI, Copilot SDK, and Copilot app sessions export telemetry, backing up anything it changes under `.copilot-value/data/copilot-env-backups/`. See [Copilot CLI, SDK apps, and the Copilot app](#copilot-cli-sdk-apps-and-the-copilot-app).
5. Configures both supported Copilot OTel setting families in detected VS Code **user settings**.
6. Backs up changed VS Code settings under `.copilot-value/data/vscode-settings-backups/`.
7. Builds and starts the stack, then waits for all health endpoints.

VS Code's Copilot OTel settings are application-scoped. A repository `.vscode/settings.json` cannot activate them, which is why the installer updates user settings while preserving unrelated values.

The installer never enables content capture and never sets `OTEL_EXPORTER_OTLP_ENDPOINT`, which would switch on OTel export in unrelated tools. Copilot runtime clients export to their default `http://localhost:4318`, which reaches the loopback Collector.

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

Both setting families are intentional: `chat.agentHost.otel.*` covers the VS Code Agent Host that runs Copilot-harness sessions, and `github.copilot.chat.otel.*` covers Copilot runtime sessions that the Copilot Chat extension starts. The Collector keeps only Copilot runtime telemetry and discards the extension's own `copilot-chat` spans, so a Copilot-harness turn is counted once. Keep `captureContent` false. Do not put these keys in repository workspace settings.

3. Confirm the Algalon stack is running with the installed `status` command.
4. Run **Developer: Reload Window**.
5. Start a new agent session with the **Copilot** harness; an already-open session does not hot-load the OTel configuration, and **Local**-harness sessions are not captured.

### Copilot CLI, SDK apps, and the Copilot app

Copilot CLI, Copilot SDK applications, and the Copilot app share the Copilot runtime, which exports OTel when `COPILOT_OTEL_ENABLED=true` is present in its environment. The installer sets that variable once for the current user:

| Platform | What the installer changes |
| --- | --- |
| Windows | `COPILOT_OTEL_ENABLED=true` in `HKCU\Environment`, then broadcasts the environment change |
| macOS | A marked `export COPILOT_OTEL_ENABLED=true` block in each existing `~/.zshrc`, `~/.bashrc`, `~/.bash_profile`, and `~/.profile`, plus `conf.d/algalon-copilot-otel.fish` when a fish configuration folder exists |
| Linux | The same shell blocks, plus `~/.config/environment.d/90-algalon-copilot-otel.conf` |

Previous values and files are backed up under `.copilot-value/data/copilot-env-backups/`, and running the installer again changes nothing. If `COPILOT_OTEL_ENABLED` already holds another value, or `OTEL_EXPORTER_OTLP_ENDPOINT` or `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` points somewhere other than a local `:4318` endpoint, the installer warns and leaves your environment unchanged. Endpoint values are never printed.

Open a new terminal, or restart the Copilot app, so new processes inherit the variable; running sessions do not pick it up. On macOS, a Copilot app launched from the Dock does not read shell startup files. Start it from a terminal, or set the variable for GUI apps yourself.

To revert the change, run `restore-copilot-env`. It removes the managed blocks and files, or restores the previous value, and leaves values you changed yourself afterwards. Later installs and upgrades respect that choice until you run `enable-copilot-env`.

To configure the variable manually instead:

```powershell
setx COPILOT_OTEL_ENABLED true
```

```sh
export COPILOT_OTEL_ENABLED=true
```

#### Copilot SDK apps

An SDK application's runtime inherits the process environment, so `COPILOT_OTEL_ENABLED=true` is enough. To configure telemetry explicitly in code, point the SDK's telemetry option at the loopback Collector and leave content capture off:

```ts
const client = new CopilotClient({ telemetry: { otlpEndpoint: "http://127.0.0.1:4318" } });
```

```python
client = CopilotClient(telemetry={"otlp_endpoint": "http://127.0.0.1:4318"})
```

```go
client := copilot.NewClient(&copilot.ClientOptions{
    Telemetry: &copilot.TelemetryConfig{OTLPEndpoint: "http://127.0.0.1:4318"},
})
```

```csharp
var client = new CopilotClient(new CopilotClientOptions {
    Telemetry = new TelemetryConfig { OtlpEndpoint = "http://127.0.0.1:4318" },
});
```

A custom telemetry `sourceName` is fine: Algalon accepts the runtime's `github-copilot` service regardless of the instrumentation scope name.

#### Mark automated and SDK sessions

Algalon values saved developer time, so it treats every session as interactive unless the session is marked otherwise. For scripted pipelines, CI jobs, or batch SDK runs, set this in the process environment:

```sh
OTEL_RESOURCE_ATTRIBUTES=algalon.session.mode=automated,algalon.surface=sdk
```

Automated sessions keep their observed usage, tokens, and time, but produce no modeled value and stay out of modeled portfolio totals; the overview reports their AI spend separately. `algalon.surface` accepts `cli`, `sdk`, `app`, or `vscode`. Without it, VS Code Copilot-harness sessions are recognized automatically, and other runtime sessions are labeled `cli`. Do not set `OTEL_RESOURCE_ATTRIBUTES` for your whole user account; mark only the processes it describes.

## Start collecting sessions

1. Open a new terminal (or restart the Copilot app) so Copilot CLI and SDK processes inherit `COPILOT_OTEL_ENABLED=true`, and run **Developer: Reload Window** in VS Code.
2. Start a Copilot CLI session, an SDK app, the Copilot app, or a VS Code **Copilot**-harness session in any local repository.
3. Work normally; do not start or complete an experiment manually.
4. Open <http://127.0.0.1:3000>.

The stack must be running while Copilot emits OTel. The Collector keeps only Copilot runtime telemetry, privacy-scrubs each trace batch, and sends it directly to the app over the internal Docker network. FastAPI keys each span by the runtime's `gen_ai.conversation.id`, joins spans that lack it to their parent span's session, commits each span transactionally to SQLite, and deduplicates replay by span identity. A resumed CLI session keeps its conversation ID, so its later turns extend the same session. The worker persists one integer inbox cursor, reads only later rows, and keeps accumulated session evidence across restarts. Event time is evidence rather than an ingestion filter, so late spans update the same privacy-safe session record.

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
| Revert the Copilot environment variable | `python .\.copilot-value\scripts\copilot_value.py restore-copilot-env` | `python3 .copilot-value/scripts/copilot_value.py restore-copilot-env` |
| Re-enable the Copilot environment variable | `python .\.copilot-value\scripts\copilot_value.py enable-copilot-env` | `python3 .copilot-value/scripts/copilot_value.py enable-copilot-env` |

`status` should report healthy default services and HTTP endpoints. It also reports whether `COPILOT_OTEL_ENABLED` is set for your user, the Copilot CLI version found on `PATH`, and warnings when the variable is missing, the CLI is older than 1.0.64, or an OTLP endpoint variable sends runtime telemetry elsewhere. `stop` removes containers but preserves SQLite, metrics, telemetry, and session state in named volumes for all collected repositories. `restart` also restores optional Grafana when it was running before the restart.

Run `grafana` in an interactive terminal to set or reset its admin password. The prompt hides input and requires at least 16 characters. The installer never saves or prints it; Grafana stores the password hash in its persistent volume. Upgrades remove older plaintext password copies from the installation environment and data directory without changing the password hash. After explicitly clearing Grafana data, run `grafana` again to choose a new password.

`logs` shows the latest 200 lines by default. It accepts `victoria-metrics`, `collector`, `worker`, `app`, or `grafana` service filters, plus `--tail <lines|all>`, `--timestamps`, and `--follow`.

The prompt explorer groups each runtime session into one prompt per user message: the runtime starts a new trace for every message, and that trace's chat and tool spans form the prompt. Each prompt carries timing, request, token, tool, model, and AI-credit evidence. Runtime telemetry is exported without content, so prompt text is unavailable. No editor log or `workspaceStorage` folder is mounted.

Use **Export sessions + prompts** on the Overall view to download `algalon-data.csv`. Prompt identity, time, request/tool/token/model/cost evidence, and ROI attribution status appear before the supporting session columns, including each session's client surface, session mode, and exclusion reason. The export has one row per prompt, retains an explicitly labeled session row for sessions without prompts, and flattens nested usage, source, phase, and all scenario evidence into spreadsheet-readable columns. It has no date cutoff. The app does not upload the file, but the downloaded copy can contain sensitive local evidence and should be handled accordingly.

The Overall view also groups the currently displayed sessions by exact request-bearing model set. Requests and observed AI cost are summed within each cohort; time gained, net value, and return are medians over valid whole-session results for the selected scenario. A session using multiple models stays in one explicit **Mixed** cohort. Treat the table as descriptive context, not causal model performance or attribution of a mixed session to one model.

Copilot runtime clients reach the loopback Collector through the user environment and VS Code user settings, independent of the current working directory. Keep one Algalon stack running; it receives Copilot OTLP events from every local repository, even when the installation lives in a different repository. Do not run a preview app against this stack's writable `telemetry-data` volume: every app owns a SQLite indexer, so previews must use a separate volume.

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

Reinstalling preserves your existing `value-model.local.json`, so keys added by a new release are only filled where the schema has a default. `benchmark.maxIdleGapSeconds`, `benchmark.capacityRealizationBand`, and `reasoningTokenWeight` default automatically. `benchmark.phaseToolPatterns` is a complete configured object, so the runtime-capture upgrade migrates it once: it first backs up the file to `.copilot-value/data/config-backups/value-model.local.<UTC timestamp>.json`, then replaces the unchanged VS Code defaults with the runtime defaults from [config/value-model.example.json](../../config/value-model.example.json), or appends the missing runtime tool names to customized patterns. Only the `phaseToolPatterns` text changes; formatting and every other key are preserved. Patterns match whole tool-name tokens, so `rg` matches the runtime's `rg` tool but never `merge_pull_request`.

The app's **Methodology > Calibration workspace** exposes every numeric input used by the current mechanistic model. Custom calibration sets can be named and saved in browser-local storage; they remain separate from the current worker-published pessimistic, base, and optimistic results. In Methodology, select the saved set and choose which of its calibrated branches **Custom uses**. The top-bar **Custom** scenario button then recomputes the displayed portfolio, session, phase, and session-level prompt context from FastAPI session evidence. **Pessimistic**, **Base**, and **Optimistic** continue to show the worker results unchanged by that browser-local set.

Custom is disabled until a saved set is selected in Methodology. The app imports the same shared TypeScript benchmark module as the worker, so it does not duplicate the formula. Observed time, cost, tokens, tools, prompts, and source completeness never change.

Open **Session detail > Token and model evidence** to inspect the models actually recorded by runtime telemetry and their individual requests, input/cache-read/uncached/output/reasoning token counts, and AI credits. These observed rows do not change with the selected scenario. If the breakdown is missing, the app reports it as unavailable rather than guessing. See [Session model breakdown](../session-insights.md#session-model-breakdown) for accounting details and limits.

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

Copilot runtime telemetry is exported without content, and the Collector strips any message content that does arrive, so the prompt index never stores prompt text and nothing from a prompt becomes a metric label. `promptStorage.retentionDays` bounds how long prompt rows are kept:

```json
{
  "promptStorage": {
    "retentionDays": 30
  }
}
```

Restart the stack after changing configuration.

## Upgrade

Download the newer `.pyz`, enter the same installation repository, and run it again. The installer stages the new runtime, stops the old bind mounts, replaces files transactionally, recreates containers, and preserves local configuration, credentials, and named volumes. If replacement fails, it restores the previous runtime.

### Upgrading from a VS Code-era release

Releases before runtime capture measured the VS Code Copilot Chat extension. Its sessions, cost sources, and tool names are not comparable with Copilot runtime evidence, so the first start after this upgrade performs a one-time fresh start:

1. The app copies the SQLite database (verified online backup), worker session artifacts and state, and any `traces*.json` archives into `/data/backups/vscode-legacy-<UTC timestamp>/` inside the installation's `telemetry-data` volume.
2. It clears the VS Code-era sessions, prompts, and inbox, and records a completion marker. An interrupted migration resumes on the next start; it never runs again once complete.
3. The installer removes the obsolete VS Code `workspaceStorage` mount, migrates `phaseToolPatterns`, and sets `COPILOT_OTEL_ENABLED=true` as described above.

After the stack starts, `install` and `status` print the backup location, for example `Legacy VS Code data was backed up to '/data/backups/vscode-legacy-20261029T120000Z' inside Docker volume '<project>_telemetry-data'`, where `<project>` is `COMPOSE_PROJECT_NAME` in `.copilot-value/docker/.env`. The same report is available at <http://127.0.0.1:3000/api/internal/runtime-migration>. To copy the backup out of the volume while the stack is running:

```sh
docker cp <project>-app-1:/data/backups ./algalon-vscode-backup
```

The backup contains private local evidence; protect it like the live data. Legacy sessions are not re-scored under runtime rules. The backed-up `value.db` can be opened read-only with any SQLite tool; do not restore it into a current installation, which would migrate it again.

Files shipped under `.copilot-value/`, including Dockerfiles, are replaced from the artifact on every upgrade. Do not maintain manual Dockerfile edits or a wheelhouse there. Configure network access in the Docker runtime instead.

## Troubleshooting

### The app briefly reports a connection error

Run `status`. The React app retries transient API failures, but repeated refusal means the container runtime or host port forwarding is unavailable. On WSL-only Windows setups, run the repository `start` command so its keepalive is active rather than invoking detached Compose directly.

### Prompt counts or token totals stop refreshing

The header reports the durable indexing state: **Index current**, **Catching up**, **Indexing blocked**, or **Index status unavailable**. The status tooltip includes pending session count, oldest pending age, and the last successful publication time. `http://127.0.0.1:3000/api/indexing` exposes the same progress, and accepts an optional public `experiment` identifier to inspect one session. A responsive API alone does not establish that its index is current.

Accepted spans and their pending indexing work commit together in SQLite. Changed artifacts and configuration schedule affected sessions again. Each session's summary, prompt rows, and input checkpoint publish in one transaction. A restart resumes unfinished work, and failed publication preserves the previous valid snapshot. The worker publishes its consumed inbox cursor so indexing can wait for a summary that covers the retained input. When newer spans change no calculation input, the worker advances the existing summary's cursor instead of republishing unchanged values. Ingestion drops spans that end before they start, because the worker cannot use them.

Indexing uses fair batches of up to 16 sessions with a five-second scheduling budget between jobs. Per-session preparation runs in a read-only process with a 15-second deadline. Only the app process commits results. Failed work remains pending with exponential retry delays capped at 256 seconds. A missing worker artifact is initially **Catching up**; malformed counters, unavailable inputs, or 60-second freshness breaches report **Blocked**. The health endpoint returns HTTP 503 while indexing is blocked or a running pass exceeds 60 seconds. These are processing signals, not a claim that retained evidence was lost.

Upgrade an older installation with the latest verified PYZ to receive this behavior. Existing inbox history is scheduled through resumable migration pages. Initial backlog catch-up can exceed the live-update target; progress remains visible. Do not delete telemetry or reset volumes to force a refresh. If inputs remain blocked, inspect the reported reason and repair the source or storage availability, then allow the existing retry to resume.

### Storage pressure and recovery

The app refuses new ingestion with HTTP 503 and `Retry-After` when fewer than 64 MiB remain on the database filesystem or a write cannot commit. The Collector retains privacy-scrubbed retries in its persistent exporter queue on the telemetry volume. Replay is deduplicated by span identity. Queue capacity and storage are finite; monitor free space and do not treat this as protection against a destroyed disk or telemetry never received by the Collector.

SQLite online backups include committed inbox spans and unfinished indexing work. From the app container, `python -m app.database_backup /data/app/value.db /data/backups/value-backup.sqlite3` creates and verifies a new backup without stopping reads. It refuses to overwrite an existing destination. Copy verified backups off the telemetry disk to protect against disk loss; they contain private local evidence and require the same access controls as the live data.

Restore only while the app writer is stopped. The same utility can copy a verified backup into a new database path; point the installation at the restored database before restarting the single writer. Keep the associated session artifacts and local configuration when recovering the complete installation. Never run a second app against the live writable volume. Backup/restart recovery is covered by automated tests; catastrophic disk loss requires a separately retained backup.

### Docker builds cannot reach npm or PyPI

If image construction reports TLS handshake failures for `registry.npmjs.org`, `pypi.org`, or `files.pythonhosted.org`, verify the Docker runtime's network, proxy, and trusted certificate configuration, then rerun `install`.

Do not patch `.copilot-value/docker/` and switch to `start`: a later transactional upgrade correctly restores the artifact and removes those unsupported edits.

### The app has no sessions

Confirm that:

- `status` reports the Collector, worker, and app healthy, and `COPILOT_OTEL_ENABLED` enabled for your user;
- the Copilot client was started after the variable was set: open a new terminal, restart the Copilot app, or run **Developer: Reload Window** in VS Code;
- VS Code sessions use the **Copilot** harness; **Local**-harness sessions are not captured;
- no `OTEL_EXPORTER_OTLP_ENDPOINT` or `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` variable sends runtime telemetry elsewhere, and any explicit endpoint uses OTLP over HTTP at `http://127.0.0.1:4318`;
- a Copilot session has sent at least one message while the Collector was running.

Copilot CLI 1.0.100 and later flush telemetry when a session exits. With older versions, a short `copilot -p` run can exit before its spans are exported.

### A session shows observed usage but no modeled value

The session was explicitly marked `algalon.session.mode=automated`, or it has no AI cost or allocatable phase time. Automated sessions keep their observed usage and are listed with the automated exclusion reason; remove the marker from interactive processes.

### Ports are already in use

Stop the other Algalon installation. The product intentionally binds all endpoints to `127.0.0.1` and does not auto-select public or alternate ports. One running installation already covers all local repositories.

### Reset local evidence

The normal `stop` and `restart` commands preserve data. To permanently delete only this installation's telemetry, sessions, SQLite read model, metrics, and optional Grafana state while preserving configuration, credentials, settings and environment backups, and installation files, run:

Windows:

```powershell
python .\.copilot-value\scripts\copilot_value.py clean-data --yes
```

macOS or Linux:

```sh
python3 .copilot-value/scripts/copilot_value.py clean-data --yes
```

If the stack was running, it starts again with empty named volumes and restores optional Grafana when it was previously active. Add `--no-start` to leave it stopped. The command is scoped by the installation's Compose project name and does not delete another Algalon installation or unrelated Docker volumes. It also deletes any VS Code-era backup stored in the telemetry volume, so copy that backup out first if you need it.

To remove the installation itself, run `restore-copilot-env` and `stop`, remove `.copilot-value/`, and delete its named volumes only if `clean-data` was not run first.

## Privacy boundary

The Collector accepts only Copilot runtime telemetry and removes prompt, response, and reasoning content, system instructions, tool definitions and payloads, commands, file paths, repository metadata, hook payloads, MCP server names, end-user and organization identifiers, and exception details before SQLite ingestion or metrics export. Runtime telemetry is exported with content capture off, so prompt text is never stored. No repository source tree or editor log folder is mounted; the telemetry inbox, scrubbed logs, SQLite read model, VictoriaMetrics, and optional Grafana state never leave the workstation through this application. The explicit CSV download writes a user-requested copy to the browser's local download location; it does not cross the loopback boundary through Algalon.
