---
description: "Use when changing FastAPI endpoints, SQLite schema/indexing, prompt grouping, retention, timestamps, or the local API read model."
applyTo: "backend/**/*.py"
---
# Backend instructions

- Keep SQLite as the transactional local OTLP inbox and derived read model over worker artifacts. The TypeScript worker remains the calculation owner.
- Maintain one writer, WAL mode, foreign keys, deterministic connection closure on Windows, and idempotent indexing.
- Stream retained scrubbed trace archives into the inbox once during upgrade. Batch commits, span-key deduplication, and a durable completion marker must make the import resumable without turning archives back into a live source.
- Reindex prompts when the OTLP inbox cursor, session artifacts, direct logs, or relevant configuration changes; handle span-before-artifact ordering.
- Prefer the exact matching VS Code direct session log: each `user_message` starts a prompt and following `llm_request.copilotUsageNanoAiu` values accumulate until the next direct user message. This matches Copilot's visible per-turn credits and avoids treating internal image/tool continuations as prompts.
- Match direct logs only by the exact OTel `gen_ai.conversation.id`/`copilot_chat.chat_session_id` found inside the authoritative resource session, keep the `workspaceStorage` mount read-only, and fall back to OTel user-request/trace grouping when no direct log exists.
- Prompt content stays local and must honor `promptStorage.enabled` and retention.
- Never infer prompt ROI from cost share. Return the explicit attribution-gap state until prompt-level phase and retained-source evidence exists.
- Use structured JSON and SQLite operations; avoid ad hoc serialization or timestamp comparisons when SQLite date functions apply.
- Validate with `python -m unittest discover -s backend/tests -p test_*.py` and exercise the affected endpoint.