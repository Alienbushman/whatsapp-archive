# whatsapp-archive

A local web app for browsing, searching, and making sense of your WhatsApp chat
exports. Drop in your `.txt` files and get a rich UI that goes well beyond
"scroll the conversation":

- **Two-pane chat browser** with full-text search, message bookmarks, and dark
  mode
- **Link preview cards** — tweets, articles, and YouTube are unfurled inline
  with previews, OG metadata, and quoted text
- **Topics** — articles are clustered into named topics you can pin, rename, and
  return to (preserved across re-clusterings)
- **Collections + Research Bins** — curated buckets for long-running research,
  plus scratchpads tied to a search session
- **Entities** — people, hashtags, mentions are extracted, linked, and
  cross-referenced across chats
- **Similar tweets** — find every chat where a URL was shared, with one-click
  jump to the original message
- **Ask panel** — natural-language queries answered locally via Ollama

Everything runs locally. No cloud, no telemetry, no third-party API keys.

---

## Quick start (Docker — recommended)

This is the path that ships with the project. It brings up the web app plus the
two dependencies it needs (Qdrant for vectors, Ollama for embeddings + LLM).

**Prereqs:**
- Docker Desktop (or any Docker engine + `docker compose v2`)
- ~5 GB free disk (image ~700 MB, Ollama models ~3 GB, vectors + DB scale with corpus)
- ~8 GB RAM available (Ollama uses ~6 GB when active)
- Ports **8800, 6336, 11437** free (web, qdrant, ollama — edit `docker-compose.yml` if taken)
- Outbound internet to `pypi.org`, `registry.npmjs.org`, `ollama.com` on first run

```bash
git clone https://github.com/Alienbushman/whatsapp-archive.git
cd whatsapp-archive

# Drop your WhatsApp .txt exports here (gitignored — won't be committed)
mkdir -p sample-archive
cp /path/to/WhatsApp\ Chat\ with\ *.txt sample-archive/

# Bring everything up
docker compose up -d
```

**First-run timing** (~5–10 min total — not hung, just downloading):
- Docker image build: ~4 min (pip + npm dependency install, silent in compose logs)
- Ollama model pull: ~3–5 min (~3 GB: `qwen2.5:3b-instruct` 1.9 GB + `nomic-embed-text` 270 MB)
- The `ollama-init` sidecar does the pull once and exits; web is usable as soon as it's healthy

Open **http://localhost:8800** in your browser. Background scrapers and the
topic-clustering loop start automatically; the UI is usable immediately, and
enrichment progress appears as it completes.

### Offline / air-gap mode

If the client machine has no outbound internet for scraping article URLs out of
chats, disable the scrape loop:

```yaml
# in docker-compose.yml
BACKGROUND_SCRAPE: "false"
```

Note: you still need outbound during the initial build (pip/npm/Ollama
downloads). To run *fully* offline, build the image and pull the Ollama models
on a connected machine, then `docker save`/`docker load` and copy the model
volume across.

### Adding more chats later

Drop additional `.txt` files into `sample-archive/` — the `WATCH=true` loop
picks them up within 30s, no restart needed.

### Stopping / clean-up

```bash
docker compose down              # stop, keep DB + models
docker compose down -v           # also wipe Qdrant data + Ollama models (~3 GB)
```

### Rebuilding after code changes

```bash
bash scripts/rebuild-on-ticket-complete.sh
```

This rebuilds the frontend bundle, rebuilds the `web` image, and force-recreates
the container so it picks up new code reliably.

---

## Quick start (local Python — no Docker)

For development without containers. You'll need to run Qdrant and Ollama
yourself if you want vector search and the Ask panel — otherwise those features
will gracefully no-op.

**Prereqs:** Python 3.11+, Node 18+.

```bash
# Install
pip install -e ".[dev]"

# Build the frontend bundle (output goes into src/whatsapp_archive/static/)
cd frontend && npm install && npm run build && cd ..

# Run
python -m whatsapp_archive --archive-dir ./sample-archive
```

Opens `http://localhost:8000` automatically.

| Flag | Default | What it does |
|---|---|---|
| `--archive-dir PATH` | `sample-archive` | Directory with WhatsApp `.txt` exports |
| `--port PORT` | `8000` | Port to bind |
| `--no-browser` | off | Don't pop a browser tab |

Or use the installed CLI:

```bash
wa-archive --archive-dir ./sample-archive
```

### Frontend hot-reload (during UI dev)

```bash
# terminal 1 — backend (no browser, no auto-build)
python -m whatsapp_archive --no-browser

# terminal 2 — Vite dev server with HMR
cd frontend && npm run dev
```

Vite at `http://localhost:5173` proxies `/api` to the backend.

---

## Exporting a WhatsApp chat

On your phone:
1. Open the chat → **⋮ menu → More → Export chat**
2. Choose **Without media** (smaller; this app only reads the `.txt`)
3. Send the `.zip` or `.txt` to yourself, unzip if needed, drop into
   `sample-archive/`

The parser handles US-style `M/D/YY` timestamps, multi-line messages, system
events (`Ben created group "Gold"`), unknown senders (`+31 6 27831253:`), and
deleted-message markers.

---

## Feature tour

| Page | What it's for |
|---|---|
| **Chats** | Browse and search messages, two-pane layout with deep-link to a tweet's location |
| **Bookmarks** | Quick-saves — star any tweet or message |
| **Collections** | Curated, persistent buckets for ongoing research |
| **Research Bins** | Temporary scratchpads tied to a search session |
| **Topics** | LLM-clustered article topics — pin the ones you want preserved across re-clusterings |
| **Entities** | People, hashtags, mentions — auto-extracted, click any to filter |
| **Aggregations** | Counts of things over time |
| **Dashboards / Digests** | Roll-ups of activity across the archive |
| **Ask** | Natural-language Q&A over the archive via the local LLM |

### Environment variables (Docker)

All defined in `docker-compose.yml`. Toggle these to disable background work on
constrained machines:

```yaml
BACKGROUND_SCRAPE: "true"      # Fetch + unfurl article URLs
BACKGROUND_ENRICH: "true"      # Embeddings + entity extraction
BACKGROUND_CLUSTERING: "true"  # Topic clustering loop
BACKGROUND_OCR: "false"        # OCR images (off by default; image lacks tesseract)
```

OCR is disabled by default because the Docker image doesn't ship with
`tesseract`. To enable, add `tesseract-ocr` to the `apt-get install` line in
`Dockerfile` and set `BACKGROUND_OCR=true`.

---

## Privacy

`sample-archive/` is **gitignored** and treated as private input. Real names and
phone numbers will be in your exports — this app keeps them local but you still
need to be careful with derivative artifacts.

- ✅ Run locally, browse, share screenshots with PII redacted
- ❌ Don't paste raw chat contents into web searches, public gists, or third-party
  rendering services
- ❌ Don't commit parsed JSON, embeddings, OCR text, or fixtures derived from real
  exports without explicit redaction

The `.gitignore` blocks `sample-archive/`, `*.db`, `*.sqlite`, and `.env` files.

---

## Architecture (briefly)

- **Backend** — FastAPI app (`src/whatsapp_archive/`), SQLite for messages +
  metadata, Qdrant for vectors, Ollama for embeddings & LLM
- **Frontend** — React + Vite, built to `src/whatsapp_archive/static/`, served
  by the FastAPI app
- **Background loops** — scrape, enrich, OCR, cluster, embed; each gated by an
  env var and pollable from inside the container
- **Storage** — `articles.db` (SQLite) lives next to your `.txt` files inside
  `sample-archive/`; Qdrant + Ollama use named Docker volumes

See `CLAUDE.md` and `AGENTS.md` for the conventions used when AI agents work on
this repo.

---

## Troubleshooting

**Web container is healthy but the page won't load** — make sure you hit
`http://localhost:8800` (not 8000; 8000 is the in-container port).

**Ollama init keeps re-pulling models** — that's expected on first run. The
`ollama-init` container exits after the pull; check `docker compose logs
ollama-init`. The models live in the `whatsapp_archive_ollama_models` volume and
persist across `docker compose down`.

**Topic clustering looks stale** — click **🔄 Recluster** on the Topics page,
or watch `docker compose logs web | grep -i cluster`. Pinned topics are
preserved.

**OCR errors flooding logs** — `BACKGROUND_OCR` is `false` by default. If
you've enabled it without installing `tesseract-ocr` in the image, turn it back
off.

**Frontend changes don't appear after rebuild** — use
`scripts/rebuild-on-ticket-complete.sh`; plain `docker compose up -d` won't
force-recreate.
