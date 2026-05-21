# AGENTS.md — whatsapp-archive

Thin pointer to the **memories MCP** at `http://localhost:8077`. The MCP is
the source of truth for this project's role contracts, ticket queue,
conventions, and per-project memory. This file captures only what isn't
already in the MCP.

Project scope: `whatsapp-archive`.

## Primary source of truth: memories MCP

Worker agents should call these directly rather than re-reading this file:

| Need | Call |
|---|---|
| Session orientation | `status(scope="whatsapp-archive")` |
| Role + project rollup | `agent_handbook(project="whatsapp-archive", role=<r>)` |
| Queue snapshot | `ticket_overview(project="whatsapp-archive")` |
| Adopt role and claim work | `agent_autostart(role=<r>, project="whatsapp-archive", model_hint=<m>)` |
| Adopt role contract only (no claim) | `agent_bootstrap(role=<r>, project="whatsapp-archive", claim=False)` |
| Search prior context | `recall(query=<q>, scope="whatsapp-archive")` |
| Save continuation | `save_handoff(title, content, project="whatsapp-archive")` |

Role contracts (`system_prompt` + `allowed_tools` + ceilings) come from
`agent_bootstrap` — they are NOT duplicated here. Always treat the returned
`role_config.system_prompt` as your operating contract for the claimed
ticket and stay within `role_config.allowed_tools`.

## Project facts

- **Repo:** clone of `https://github.com/Alienbushman/whatsapp-archive.git`
  (originally developed at `~/Documents/Code/pet_projects/whatsapp-archive` on
  the author's machine — paths are relative to the repo root, not absolute).
- **Default branch:** `master`
- **Status:** Live as of 2026-05-21 — full FastAPI + React + Qdrant + Ollama
  stack with topics, entities, collections, research bins, and dashboards.
- **Default role:** `planner` — break work down into a ticket DAG before
  coders execute.
- **Default template:** `subtask_v1`.

## Privacy / PII

`sample-archive/*.txt` contains real names and phone numbers. Treat as
private input — full rules in `CLAUDE.md` (Privacy / PII section). Quick
version:

- Never paste contents into external services (web search, pastebins,
  rendering tools).
- Never commit raw derivatives (caches, embeddings, parsed JSON, fixtures)
  without explicit user confirmation.
- Redact names and numbers from any output snippet shared externally.

## Parser format

WhatsApp's plain-text export has subtle quirks: US-style `M/D/YY` dates,
multi-line message continuations, system events with no sender, raw phone
numbers as senders. Full spec in `CLAUDE.md` (Sample data format). Ground
parser work in actual `sample-archive/*.txt` content, not the abstract
WhatsApp export spec.

## Worker flow

1. `agent_autostart(role=<r>, project="whatsapp-archive", model_hint=<m>)` —
   claims the next eligible ticket and returns the role contract.
2. Work the ticket per the contract; `ticket_heartbeat` every ~5 min on
   long edits.
3. `ticket_complete` (or `ticket_fail` with `retry=True` on hard errors)
   with `result_artifact_ids`; add `lessons` if non-routine.
4. Loop on `agent_tick(agent_id, wait_seconds=300)` until
   `queue_drained(project="whatsapp-archive")` reports drained, then exit.
