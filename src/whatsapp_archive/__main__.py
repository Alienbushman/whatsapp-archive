import argparse
import webbrowser
import uvicorn
from pathlib import Path
from whatsapp_archive import create_app


def main():
    parser = argparse.ArgumentParser(description="Browse WhatsApp chat archives in your browser.")
    parser.add_argument("--archive-dir", type=Path, default=Path("sample-archive"))
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--watch", action="store_true", help="Reload archives on file change")
    parser.add_argument("--watch-interval", type=int, default=30, metavar="SECS")
    args = parser.parse_args()

    app = create_app(
        args.archive_dir,
        ollama_url=args.ollama_url,
        watch=args.watch,
        watch_interval=args.watch_interval,
    )
    url = f"http://localhost:{args.port}"
    print(f"Serving archive from {args.archive_dir}  ->  {url}")
    if not args.no_browser:
        webbrowser.open(url)
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
