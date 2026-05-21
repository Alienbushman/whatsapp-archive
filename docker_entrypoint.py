"""Container entrypoint — binds uvicorn to 0.0.0.0 so the host can reach it.

Reads config from env vars instead of CLI flags, since `python -m whatsapp_archive`
binds to 127.0.0.1 and opens a browser, neither of which we want inside a container.
"""

import logging
import os
from pathlib import Path

import uvicorn

from whatsapp_archive import create_app

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

archive_dir = Path(os.environ.get("ARCHIVE_DIR", "/app/data/incoming"))
ollama_url = os.environ.get("OLLAMA_URL", "http://ollama:11434")
port = int(os.environ.get("PORT", "8000"))
watch = os.environ.get("WATCH", "false").lower() in ("1", "true", "yes")
watch_interval = int(os.environ.get("WATCH_INTERVAL", "30"))

archive_dir.mkdir(parents=True, exist_ok=True)

app = create_app(
    archive_dir,
    ollama_url=ollama_url,
    watch=watch,
    watch_interval=watch_interval,
)

uvicorn.run(app, host="0.0.0.0", port=port)
