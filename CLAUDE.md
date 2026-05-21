# whatsapp-archive

Tools for working with exported WhatsApp chat archives.

## Status

Green-field. Only `sample-archive/` exists right now — no source code, no commits on `master` yet. Ground decisions in what's actually in the sample files rather than the abstract WhatsApp export spec; the format has locale and platform variations.

## Sample data format

`sample-archive/*.txt` are WhatsApp's plain-text export. Each line is one of:

- Message: `M/D/YY, HH:MM - Sender: body`
- System event: `M/D/YY, HH:MM - <event>` (e.g. `Ben created group "Gold"`, `Ben added you`, `<sender>: This message was deleted`)
- Continuation of a multi-line message body (no timestamp prefix — belongs to the previous message)

Gotchas worth keeping in mind:

- Dates are `M/D/YY` (US-style, no leading zeros). Don't assume ISO; don't assume the export is in the system locale.
- Senders include raw phone numbers when the contact isn't in the address book (`+31 6 27831253:`). The first ` - ` after the timestamp and the first `: ` after the sender are the reliable separators — not whitespace splits.
- `*Learn more*` and similar are WhatsApp's italics markup, not Markdown — strip or escape before rendering.
- System events have no sender colon. A naive `split(": ", 1)` parser will mislabel them as messages from a sender named after the date.
- "This message was deleted" still has a sender; the body is the literal string.

## Privacy / PII

`sample-archive/` contains real names and phone numbers from the owner's chats. Treat it as private input:

- Do not paste contents into web searches, public gists, pastebins, or external rendering services.
- Do not commit derivative artifacts (caches, embeddings, parsed JSON, fixtures) that contain raw messages, names, or numbers without explicit confirmation.
- If sharing output snippets externally, redact names and phone numbers first.

## Memory MCP

Local memories store at `http://localhost:8077` via the `memories` MCP. Project scope is `whatsapp-archive`.

- Before broad grep or web search: `memories.recall(query, scope="whatsapp-archive")`.
- When you discover a non-obvious format quirk, parser edge case, or sample-data oddity: `memories.remember(...)` with the same scope. These are exactly the facts that get re-discovered painfully.
- End of a non-trivial session: `memories.save_handoff(title, content, project="whatsapp-archive")`.

The global `~/.claude/CLAUDE.md` has the full memories-MCP playbook (recall flow, graph walks, archival caveats) — this file only fixes the project scope.

## Git

Remote: `https://github.com/Alienbushman/whatsapp-archive.git`. Default local branch is `master`; first commit will establish upstream. No commits yet, so don't reach for `git log` for context — there isn't any.
