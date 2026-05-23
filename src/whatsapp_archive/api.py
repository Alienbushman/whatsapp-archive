import asyncio
import hashlib
import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Union

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .models import ChatExport, Message, SystemEvent
from .parser import parse_file
from .scrape.article import (
    BlockedPaywall,
    Gone404,
    ScrapedArticle,
    TransientError,
    UnsupportedMediaType,
    scrape_generic,
)
from .scrape.background import run_digest_loop, run_engagement_refresh_loop, run_enrich_loop, run_ocr_loop, run_scrape_loop, run_topic_clustering_loop, run_profile_refresh_loop
from .enrich.ollama import EnrichError, enrich_article
from .scrape.vector import ensure_collection, find_similar_articles, get_client, search_similar, upsert_article_vector
from .scrape.db import (
    get_archive_db_stats,
    get_article_by_id,
    get_media_ocr_for_article,
    get_article_by_tweet_id,
    get_article_by_url,
    get_articles_by_urls,
    get_replies_to_tweet,
    get_articles_with_entities,
    get_author_tweet_metas,
    get_enriched_articles_for_keyword,
    get_enrichment,
    get_tweets_by_author,
    get_tweets_by_hashtag,
    get_tweets_by_mention,
    keyword_search,
    keyword_search_with_snippet,
    list_authors,
    list_hashtags,
    list_mentions,
    list_top_authors_windowed,
    list_top_entities_windowed,
    list_top_hashtags_windowed,
    list_top_tweets_windowed,
    list_sentiment_rows,
    backfill_tickers,
    get_articles_for_export,
    get_articles_in_timerange,
    timeseries_tweets,
    timeseries_sentiment,
    open_db,
    search_enrichment_summary,
    search_tweet_meta_blob,
    upsert_article,
    upsert_enrichment,
    upsert_bookmark,
    delete_bookmark,
    get_bookmark,
    get_bookmarks,
    upsert_message_bookmark,
    delete_message_bookmark,
    get_message_bookmark,
    get_message_bookmarks,
    create_collection,
    get_collections,
    get_collection,
    add_collection_item,
    remove_collection_item,
    delete_collection,
    get_collection_items,
    create_research_bin,
    get_research_bins,
    get_research_bin,
    get_research_bin_items,
    add_research_bin_item,
    delete_research_bin_item,
    promote_research_bin_to_collection,
    delete_research_bin,
    update_research_bin,
    get_cooccurrence,
    get_dossier_cache,
    save_dossier_cache,
    get_entity_home_cards,
    get_entity_graph,
    get_articles_for_entity,
)
from .research.dossier import build_entity_dossier

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(name: str) -> str:
    return _SLUG_RE.sub("-", name.lower()).strip("-")


def _export_bundle(
    label: str,
    slug: str,
    msg_hits: list[dict],
    article_hits: list[dict],
    fmt: str,
    url_to_article: "dict[str, dict] | None" = None,
) -> StreamingResponse:
    import csv as _csv
    import io
    import json as _json

    if url_to_article is None:
        url_to_article = {a["url"]: a for a in article_hits}
    today = datetime.utcnow().strftime("%Y%m%d")
    filename = f"{slug}-{today}"
    generated = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    if fmt == "json":
        data = _json.dumps(
            {"label": label, "messages": msg_hits, "articles": article_hits},
            default=str,
        )
        return StreamingResponse(
            io.BytesIO(data.encode()),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename={filename}.json"},
        )

    if fmt == "csv":
        buf = io.StringIO()
        writer = _csv.DictWriter(
            buf,
            fieldnames=["timestamp", "chat", "sender", "body", "url", "article_title", "article_summary"],
        )
        writer.writeheader()
        for m in msg_hits:
            url = next((u for u in url_to_article if u in (m.get("body") or "")), "")
            art = url_to_article.get(url, {})
            writer.writerow({
                "timestamp": m.get("ts_str", ""),
                "chat": m.get("chat_name", ""),
                "sender": m.get("sender", ""),
                "body": m.get("body", ""),
                "url": url,
                "article_title": art.get("title", ""),
                "article_summary": art.get("summary", ""),
            })
        for a in article_hits:
            writer.writerow({
                "timestamp": a.get("published_at", ""),
                "chat": "",
                "sender": "",
                "body": "",
                "url": a.get("url", ""),
                "article_title": a.get("title", ""),
                "article_summary": a.get("summary", "") or (a.get("raw_text") or "")[:300],
            })
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}.csv"},
        )

    # Markdown (default)
    lines: list[str] = [
        f"# Export: {label}",
        f"_Generated {generated} · "
        f"{len(msg_hits)} message{'s' if len(msg_hits) != 1 else ''} · "
        f"{len(article_hits)} article{'s' if len(article_hits) != 1 else ''}_",
        "",
    ]
    all_items: list[dict] = []
    for m in msg_hits:
        all_items.append({"kind": "message", "ts": m["ts"], **m})
    for a in article_hits:
        if a.get("published_at"):
            try:
                a_ts = datetime.fromisoformat(a["published_at"][:19])
            except ValueError:
                a_ts = datetime.utcnow()
        else:
            a_ts = datetime.utcnow()
        all_items.append({"kind": "article", "ts": a_ts, **a})
    all_items.sort(key=lambda x: x["ts"])
    for item in all_items:
        lines.append("---")
        if item["kind"] == "message":
            lines.append(
                f"## {item['ts'].strftime('%Y-%m-%d %H:%M')} · {item.get('chat_name', '')} · {item.get('sender', '')}"
            )
            lines.append("")
            lines.append(item.get("body", ""))
            url_in_body = next((u for u in url_to_article if u in (item.get("body") or "")), None)
            if url_in_body:
                art = url_to_article[url_in_body]
                summary = art.get("summary") or (art.get("raw_text") or "")[:300]
                lines.append("")
                lines.append(f"[Source link]({url_in_body}) — *{art.get('title', '')}*")
                lines.append(f"> {summary}")
        else:
            lines.append(f"## {item['ts'].strftime('%Y-%m-%d %H:%M')} · Article · {item.get('title', '')}")
            lines.append("")
            lines.append(f"URL: {item.get('url', '')}")
            summary = item.get("summary") or (item.get("raw_text") or "")[:300]
            if summary:
                lines.append("")
                lines.append(f"> {summary}")
        lines.append("")
    md = "\n".join(lines)
    return StreamingResponse(
        io.BytesIO(md.encode("utf-8")),
        media_type="text/markdown",
        headers={"Content-Disposition": f"attachment; filename={filename}.md"},
    )


def _entry_dict(entry: Union[Message, SystemEvent]) -> dict[str, Any]:
    if isinstance(entry, Message):
        return {
            "type": "message",
            "timestamp": entry.timestamp.isoformat(),
            "sender": entry.sender,
            "body": entry.body,
            "is_deleted": entry.is_deleted,
        }
    return {
        "type": "system",
        "timestamp": entry.timestamp.isoformat(),
        "text": entry.text,
    }


class _FetchRequest(BaseModel):
    url: str


class _BulkStatusRequest(BaseModel):
    urls: list[str]


class _AskRequest(BaseModel):
    question: str
    chat_id: str | None = None
    k: int = 12


def _row_to_dict(row) -> dict:
    if not row:
        return {}
    result = dict(row)
    # Tweet metadata is persisted as a JSON blob. Surface BOTH the raw JSON string
    # (key `tweet_meta`) and the parsed object (key `tweet`) so consumers expecting
    # either shape work. Earlier the helper popped `tweet_meta`, which silently broke
    # frontends that looked it up by that name (e.g. Q15 similar rendering).
    raw_meta = result.get("tweet_meta")
    if raw_meta:
        import json as _json
        try:
            result["tweet"] = _json.loads(raw_meta)
        except (ValueError, TypeError):
            result["tweet"] = None
    else:
        result["tweet"] = None
    return result


def create_app(
    archive_dir: Path,
    ollama_url: str = "http://localhost:11434",
    watch: bool = False,
    watch_interval: int = 30,
) -> FastAPI:
    app = FastAPI(title="whatsapp-archive")

    # Load all archives eagerly at app creation time
    chats: dict[str, ChatExport] = {}
    chat_names: dict[str, str] = {}
    _mtimes: dict[str, float] = {}
    _fingerprints: dict[str, str] = {}  # sha256(file bytes) → chat_id; prevents duplicate uploads
    for _path in sorted(archive_dir.glob("*.txt")):
        _cid = _slug(_path.stem)
        _bytes = _path.read_bytes()
        chats[_cid] = parse_file(_path)
        chat_names[_cid] = _path.stem
        _mtimes[_cid] = _path.stat().st_mtime
        _fingerprints[hashlib.sha256(_bytes).hexdigest()] = _cid
    _total = sum(len(e.entries) for e in chats.values())
    logger.info("Loaded %d chats, %d total entries from %s", len(chats), _total, archive_dir)

    # Captured at startup; used by the watcher thread to schedule scrape coroutines
    # for newly-detected files. Without this, dropping a .txt into the watched
    # folder produced a visible chat with orphaned URLs (no scrape → no enrich →
    # no embed → never indexed). Bug #40.
    _main_loop_holder: dict[str, Any] = {"loop": None}

    if watch:
        def _watcher():
            while True:
                time.sleep(watch_interval)
                current: dict[str, Path] = {
                    _slug(_p.stem): _p for _p in archive_dir.glob("*.txt")
                }
                # Detect and remove deleted files
                removed = set(_mtimes.keys()) - set(current.keys())
                for _cid in removed:
                    chats.pop(_cid, None)
                    chat_names.pop(_cid, None)
                    _mtimes.pop(_cid, None)
                    for _fp in [fp for fp, cid in _fingerprints.items() if cid == _cid]:
                        _fingerprints.pop(_fp, None)
                if removed:
                    logger.info("[watch] removed %d chat(s) (files deleted: %s)",
                                len(removed), ", ".join(removed))
                # Detect new or modified files
                for _cid, _p in current.items():
                    _mtime = _p.stat().st_mtime
                    if _mtimes.get(_cid) != _mtime:
                        is_new_chat = _cid not in _mtimes
                        _bytes = _p.read_bytes()
                        chats[_cid] = parse_file(_p)
                        chat_names[_cid] = _p.stem
                        _mtimes[_cid] = _mtime
                        _fingerprints[hashlib.sha256(_bytes).hexdigest()] = _cid
                        logger.info("Reloaded %s", _p.name)
                        # Trigger the full downstream pipeline (scrape → enrich loop
                        # picks up automatically → embed inside enrich) for any URLs
                        # in the new/updated chat.
                        if _env_flag("BACKGROUND_SCRAPE", "true"):
                            new_urls = [lnk.url for lnk in chats[_cid].links]
                            loop = _main_loop_holder.get("loop")
                            if new_urls and loop is not None:
                                try:
                                    asyncio.run_coroutine_threadsafe(
                                        run_scrape_loop(
                                            new_urls,
                                            db,
                                            {"phase": "pending"},
                                            concurrency=int(os.environ.get("SCRAPE_CONCURRENCY", "3")),
                                        ),
                                        loop,
                                    )
                                    logger.info(
                                        "[watch] scheduled scrape of %d url(s) from %s chat %s",
                                        len(new_urls),
                                        "new" if is_new_chat else "modified",
                                        _cid,
                                    )
                                except Exception as exc:
                                    logger.warning("[watch] failed to schedule scrape: %s", exc)

        threading.Thread(target=_watcher, daemon=True, name="archive-watcher").start()
        logger.info("File watcher started (interval=%ds)", watch_interval)

    db = open_db(archive_dir)

    try:
        qdrant = get_client()
        ensure_collection(qdrant)
    except Exception:
        qdrant = None
        logger.warning("Qdrant unavailable — semantic search disabled")

    # ── Background scrape + enrich + OCR + digest + engagement progress trackers
    scrape_progress: dict[str, Any] = {"phase": "pending"}
    enrich_progress: dict[str, Any] = {"phase": "pending"}
    ocr_progress: dict[str, Any] = {"phase": "pending"}
    digest_progress: dict[str, Any] = {"phase": "pending"}
    engagement_refresh_progress: dict[str, Any] = {"phase": "pending"}
    topic_clustering_progress: dict[str, Any] = {"phase": "pending"}
    profile_refresh_progress: dict[str, Any] = {"phase": "pending"}

    def _env_flag(name: str, default: str) -> bool:
        return os.environ.get(name, default).lower() in ("true", "1", "yes")

    @app.on_event("startup")
    async def _start_background_tasks() -> None:
        # Stash the running event loop so the watcher thread (started before this
        # handler runs) can schedule coroutines via run_coroutine_threadsafe().
        _main_loop_holder["loop"] = asyncio.get_running_loop()
        if _env_flag("BACKGROUND_SCRAPE", "true"):
            urls: list[str] = []
            for export in chats.values():
                for link in export.links:
                    urls.append(link.url)
            logger.info("Starting background scrape of %d link refs", len(urls))
            asyncio.create_task(
                run_scrape_loop(
                    urls,
                    db,
                    scrape_progress,
                    concurrency=int(os.environ.get("SCRAPE_CONCURRENCY", "3")),
                )
            )
        else:
            scrape_progress["phase"] = "disabled"

        if _env_flag("BACKGROUND_ENRICH", "true"):
            logger.info("Starting background enrich loop against %s", ollama_url)
            asyncio.create_task(
                run_enrich_loop(
                    db,
                    ollama_url,
                    enrich_progress,
                    concurrency=int(os.environ.get("ENRICH_CONCURRENCY", "1")),
                    poll_interval=int(os.environ.get("ENRICH_POLL_INTERVAL", "60")),
                    qdrant=qdrant,
                )
            )
        else:
            enrich_progress["phase"] = "disabled"

        if _env_flag("BACKGROUND_OCR", "true"):
            logger.info("Starting background OCR loop")
            asyncio.create_task(
                run_ocr_loop(
                    db,
                    ocr_progress,
                    concurrency=int(os.environ.get("OCR_CONCURRENCY", "2")),
                    poll_interval=int(os.environ.get("OCR_POLL_INTERVAL", "60")),
                )
            )
        else:
            ocr_progress["phase"] = "disabled"

        if _env_flag("BACKGROUND_DIGESTS", "true"):
            logger.info("Starting background digest loop")
            asyncio.create_task(
                run_digest_loop(
                    db,
                    ollama_url,
                    digest_progress,
                    chats=chats,
                    poll_interval=int(os.environ.get("DIGEST_POLL_INTERVAL", "600")),
                )
            )
        else:
            digest_progress["phase"] = "disabled"

        if _env_flag("BACKGROUND_ENGAGEMENT_REFRESH", "true"):
            logger.info("Starting background engagement refresh loop")
            asyncio.create_task(
                run_engagement_refresh_loop(
                    db,
                    engagement_refresh_progress,
                    concurrency=2,
                )
            )
        else:
            engagement_refresh_progress["phase"] = "disabled"

        if _env_flag("BACKGROUND_CLUSTERING", "false"):
            logger.info("Starting background topic clustering loop")
            asyncio.create_task(
                run_topic_clustering_loop(
                    db,
                    qdrant,
                    ollama_url,
                    topic_clustering_progress,
                )
            )
        else:
            topic_clustering_progress["phase"] = "disabled"

        if _env_flag("BACKGROUND_PROFILES", "false"):
            logger.info("Starting background author profile refresh loop")
            asyncio.create_task(run_profile_refresh_loop(db, profile_refresh_progress))
        else:
            profile_refresh_progress["phase"] = "disabled"

    # ── API routes ────────────────────────────────────────────────────────────

    @app.get("/api/scrape/status")
    def get_scrape_status() -> dict:
        return dict(scrape_progress)

    @app.get("/api/enrich/status")
    def get_enrich_status() -> dict:
        return dict(enrich_progress)

    @app.get("/api/ocr/status")
    def get_ocr_status() -> dict:
        return dict(ocr_progress)

    @app.get("/api/digests/status")
    def get_digest_status() -> dict:
        return dict(digest_progress)

    @app.get("/api/engagement_refresh/status")
    def get_engagement_refresh_status() -> dict:
        return dict(engagement_refresh_progress)

    @app.post("/api/articles/{article_id}/refresh")
    def article_refresh(article_id: int) -> dict:
        from .scrape.background import _refresh_one_sync
        result = _refresh_one_sync(article_id, db)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error") or result.get("kind", "refresh failed"))
        row = db.execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="article not found")
        d = dict(row)
        if d.get("tweet_meta") and isinstance(d["tweet_meta"], str):
            try:
                d["tweet_meta"] = json.loads(d["tweet_meta"])
            except (ValueError, TypeError):
                pass
        return d

    # ── Scrape recovery admin (I18) ──────────────────────────────────────────

    @app.get("/api/articles/by_status")
    def articles_by_status(
        status: str = Query("failed"),
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        order: str = Query("fetched_at"),
    ) -> dict:
        allowed_order = {"fetched_at", "url", "id"}
        order_col = order if order in allowed_order else "fetched_at"
        offset = (page - 1) * page_size
        total = db.execute("SELECT COUNT(*) AS c FROM articles WHERE status=?", (status,)).fetchone()["c"]
        rows = db.execute(
            f"SELECT id, url, status, error, fetched_at, title FROM articles"
            f" WHERE status=? ORDER BY {order_col} DESC LIMIT ? OFFSET ?",
            (status, page_size, offset),
        ).fetchall()
        return {
            "status": status,
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": [dict(r) for r in rows],
        }

    @app.get("/api/articles/scrape_errors/summary")
    def scrape_errors_summary(limit: int = Query(20, ge=1, le=100)) -> list[dict]:
        rows = db.execute(
            """
            SELECT SUBSTR(COALESCE(error, 'unknown'), 1, 120) AS error_signature,
                   COUNT(*) AS count,
                   GROUP_CONCAT(url, '|||') AS sample_urls_raw
            FROM articles
            WHERE status IN ('failed', 'blocked')
            GROUP BY error_signature
            ORDER BY count DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        result = []
        for r in rows:
            sample = (r["sample_urls_raw"] or "").split("|||")[:3]
            result.append({"error_signature": r["error_signature"], "count": r["count"], "sample_urls": sample})
        return result

    @app.post("/api/articles/{article_id}/retry")
    async def article_retry(article_id: int) -> dict:
        row = db.execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="article not found")
        url = row["url"]
        try:
            result: ScrapedArticle = await asyncio.to_thread(scrape_generic, url)
        except BlockedPaywall as exc:
            upsert_article(db, url, "blocked", error=str(exc))
            raise HTTPException(status_code=451, detail=str(exc))
        except Gone404 as exc:
            upsert_article(db, url, "failed", error=str(exc))
            raise HTTPException(status_code=404, detail="Gone")
        except (TransientError, UnsupportedMediaType) as exc:
            upsert_article(db, url, "failed", error=str(exc))
            raise HTTPException(status_code=502, detail=str(exc))
        except Exception as exc:
            upsert_article(db, url, "failed", error=str(exc))
            raise HTTPException(status_code=500, detail=str(exc))

        row_id = upsert_article(
            db, result.url, "ok",
            title=result.title, author=result.author,
            published_at=result.published_at.isoformat() if result.published_at else None,
            raw_text=result.raw_text, og_image=result.og_image_url,
            scrape_method=result.scrape_method,
            fetched_at=datetime.utcnow().isoformat(),
            tweet_meta=json.dumps(result.tweet) if result.tweet else None,
        )
        return _row_to_dict(get_article_by_id(db, row_id))

    class RetryBulkBody(BaseModel):
        ids: list[int]

    @app.post("/api/articles/retry_bulk")
    async def article_retry_bulk(body: RetryBulkBody) -> dict:
        ids = body.ids[:200]  # cap at 200
        rows = db.execute(
            f"SELECT url FROM articles WHERE id IN ({','.join('?' * len(ids))})",
            ids,
        ).fetchall()
        urls = [r["url"] for r in rows]
        # Enqueue as a background task; don't block the response
        asyncio.create_task(
            run_scrape_loop(urls, db, scrape_progress)
        )
        return {"enqueued": len(urls)}

    class MarkBlockedBody(BaseModel):
        reason: str = "blocked_manual"

    @app.post("/api/articles/{article_id}/mark_blocked")
    def article_mark_blocked(article_id: int, body: MarkBlockedBody) -> dict:
        row = db.execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="article not found")
        db.execute(
            "UPDATE articles SET status='blocked', error=? WHERE id=?",
            (body.reason, article_id),
        )
        db.commit()
        return _row_to_dict(db.execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchone())

    # ── Dashboard overview (I6) ───────────────────────────────────────────────

    @app.get("/api/dashboard")
    def get_dashboard() -> dict:
        import json as _json

        stats = get_archive_db_stats(db)

        # Message counts and date range from in-memory chats
        total_messages = 0
        earliest: datetime | None = None
        latest_msg: datetime | None = None
        most_active_chat_id: str | None = None
        most_active_count = 0
        for cid, export in chats.items():
            count = 0
            for entry in export.entries:
                if isinstance(entry, Message):
                    count += 1
                    if earliest is None or entry.timestamp < earliest:
                        earliest = entry.timestamp
                    if latest_msg is None or entry.timestamp > latest_msg:
                        latest_msg = entry.timestamp
            total_messages += count
            if count > most_active_count:
                most_active_count = count
                most_active_chat_id = cid

        # Top 5 authors and hashtags
        top_authors = [
            {"handle": r["handle"], "tweet_count": r["tweet_count"]}
            for r in list_top_authors_windowed(db, limit=5)
        ]
        top_hashtags = [
            {"tag": r["tag"], "count": r["count"]}
            for r in list_top_hashtags_windowed(db, limit=5)
        ]

        # Latest tweet
        latest_tweet = None
        if stats["latest_tweet_row"]:
            latest_tweet = _row_to_dict(stats["latest_tweet_row"])

        ok_count = stats["total_articles"]
        total_articles = ok_count + stats["pending_scrape"] + stats["blocked_scrape"] + stats["failed_scrape"]

        return {
            "total_chats": len(chats),
            "total_messages": total_messages,
            "total_articles": ok_count,
            "total_tweets": stats["total_tweets"],
            "distinct_authors": stats["distinct_authors"],
            "distinct_hashtags": stats["distinct_hashtags"],
            "date_range": {
                "from": earliest.strftime("%Y-%m-%d") if earliest else None,
                "to": latest_msg.strftime("%Y-%m-%d") if latest_msg else None,
            },
            "most_active_chat": {
                "id": most_active_chat_id,
                "name": chat_names.get(most_active_chat_id, "") if most_active_chat_id else None,
                "message_count": most_active_count,
            } if most_active_chat_id else None,
            "scrape": {
                "ok": ok_count,
                "pending": stats["pending_scrape"],
                "blocked": stats["blocked_scrape"],
                "failed": stats["failed_scrape"],
                "total": total_articles,
            },
            "enrich": {
                "enriched": stats["enriched"],
                "total": ok_count,
            },
            "top_authors": top_authors,
            "top_hashtags": top_hashtags,
            "latest_tweet": latest_tweet,
        }

    @app.get("/api/chats")
    def list_chats() -> list[dict]:
        return [
            {
                "id": cid,
                "name": chat_names[cid],
                "message_count": sum(1 for e in export.entries if isinstance(e, Message)),
            }
            for cid, export in chats.items()
        ]

    _MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB

    @app.post("/api/chats/upload")
    async def upload_chat(file: UploadFile = File(...)) -> dict:
        filename = file.filename or ""
        if not filename.endswith(".txt"):
            raise HTTPException(status_code=400, detail="Only .txt files are accepted")

        content_bytes = await file.read()
        if len(content_bytes) > _MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="File too large (>10 MB)")
        if len(content_bytes) == 0:
            raise HTTPException(status_code=400, detail="Empty file")

        safe_name = Path(filename).name
        if not safe_name.endswith(".txt") or safe_name == ".txt":
            raise HTTPException(status_code=400, detail="Invalid filename")

        # Fingerprint dedup: same bytes → same chat, return the existing one
        fp = hashlib.sha256(content_bytes).hexdigest()
        if fp in _fingerprints:
            existing_cid = _fingerprints[fp]
            existing_export = chats.get(existing_cid)
            msg_count = (
                sum(1 for e in existing_export.entries if isinstance(e, Message))
                if existing_export else 0
            )
            logger.info("Upload dedup: %s matches existing chat %s", filename, existing_cid)
            return {
                "id": existing_cid,
                "name": chat_names.get(existing_cid, existing_cid),
                "message_count": msg_count,
                "link_count": len(existing_export.links) if existing_export else 0,
                "replaced": False,
            }

        dest = archive_dir / safe_name
        dest.write_bytes(content_bytes)

        try:
            export = parse_file(dest)
        except Exception as exc:
            try:
                dest.unlink()
            except FileNotFoundError:
                pass
            raise HTTPException(status_code=400, detail=f"Parse failed: {exc}") from exc

        cid = _slug(dest.stem)
        chats[cid] = export
        chat_names[cid] = dest.stem
        _mtimes[cid] = dest.stat().st_mtime
        _fingerprints[fp] = cid

        message_count = sum(1 for e in export.entries if isinstance(e, Message))

        new_urls = [lnk.url for lnk in export.links]
        if new_urls and _env_flag("BACKGROUND_SCRAPE", "true"):
            asyncio.create_task(
                run_scrape_loop(
                    new_urls,
                    db,
                    {"phase": "pending"},
                    concurrency=int(os.environ.get("SCRAPE_CONCURRENCY", "3")),
                )
            )

        logger.info("Uploaded chat %s (%d messages, %d links)", dest.stem, message_count, len(export.links))
        return {
            "id": cid,
            "name": dest.stem,
            "message_count": message_count,
            "link_count": len(export.links),
            "replaced": True,
        }

    @app.delete("/api/chats/{chat_id}")
    def delete_chat(
        chat_id: str,
        remove_file: bool = Query(False, description="Also delete the source .txt file"),
    ) -> dict:
        if chat_id not in chats:
            raise HTTPException(status_code=404, detail="Chat not found")
        export = chats.pop(chat_id)
        chat_names.pop(chat_id, None)
        _mtimes.pop(chat_id, None)
        for _fp in [fp for fp, cid in _fingerprints.items() if cid == chat_id]:
            _fingerprints.pop(_fp, None)
        msg_count = sum(1 for e in export.entries if isinstance(e, Message))
        if remove_file and export.source_file.exists():
            export.source_file.unlink()
            logger.info("Deleted chat %s and source file (%d messages)", chat_id, msg_count)
        else:
            logger.info("Deleted chat %s from memory (%d messages)", chat_id, msg_count)
        return {"deleted": True, "chat_id": chat_id, "removed_messages_count": msg_count}

    @app.get("/api/chats/{chat_id}/messages")
    def get_messages(
        chat_id: str,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=500),
        q: str = Query(""),
    ) -> dict:
        export = chats.get(chat_id)
        if export is None:
            raise HTTPException(status_code=404, detail="Chat not found")

        entries = export.entries
        if q:
            q_lower = q.lower()
            entries = [
                e for e in entries
                if (isinstance(e, Message) and q_lower in e.body.lower())
                or (isinstance(e, SystemEvent) and q_lower in e.text.lower())
            ]

        total = len(entries)
        start = (page - 1) * page_size
        page_entries = entries[start : start + page_size]

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "entries": [_entry_dict(e) for e in page_entries],
        }

    @app.get("/api/chats/{chat_id}/links")
    def get_links(
        chat_id: str,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=500),
        q: str = Query(""),
    ) -> dict:
        export = chats.get(chat_id)
        if export is None:
            raise HTTPException(status_code=404, detail="Chat not found")
        links = export.links
        if q:
            q_lower = q.lower()
            links = [lnk for lnk in links if q_lower in lnk.url.lower()]
        total = len(links)
        start = (page - 1) * page_size
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "links": [lnk.model_dump(mode="json") for lnk in links[start: start + page_size]],
        }

    @app.get("/api/links")
    def search_links(
        q: str = Query(..., min_length=1),
        chat_id: str = Query(""),
    ) -> list[dict]:
        q_lower = q.lower()
        results: list[dict] = []
        targets = {chat_id: chats[chat_id]} if chat_id and chat_id in chats else chats
        for cid, export in targets.items():
            for lnk in export.links:
                if q_lower in lnk.url.lower():
                    results.append({
                        "chat_id": cid,
                        "chat_name": chat_names[cid],
                        **lnk.model_dump(mode="json"),
                    })
                    if len(results) >= 200:
                        return results
        return results

    @app.get("/api/search")
    def search(
        q: str = Query(..., min_length=1),
        chat_id: str = Query(""),
        kind: str = Query("all"),
        fuzzy_fallback: bool = Query(True),
        from_date: str = Query(""),
        to_date: str = Query(""),
        author: str = Query(""),
        hashtag: str = Query(""),
        mention: str = Query(""),
        min_likes: int = Query(0, ge=0),
        min_retweets: int = Query(0, ge=0),
        has_media: int = Query(0),
        sentiment: str = Query(""),
        sender: str = Query(""),
    ) -> dict:
        import json as _json

        # Accept both singular ("article") and plural ("articles") forms — the
        # fuzzy_search sibling uses plurals and many frontends mix them up.
        kind = {"articles": "article", "messages": "message", "chats": "chat"}.get(kind, kind)

        filters_applied: dict = {}
        if from_date: filters_applied["from"] = from_date
        if to_date: filters_applied["to"] = to_date
        if author: filters_applied["author"] = author
        if hashtag: filters_applied["hashtag"] = hashtag
        if mention: filters_applied["mention"] = mention
        if min_likes: filters_applied["min_likes"] = min_likes
        if min_retweets: filters_applied["min_retweets"] = min_retweets
        if has_media: filters_applied["has_media"] = True
        if sentiment: filters_applied["sentiment"] = sentiment
        if sender: filters_applied["sender"] = sender

        def _parse_meta(row) -> dict:
            try:
                return _json.loads(row["tweet_meta"] or "{}") or {}
            except (ValueError, TypeError):
                return {}

        def _passes_article_filters(row) -> bool:
            if from_date or to_date:
                d = (row["published_at"] or row["fetched_at"] or "")[:10]
                if from_date and d and d < from_date:
                    return False
                if to_date and d and d > to_date:
                    return False
            if author or hashtag or mention or min_likes or min_retweets or has_media:
                meta = _parse_meta(row)
                if author and meta.get("author_handle", "").lower() != author.lower():
                    return False
                if hashtag and hashtag.lower() not in [h.lower() for h in (meta.get("hashtags") or [])]:
                    return False
                if mention and mention.lower() not in [m.lower() for m in (meta.get("mentioned_handles") or [])]:
                    return False
                if min_likes and int(meta.get("favorite_count") or 0) < min_likes:
                    return False
                if min_retweets and int(meta.get("retweet_count") or 0) < min_retweets:
                    return False
                if has_media and not (meta.get("media_urls") or []):
                    return False
            return True

        q_lower = q.lower()
        results: list[dict] = []

        if kind in ("message", "all"):
            targets = {chat_id: chats[chat_id]} if chat_id and chat_id in chats else chats
            for cid, export in targets.items():
                for entry in export.entries:
                    text = entry.body if isinstance(entry, Message) else entry.text
                    if q_lower in text.lower():
                        if isinstance(entry, Message):
                            d = entry.timestamp.date().isoformat()
                            if from_date and d < from_date:
                                continue
                            if to_date and d > to_date:
                                continue
                            if sender and sender.lower() not in entry.sender.lower():
                                continue
                        count = text.lower().count(q_lower)
                        results.append({
                            "kind": "message",
                            "chat_id": cid,
                            "chat_name": chat_names[cid],
                            "entry": _entry_dict(entry),
                            "score": count * 0.5,
                        })
                        if len(results) >= 200:
                            break

        if kind in ("article", "all"):
            # Build a URL→(chat_id, chat_name) index from the current loaded chats so
            # article hits can be attributed back to the chat that shared them.
            url_to_chat: dict[str, tuple[str, str]] = {}
            for cid, export in chats.items():
                for lnk in export.links:
                    if lnk.url not in url_to_chat:
                        url_to_chat[lnk.url] = (cid, chat_names[cid])

            seen_article_ids: set[int] = set()

            def _match_source(url: str, match_field: str) -> str:
                is_tweet = "x.com/" in url or "twitter.com/" in url
                if match_field == "meta":
                    return "tweet_meta"
                if match_field == "title":
                    return "tweet_title" if is_tweet else "article_title"
                if match_field == "body":
                    return "tweet_body" if is_tweet else "article_body"
                return match_field

            # #hashtag / @mention / $ticker queries cause FTS5 syntax errors; handle
            # them first via a LIKE scan on tweet_search_blob.
            if q.startswith("#") or q.startswith("@") or q.startswith("$"):
                try:
                    for row in search_tweet_meta_blob(db, q, 50):
                        if not _passes_article_filters(row):
                            continue
                        aid = row["id"]
                        if aid in seen_article_ids:
                            continue
                        seen_article_ids.add(aid)
                        blob = row["tweet_search_blob"] or ""
                        idx = blob.lower().find(q_lower)
                        raw_snip = blob[max(0, idx - 20): idx + len(q) + 40] if idx >= 0 else blob[:60]
                        attr = url_to_chat.get(row["url"])
                        results.append({
                            "kind": "article",
                            "article_id": aid,
                            "url": row["url"],
                            "title": row["title"],
                            "tweet_meta": row["tweet_meta"],
                            "snippet": f"<mark>{raw_snip}</mark>" if raw_snip else "",
                            "match_field": "meta",
                            "match_source": "tweet_meta",
                            "chat_id": attr[0] if attr else None,
                            "chat_name": attr[1] if attr else None,
                            "score": 1.0,
                        })
                except Exception:
                    pass

            try:
                fts_rows = keyword_search_with_snippet(db, q, 50)
                for row in fts_rows:
                    if not _passes_article_filters(row):
                        continue
                    aid = row["id"]
                    seen_article_ids.add(aid)
                    meta_snip = row["meta_snippet"] or ""
                    body_snip = row["body_snippet"] or ""
                    title_snip = row["title_snippet"] or ""
                    if "<mark>" in meta_snip:
                        snippet, match_field = meta_snip, "meta"
                    elif "<mark>" in body_snip:
                        snippet, match_field = body_snip, "body"
                    elif "<mark>" in title_snip:
                        snippet, match_field = title_snip, "title"
                    else:
                        snippet, match_field = body_snip or title_snip or meta_snip, "body"
                    attr = url_to_chat.get(row["url"])
                    results.append({
                        "kind": "article",
                        "article_id": aid,
                        "url": row["url"],
                        "title": row["title"],
                        "tweet_meta": row["tweet_meta"],
                        "snippet": snippet,
                        "match_field": match_field,
                        "match_source": _match_source(row["url"] or "", match_field),
                        "chat_id": attr[0] if attr else None,
                        "chat_name": attr[1] if attr else None,
                        "score": -row["score"],
                    })
                sum_rows = search_enrichment_summary(db, q_lower, 30)
                for row in sum_rows:
                    if row["id"] in seen_article_ids:
                        continue
                    if not _passes_article_filters(row):
                        continue
                    seen_article_ids.add(row["id"])
                    summary = row["summary"] or ""
                    idx = summary.lower().find(q_lower)
                    snippet = "…" + summary[max(0, idx - 60): idx + 120] + "…" if idx >= 0 else summary[:180]
                    attr = url_to_chat.get(row["url"])
                    results.append({
                        "kind": "article",
                        "article_id": row["id"],
                        "url": row["url"],
                        "title": row["title"],
                        "tweet_meta": row["tweet_meta"],
                        "snippet": snippet,
                        "match_field": "summary",
                        "match_source": "article_summary",
                        "chat_id": attr[0] if attr else None,
                        "chat_name": attr[1] if attr else None,
                        "score": 0.3,
                    })
            except Exception:
                pass

            # Sentiment post-filter: batch-load enrichments for article hits
            if sentiment and results:
                article_ids = {r["article_id"] for r in results if r.get("kind") == "article" and r.get("article_id")}
                matching_ids: set[int] = set()
                for aid in article_ids:
                    enr = get_enrichment(db, aid)
                    if enr and (enr["sentiment"] or "") == sentiment:
                        matching_ids.add(aid)
                results = [
                    r for r in results
                    if r.get("kind") != "article" or r.get("article_id") in matching_ids
                ]

        did_you_mean: list[dict] | None = None
        if fuzzy_fallback and not results and kind in ("message", "all"):
            try:
                from rapidfuzz import process, fuzz
                chat_name_list = list(chat_names.values())
                fuzzy_hits = process.extract(q, chat_name_list, scorer=fuzz.WRatio, limit=3, score_cutoff=60)
                if fuzzy_hits:
                    name_to_id = {v: k for k, v in chat_names.items()}
                    did_you_mean = [
                        {"chat_id": name_to_id.get(name, ""), "chat_name": name, "score": score}
                        for name, score, _ in fuzzy_hits
                    ]
            except ImportError:
                pass

        results.sort(key=lambda r: -r.get("score", 0))
        return {
            "results": results[:200],
            "did_you_mean": did_you_mean,
            "filters_applied": filters_applied if filters_applied else None,
        }

    # ── Article scraper endpoints ─────────────────────────────────────────────

    @app.post("/api/articles/fetch")
    async def fetch_article(req: _FetchRequest) -> dict:
        existing = get_article_by_url(db, req.url)
        if existing and existing["status"] == "ok":
            return _row_to_dict(existing)

        try:
            result: ScrapedArticle = await asyncio.to_thread(scrape_generic, req.url)
        except BlockedPaywall as exc:
            row_id = upsert_article(db, req.url, "blocked", error=str(exc))
            raise HTTPException(status_code=451, detail=str(exc))
        except Gone404 as exc:
            upsert_article(db, req.url, "failed", error=str(exc))
            raise HTTPException(status_code=404, detail="Article not found at URL")
        except (TransientError, UnsupportedMediaType) as exc:
            upsert_article(db, req.url, "failed", error=str(exc))
            raise HTTPException(status_code=502, detail=str(exc))

        import json as _json
        row_id = upsert_article(
            db,
            result.url,
            "ok",
            title=result.title,
            author=result.author,
            published_at=result.published_at.isoformat() if result.published_at else None,
            raw_text=result.raw_text,
            og_image=result.og_image_url,
            scrape_method=result.scrape_method,
            fetched_at=datetime.utcnow().isoformat(),
            tweet_meta=_json.dumps(result.tweet) if result.tweet else None,
        )
        return _row_to_dict(get_article_by_id(db, row_id))

    @app.get("/api/articles")
    def get_article_by_url_endpoint(
        url: str = Query(..., min_length=1),
        include_enrichment: int = Query(0),
    ) -> dict:
        row = get_article_by_url(db, url)
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        result = _row_to_dict(row)
        if include_enrichment:
            import json as _json
            enrich_row = get_enrichment(db, row["id"])
            if enrich_row:
                enrich_data = _row_to_dict(enrich_row)
                for field in ("categories", "entities"):
                    try:
                        enrich_data[field] = _json.loads(enrich_data.get(field) or "[]")
                    except (ValueError, TypeError):
                        pass
                result["enrichment"] = enrich_data
        return result

    @app.post("/api/articles/bulk")
    def bulk_articles(req: _BulkStatusRequest) -> dict:
        if len(req.urls) > 100:
            raise HTTPException(status_code=413, detail="Too many URLs (max 100)")
        if not req.urls:
            return {}
        rows = get_articles_by_urls(db, req.urls)
        result: dict = {}
        for url in req.urls:
            row = rows.get(url)
            result[url] = _row_to_dict(row) if row and row["status"] == "ok" else None
        return result

    @app.post("/api/articles/bulk_status")
    def bulk_status(req: _BulkStatusRequest) -> dict:
        rows = get_articles_by_urls(db, req.urls)
        import json as _json
        status_map: dict[str, dict] = {}
        for url in req.urls:
            row = rows.get(url)
            if not row:
                status_map[url] = {"status": "fresh", "has_enrichment": False}
            else:
                enrich = get_enrichment(db, row["id"]) if row["status"] == "ok" else None
                status_map[url] = {
                    "status": row["status"],
                    "has_enrichment": enrich is not None,
                }
        return status_map

    @app.get("/api/articles/{article_id}")
    def get_article(article_id: int) -> dict:
        row = get_article_by_id(db, article_id)
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        return _row_to_dict(row)

    # NOTE: /api/articles/{article_id}/retry already exists (line ~564) and is
    # wired into ScrapeRecoveryPage. The force=True flag on _scrape_one_sync
    # (added 2026-05-20 alongside this comment) lets a future surface bypass the
    # "already ok" early-return if needed.

    # ── Enrichment endpoints ─────────────────────────────────────────────────

    @app.post("/api/articles/{article_id}/enrich")
    async def enrich_article_endpoint(article_id: int) -> dict:
        row = get_article_by_id(db, article_id)
        if not row:
            raise HTTPException(status_code=404, detail="Article not found")
        try:
            result = await asyncio.to_thread(
                enrich_article, dict(row), ollama_url
            )
        except EnrichError as exc:
            raise HTTPException(status_code=502, detail=str(exc))

        import json
        from .enrich.entity_sync import sync_llm_entities
        enriched_at = datetime.utcnow().isoformat()
        entities_list = [e.model_dump() for e in result.entities]
        entities_json = json.dumps(entities_list)
        upsert_enrichment(
            db,
            article_id=article_id,
            summary=result.summary,
            categories=json.dumps(result.categories),
            entities=entities_json,
            sentiment=result.sentiment,
            model="qwen2.5:3b",
            enriched_at=enriched_at,
        )
        try:
            sync_llm_entities(db, article_id, entities_list)
        except Exception as exc:
            logger.warning("sync_llm_entities failed for article %d: %s", article_id, exc)
        if qdrant is not None:
            try:
                text_for_embed = f"{dict(row).get('title', '')} {result.summary}"
                await asyncio.to_thread(
                    upsert_article_vector,
                    qdrant,
                    article_id,
                    text_for_embed,
                    {"url": dict(row).get("url", ""), "title": dict(row).get("title", "")},
                    ollama_url,
                )
            except Exception as exc:
                logger.warning("Vector upsert failed for article %d: %s", article_id, exc)
        return {
            "article_id": article_id,
            "summary": result.summary,
            "categories": result.categories,
            "suggested_new_category": result.suggested_new_category,
            "entities": [e.model_dump() for e in result.entities],
            "sentiment": result.sentiment,
            "enriched_at": enriched_at,
        }

    @app.get("/api/articles/{article_id}/enrichment")
    def get_enrichment_endpoint(article_id: int) -> dict:
        row = get_enrichment(db, article_id)
        if not row:
            raise HTTPException(status_code=404, detail="No enrichment for this article")
        return _row_to_dict(row)

    @app.get("/api/articles/{article_id}/similar")
    async def similar_articles(
        article_id: int,
        limit: int = Query(10, ge=1, le=30),
        min_score: float = Query(0.0, ge=0.0, le=1.0),
    ) -> list[dict]:
        if qdrant is None:
            raise HTTPException(status_code=503, detail="Qdrant unavailable — semantic search disabled")
        hits = await asyncio.to_thread(find_similar_articles, qdrant, article_id, limit)
        if hits is None:
            raise HTTPException(status_code=404, detail="No vector for this article yet")
        # Build url → list of (chat_id, chat_name, ts) so the UI can offer a
        # "View in chat" affordance for EVERY location the URL was shared.
        # Multiple chats may share the same URL — the UI shows a dropdown when
        # more than one is present.
        url_to_locs: dict[str, list[dict]] = {}
        for cid, export in chats.items():
            for entry in export.entries:
                if not isinstance(entry, Message):
                    continue
                for tok in (entry.body or "").split():
                    cleaned = tok.rstrip(".,)>]")
                    if cleaned.startswith("http"):
                        bucket = url_to_locs.setdefault(cleaned, [])
                        # dedupe by (chat_id, ts) — same URL referenced twice in
                        # the same chat produces one location row.
                        key = (cid, entry.timestamp.isoformat())
                        if not any(l["chat_id"] == key[0] and l["ts"] == key[1] for l in bucket):
                            bucket.append({
                                "chat_id": cid,
                                "chat_name": chat_names[cid],
                                "ts": entry.timestamp.isoformat(),
                            })
        result = []
        for h in hits:
            if h["score"] < min_score:
                continue
            row = get_article_by_id(db, h["id"])
            if row:
                d = {**_row_to_dict(row), "similarity_score": h["score"]}
                locs = url_to_locs.get(d.get("url") or "", [])
                d["locations"] = locs  # full list — ordered as first-encountered
                # Keep the singular fields for backward compatibility with
                # earlier frontend versions (Q15 follow-up).
                if locs:
                    d["chat_id"] = locs[0]["chat_id"]
                    d["chat_name"] = locs[0]["chat_name"]
                    d["ts_first_seen"] = locs[0]["ts"]
                else:
                    d["chat_id"] = None
                    d["chat_name"] = None
                    d["ts_first_seen"] = None
                result.append(d)
        return result

    @app.get("/api/chats/{chat_id}/find-message")
    def find_message_page(
        chat_id: str,
        ts: str = Query(..., description="ISO timestamp of the message to locate"),
        page_size: int = Query(50, ge=1, le=500),
        q: str = Query(""),
    ) -> dict:
        """Return the page number that contains the message with the given ts.

        Used by the frontend to jump from a similar-tweet click to the correct
        page of the chat thread so the message can be scrolled into view.
        """
        if chat_id not in chats:
            raise HTTPException(status_code=404, detail="Chat not found")
        export = chats[chat_id]
        q_low = (q or "").lower()
        # Apply same filter the messages endpoint does (search-by-body).
        entries = export.entries
        if q_low:
            entries = [
                e for e in entries
                if (isinstance(e, Message) and q_low in (e.body or "").lower())
                or (not isinstance(e, Message) and q_low in (getattr(e, "text", "") or "").lower())
            ]
        target_iso = ts
        for idx, entry in enumerate(entries):
            entry_ts = getattr(entry, "timestamp", None)
            entry_ts_iso = entry_ts.isoformat() if entry_ts else None
            if entry_ts_iso == target_iso:
                page = (idx // page_size) + 1
                return {
                    "page": page,
                    "index_in_page": idx % page_size,
                    "total_pages": max(1, (len(entries) + page_size - 1) // page_size),
                    "found": True,
                }
        return {"page": 1, "index_in_page": 0, "total_pages": 1, "found": False}

    class SimilarBulkBody(BaseModel):
        ids: list[int]
        limit: int = 5
        min_score: float = 0.0

    @app.post("/api/articles/similar_bulk")
    async def similar_bulk(body: SimilarBulkBody) -> dict:
        """Return similar articles for multiple IDs in one call.

        Returns {article_id: [similar_articles]} — IDs with no vector return [].
        """
        if qdrant is None:
            return {str(aid): [] for aid in body.ids[:50]}
        results: dict[str, list] = {}
        for article_id in body.ids[:50]:  # cap to avoid abuse
            try:
                hits = await asyncio.to_thread(find_similar_articles, qdrant, article_id, body.limit)
            except Exception:
                hits = None
            if not hits:
                results[str(article_id)] = []
                continue
            items = []
            for h in hits:
                if h["score"] < body.min_score:
                    continue
                row = get_article_by_id(db, h["id"])
                if row:
                    items.append({**_row_to_dict(row), "similarity_score": h["score"]})
            results[str(article_id)] = items
        return results

    @app.post("/api/embeddings/backfill")
    async def embeddings_backfill() -> dict:
        """Embed all enriched articles that have no Qdrant vector yet."""
        if qdrant is None:
            raise HTTPException(status_code=503, detail="Qdrant unavailable")
        rows = db.execute(
            "SELECT a.id, a.url, a.title, e.summary "
            "FROM articles a "
            "JOIN article_enrichments e ON e.article_id = a.id "
            "WHERE a.status='ok'"
        ).fetchall()
        ok = 0
        skipped = 0
        failed = 0
        for row in rows:
            article_id = row["id"]
            try:
                existing = await asyncio.to_thread(
                    qdrant.retrieve, "articles", [article_id], with_vectors=False
                )
                if existing:
                    skipped += 1
                    continue
                title = row["title"] or ""
                summary = row["summary"] or ""
                text_for_embed = f"{title} {summary}".strip()
                if not text_for_embed:
                    skipped += 1
                    continue
                await asyncio.to_thread(
                    upsert_article_vector,
                    qdrant,
                    article_id,
                    text_for_embed,
                    {"url": row["url"] or "", "title": title},
                    ollama_url,
                )
                ok += 1
            except Exception as exc:
                logger.warning("Backfill embed failed for article %d: %s", article_id, exc)
                failed += 1
        return {"embedded": ok, "skipped": skipped, "failed": failed}

    # ── Tweet thread reconstruction (I5) ─────────────────────────────────────

    @app.get("/api/articles/{article_id}/thread")
    def get_tweet_thread(
        article_id: int,
        max_depth: int = Query(10, ge=1, le=20),
    ) -> list[dict]:
        import json as _json

        seed_row = get_article_by_id(db, article_id)
        if seed_row is None or not seed_row["tweet_meta"]:
            raise HTTPException(status_code=404, detail="Tweet article not found")

        try:
            seed_meta = _json.loads(seed_row["tweet_meta"])
        except (ValueError, TypeError):
            raise HTTPException(status_code=404, detail="Invalid tweet metadata")

        seed_tweet_id = seed_meta.get("tweet_id")
        if not seed_tweet_id:
            raise HTTPException(status_code=404, detail="Article has no tweet_id")

        seen: set[str] = {seed_tweet_id}

        # Walk upward: follow in_reply_to_status_id chain
        ancestors: list[dict] = []
        current_meta = seed_meta
        for _ in range(max_depth):
            parent_id = current_meta.get("in_reply_to_status_id")
            if not parent_id or parent_id in seen:
                break
            seen.add(parent_id)
            parent_row = get_article_by_tweet_id(db, parent_id)
            if parent_row is None:
                ancestors.insert(0, {"missing": True, "tweet_id": parent_id})
                break
            try:
                parent_meta = _json.loads(parent_row["tweet_meta"])
            except (ValueError, TypeError):
                break
            ancestors.insert(0, {
                "id": parent_row["id"],
                "url": parent_row["url"],
                "tweet": parent_meta,
            })
            current_meta = parent_meta

        # Label ancestor positions
        result: list[dict] = []
        for i, anc in enumerate(ancestors):
            if anc.get("missing"):
                result.append({**anc, "position": "ancestor"})
            else:
                pos = "parent" if i == len(ancestors) - 1 else "ancestor"
                result.append({**anc, "position": pos})

        # Self
        result.append({
            "id": seed_row["id"],
            "url": seed_row["url"],
            "tweet": seed_meta,
            "position": "self",
        })

        # Walk downward: collect replies recursively
        def _collect_replies(tweet_id: str, depth: int) -> list[dict]:
            if depth <= 0:
                return []
            reply_rows = get_replies_to_tweet(db, tweet_id)
            items: list[dict] = []
            for row in reply_rows:
                try:
                    meta = _json.loads(row["tweet_meta"])
                except (ValueError, TypeError):
                    continue
                tid = meta.get("tweet_id", "")
                if tid in seen:
                    continue
                seen.add(tid)
                items.append({"id": row["id"], "url": row["url"], "tweet": meta, "position": "reply"})
                items.extend(_collect_replies(tid, depth - 1))
            return items

        result.extend(_collect_replies(seed_tweet_id, max_depth))
        return result

    @app.get("/api/articles/{article_id}/ocr")
    def get_article_ocr(article_id: int) -> list[dict]:
        rows = get_media_ocr_for_article(db, article_id)
        return [{"media_url": r["media_url"], "ocr_text": r["ocr_text"] or ""} for r in rows]

    # ── Tweet quote/reply links (E4) ──────────────────────────────────────────

    @app.get("/api/articles/{article_id}/quoted_by")
    def article_quoted_by(article_id: int) -> list[dict]:
        from .scrape.db import get_quoted_by as _get_quoted_by
        return _get_quoted_by(db, article_id)

    @app.get("/api/articles/{article_id}/replied_by")
    def article_replied_by(article_id: int) -> list[dict]:
        from .scrape.db import get_replied_by as _get_replied_by
        return _get_replied_by(db, article_id)

    @app.get("/api/articles/{article_id}/links")
    def article_links(article_id: int) -> dict:
        from .scrape.db import get_article_tweet_links as _get_links
        return _get_links(db, article_id)

    # ── Bookmarks ─────────────────────────────────────────────────────────────

    class BookmarkBody(BaseModel):
        starred: int = 1
        note: str | None = None

    @app.post("/api/bookmarks/{article_id}")
    def bookmark_upsert(article_id: int, body: BookmarkBody) -> dict:
        row = upsert_bookmark(db, article_id, starred=body.starred, note=body.note)
        if row is None:
            raise HTTPException(status_code=404, detail="article not found")
        return dict(row)

    @app.delete("/api/bookmarks/{article_id}")
    def bookmark_delete(article_id: int) -> dict:
        deleted = delete_bookmark(db, article_id)
        return {"deleted": deleted}

    @app.get("/api/bookmarks")
    def bookmarks_list(
        has_note: bool = Query(False),
        since: str | None = Query(None),
        limit: int = Query(100, ge=1, le=500),
    ) -> list[dict]:
        rows = get_bookmarks(db, has_note=has_note, since=since, limit=limit)
        return [dict(r) for r in rows]

    # ── Message bookmarks ─────────────────────────────────────────────────────

    class MsgBookmarkBody(BaseModel):
        chat_id: str
        sender: str
        ts: str
        body: str
        note: str | None = None

    @app.post("/api/message-bookmarks")
    def message_bookmark_upsert(body: MsgBookmarkBody) -> dict:
        msg_key = f"{body.chat_id}|{body.ts}|{body.sender}"
        row = upsert_message_bookmark(db, msg_key, body.chat_id, body.sender, body.ts, body.body, body.note)
        return dict(row)

    @app.delete("/api/message-bookmarks/{msg_key:path}")
    def message_bookmark_delete(msg_key: str) -> dict:
        deleted = delete_message_bookmark(db, msg_key)
        return {"deleted": deleted}

    @app.get("/api/message-bookmarks/{msg_key:path}")
    def message_bookmark_get(msg_key: str) -> dict:
        row = get_message_bookmark(db, msg_key)
        if row is None:
            raise HTTPException(status_code=404, detail="not found")
        return dict(row)

    @app.get("/api/message-bookmarks")
    def message_bookmarks_list(
        limit: int = Query(100, ge=1, le=1000),
    ) -> list[dict]:
        rows = get_message_bookmarks(db, limit=limit)
        return [dict(r) for r in rows]

    # ── Collections ───────────────────────────────────────────────────────────

    class CollectionBody(BaseModel):
        name: str
        description: str | None = None

    class CollectionItemBody(BaseModel):
        article_id: int

    @app.post("/api/collections")
    def collection_create(body: CollectionBody) -> dict:
        try:
            row = create_collection(db, name=body.name, description=body.description)
        except Exception:
            raise HTTPException(status_code=409, detail="collection name already exists")
        return dict(row)

    @app.get("/api/collections")
    def collections_list() -> list[dict]:
        return [dict(r) for r in get_collections(db)]

    @app.get("/api/collections/{collection_id}")
    def collection_get(collection_id: int) -> dict:
        row = get_collection(db, collection_id)
        if not row:
            raise HTTPException(status_code=404, detail="collection not found")
        return dict(row)

    @app.post("/api/collections/{collection_id}/items")
    def collection_item_add(collection_id: int, body: CollectionItemBody) -> dict:
        if not get_collection(db, collection_id):
            raise HTTPException(status_code=404, detail="collection not found")
        add_collection_item(db, collection_id, body.article_id)
        return {"collection_id": collection_id, "article_id": body.article_id}

    @app.delete("/api/collections/{collection_id}/items/{article_id}")
    def collection_item_remove(collection_id: int, article_id: int) -> dict:
        if not get_collection(db, collection_id):
            raise HTTPException(status_code=404, detail="collection not found")
        removed = remove_collection_item(db, collection_id, article_id)
        if not removed:
            raise HTTPException(status_code=404, detail="item not in collection")
        return {"removed": True}

    @app.delete("/api/collections/{collection_id}")
    def collection_delete(collection_id: int) -> dict:
        """Delete the entire collection and its items. Returns 404 if the
        collection doesn't exist."""
        if not get_collection(db, collection_id):
            raise HTTPException(status_code=404, detail="collection not found")
        delete_collection(db, collection_id)
        return {"deleted": True, "id": collection_id}

    @app.get("/api/collections/{collection_id}/items")
    def collection_items_list(
        collection_id: int,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        order: str = Query("desc"),
    ) -> list[dict]:
        if not get_collection(db, collection_id):
            raise HTTPException(status_code=404, detail="collection not found")
        rows = get_collection_items(db, collection_id, page=page, page_size=page_size, order=order)
        return [_row_to_dict(r) for r in rows]

    @app.get("/api/collections/{collection_id}/export")
    def collection_export(
        collection_id: int,
        format: str = Query("md", pattern="^(md|csv|json)$"),
    ) -> Any:
        col = get_collection(db, collection_id)
        if not col:
            raise HTTPException(status_code=404, detail="collection not found")
        rows = get_collection_items(db, collection_id, page_size=1000)
        article_hits = [_row_to_dict(r) for r in rows]
        label = col["name"]
        slug = _slug(label)
        return _export_bundle(label, slug, [], article_hits, format)

    # ── Research bins (Q11) ──────────────────────────────────────────────────

    class ResearchBinBody(BaseModel):
        name: str
        hypothesis: str | None = None
        seed_query: str | None = None
        seed_filters: str | None = None
        expiry_days: int = 7

    class ResearchBinUpdateBody(BaseModel):
        name: str | None = None
        hypothesis: str | None = None

    class ResearchBinItemBody(BaseModel):
        target_kind: str = "article"
        target_id: str
        note: str | None = None

    @app.post("/api/research_bins")
    def research_bin_create(body: ResearchBinBody) -> dict:
        row = create_research_bin(
            db, body.name, body.hypothesis, body.seed_query,
            body.seed_filters, body.expiry_days,
        )
        return dict(row)

    @app.get("/api/research_bins")
    def research_bins_list(
        include_expired: int = Query(0),
        with_pinned_for: str = Query(""),
    ) -> list[dict]:
        rows = get_research_bins(db, include_expired=bool(include_expired))
        result = [dict(r) for r in rows]
        if with_pinned_for and ":" in with_pinned_for:
            kind, _, tid = with_pinned_for.partition(":")
            pinned_bin_ids = {
                row["bin_id"]
                for row in db.execute(
                    "SELECT bin_id FROM research_bin_items WHERE target_kind=? AND target_id=?",
                    (kind, tid),
                ).fetchall()
            }
            for r in result:
                r["is_pinned_here"] = r["id"] in pinned_bin_ids
        return result

    @app.get("/api/research_bins/{bin_id}")
    def research_bin_get(bin_id: int, include_expired: int = Query(0)) -> dict:
        row = get_research_bin(db, bin_id)
        if not row:
            raise HTTPException(status_code=404, detail="research bin not found")
        items = get_research_bin_items(db, bin_id)
        result = dict(row)
        result["items"] = items

        # Re-run seed query if present
        if row["seed_query"]:
            try:
                hits = keyword_search(db, row["seed_query"], limit=50)
                result["live_hits"] = [dict(h) for h in hits]
            except Exception:
                result["live_hits"] = []
        else:
            result["live_hits"] = []

        pinned_ids = {
            it["target_id"] for it in items if it["target_kind"] == "article"
        }
        for h in result["live_hits"]:
            h["pinned"] = str(h.get("id", "")) in pinned_ids

        return result

    @app.patch("/api/research_bins/{bin_id}")
    def research_bin_update(bin_id: int, body: ResearchBinUpdateBody) -> dict:
        row = update_research_bin(db, bin_id, body.name, body.hypothesis)
        if not row:
            raise HTTPException(status_code=404, detail="research bin not found")
        return dict(row)

    @app.post("/api/research_bins/{bin_id}/items")
    def research_bin_add_item(bin_id: int, body: ResearchBinItemBody) -> dict:
        if not get_research_bin(db, bin_id):
            raise HTTPException(status_code=404, detail="research bin not found")
        row = add_research_bin_item(db, bin_id, body.target_kind, body.target_id, body.note)
        return dict(row)

    @app.delete("/api/research_bins/{bin_id}/items/{item_id}")
    def research_bin_remove_item(bin_id: int, item_id: int) -> dict:
        ok = delete_research_bin_item(db, item_id)
        if not ok:
            raise HTTPException(status_code=404, detail="item not found")
        return {"deleted": True}

    @app.post("/api/research_bins/{bin_id}/promote")
    def research_bin_promote(bin_id: int) -> dict:
        col = promote_research_bin_to_collection(db, bin_id)
        if not col:
            raise HTTPException(status_code=404, detail="research bin not found")
        return dict(col)

    @app.delete("/api/research_bins/{bin_id}")
    def research_bin_delete(bin_id: int) -> dict:
        ok = delete_research_bin(db, bin_id)
        if not ok:
            raise HTTPException(status_code=404, detail="research bin not found")
        return {"deleted": True}

    # ── Group export (G3) ────────────────────────────────────────────────────

    class GroupExportBody(BaseModel):
        article_ids: list[int]
        format: str = "prompt"
        prompt_template: str = "analyse"
        custom_prompt: str | None = None
        max_tweet_chars: int = 500
        include_similar_context: bool = False

    @app.post("/api/export/group")
    async def export_group(
        body: GroupExportBody,
        inline: bool = Query(False),
    ) -> Any:
        import io as _io
        from .export.group import render_group_bundle, bundle_filename

        if body.format not in ("md", "txt", "json", "prompt"):
            raise HTTPException(status_code=422, detail="format must be md, txt, json, or prompt")
        if body.prompt_template not in ("analyse", "summarise", "extract_claims", "custom"):
            raise HTTPException(status_code=422, detail="unknown prompt_template")
        if not body.article_ids:
            raise HTTPException(status_code=422, detail="article_ids must not be empty")

        articles = []
        for aid in body.article_ids[:100]:
            row = get_article_by_id(db, aid)
            if row:
                d = _row_to_dict(row)
                enr = get_enrichment(db, aid)
                if enr:
                    d["summary"] = dict(enr).get("summary")
                articles.append(d)

        similar_context: list[dict] | None = None
        if body.include_similar_context and qdrant is not None:
            seen_ids = {a.get("id") for a in articles}
            context_items: list[dict] = []
            for article in articles:
                aid = article.get("id")
                if aid is None:
                    continue
                try:
                    hits = await asyncio.to_thread(find_similar_articles, qdrant, aid, 3)
                except Exception:
                    hits = None
                if not hits:
                    continue
                for h in hits:
                    if h["id"] in seen_ids:
                        continue
                    seen_ids.add(h["id"])
                    row = get_article_by_id(db, h["id"])
                    if row:
                        context_items.append({**_row_to_dict(row), "similarity_score": h["score"]})
            if context_items:
                similar_context = context_items

        text = render_group_bundle(
            articles,
            fmt=body.format,
            prompt_template=body.prompt_template,
            custom_prompt=body.custom_prompt,
            max_tweet_chars=body.max_tweet_chars,
            similar_context=similar_context,
        )

        if inline:
            return {"body": text}

        fname = bundle_filename(body.format)
        media_type = {
            "json": "application/json",
            "txt": "text/plain",
            "prompt": "text/plain",
            "md": "text/markdown",
        }[body.format]
        return StreamingResponse(
            _io.BytesIO(text.encode("utf-8")),
            media_type=media_type,
            headers={"Content-Disposition": f"attachment; filename={fname}"},
        )

    # ── Digests ───────────────────────────────────────────────────────────────

    from .enrich.digest import generate_digest as _generate_digest
    from datetime import date as _date

    class DigestGenerateBody(BaseModel):
        period: str = "daily"
        end_date: str | None = None
        chat_id: str | None = None

    @app.post("/api/digests/generate")
    def digest_generate(body: DigestGenerateBody) -> dict:
        if body.period not in ("daily", "weekly"):
            raise HTTPException(status_code=422, detail="period must be 'daily' or 'weekly'")
        end_date = None
        if body.end_date:
            try:
                end_date = _date.fromisoformat(body.end_date)
            except ValueError:
                raise HTTPException(status_code=422, detail="end_date must be ISO date (YYYY-MM-DD)")
        result = _generate_digest(
            db=db,
            ollama_url=ollama_url,
            period=body.period,
            chats=chats,
            end_date=end_date,
            chat_id=body.chat_id,
        )
        if "citations" in result and isinstance(result["citations"], str):
            try:
                result["citations"] = json.loads(result["citations"])
            except (ValueError, TypeError):
                result["citations"] = []
        return result

    @app.get("/api/digests")
    def digests_list(
        period: str | None = Query(None),
        chat_id: str | None = Query(None),
        limit: int = Query(20, ge=1, le=100),
    ) -> list[dict]:
        conditions = []
        params: list = []
        if period:
            conditions.append("period = ?")
            params.append(period)
        if chat_id:
            conditions.append("chat_id = ?")
            params.append(chat_id)
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(limit)
        rows = db.execute(f"SELECT * FROM digests{where} ORDER BY generated_at DESC LIMIT ?", params).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            if isinstance(d.get("citations"), str):
                try:
                    d["citations"] = json.loads(d["citations"])
                except (ValueError, TypeError):
                    d["citations"] = []
            results.append(d)
        return results

    @app.get("/api/digests/{digest_id}")
    def digest_get(digest_id: int) -> dict:
        row = db.execute("SELECT * FROM digests WHERE id = ?", (digest_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="digest not found")
        d = dict(row)
        if isinstance(d.get("citations"), str):
            try:
                d["citations"] = json.loads(d["citations"])
            except (ValueError, TypeError):
                d["citations"] = []
        return d

    # ── Entity admin (I17) ────────────────────────────────────────────────────

    from .scrape.db import (
        backfill_entities,
        get_entity,
        list_entities,
        merge_entities,
        rename_entity,
        unmerge_entity,
        upsert_entity,
        link_article_entity,
    )

    @app.get("/api/entities")
    def entities_list(
        kind: str | None = Query(None),
        search: str | None = Query(None),
        order: str = Query("count"),
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
        include_aliases: bool = Query(False),
    ) -> list[dict]:
        return list_entities(db, kind=kind, search=search, order=order,
                             limit=limit, offset=offset, include_aliases=include_aliases)

    @app.get("/api/entities/backfill")
    def entities_backfill_status() -> dict:
        count = db.execute("SELECT COUNT(*) AS c FROM entities").fetchone()["c"]
        ae_count = db.execute("SELECT COUNT(*) AS c FROM article_entities").fetchone()["c"]
        return {"entity_count": count, "article_entity_count": ae_count}

    @app.post("/api/entities/backfill")
    def entities_backfill() -> dict:
        inserted = backfill_entities(db)
        return {"inserted": inserted}

    @app.get("/api/entities/{entity_id}")
    def entity_get(entity_id: int) -> dict:
        result = get_entity(db, entity_id)
        if not result:
            raise HTTPException(status_code=404, detail="entity not found")
        if result.get("canonical_id"):
            canonical = get_entity(db, result["canonical_id"])
            if canonical:
                canonical["redirected_from"] = entity_id
                return canonical
        return result

    class MergeBody(BaseModel):
        source_ids: list[int]
        target_id: int

    @app.get("/api/entities/{entity_id}/articles")
    def entity_articles(
        entity_id: int,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        order: str = Query("date", pattern="^(date|engagement)$"),
    ) -> dict:
        result = get_entity(db, entity_id)
        if not result:
            raise HTTPException(status_code=404, detail="entity not found")
        data = get_articles_for_entity(db, entity_id, page=page, page_size=page_size, order=order)
        import json as _j
        articles = []
        for a in data["articles"]:
            raw_meta = a.pop("tweet_meta", None)
            if raw_meta:
                try:
                    a["tweet"] = _j.loads(raw_meta)
                except (ValueError, TypeError):
                    a["tweet"] = None
            articles.append(a)
        data["articles"] = articles
        return data

    @app.post("/api/entities/merge")
    def entity_merge(body: MergeBody) -> dict:
        if not body.source_ids:
            raise HTTPException(status_code=422, detail="source_ids must not be empty")
        return merge_entities(db, body.source_ids, body.target_id)

    class UnmergeBody(BaseModel):
        source_id: int

    @app.post("/api/entities/{entity_id}/unmerge")
    def entity_unmerge(entity_id: int, body: UnmergeBody) -> dict:
        return unmerge_entity(db, body.source_id)

    class RenameBody(BaseModel):
        name: str

    @app.post("/api/entities/{entity_id}/rename")
    def entity_rename(entity_id: int, body: RenameBody) -> dict:
        result = rename_entity(db, entity_id, body.name)
        if not result:
            raise HTTPException(status_code=422, detail="name must not be empty")
        return result

    # ── Research workspace (R1) ───────────────────────────────────────────────

    @app.get("/api/research/entity/{entity_name:path}")
    async def research_entity_dossier(
        entity_name: str,
        refresh: bool = Query(False),
        req_ollama_url: str = Query(None),
    ) -> dict:
        eff_url = req_ollama_url or ollama_url
        try:
            dossier = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: build_entity_dossier(db, entity_name, eff_url, force_refresh=refresh),
            )
        except Exception as exc:
            logger.warning("build_entity_dossier failed for '%s': %s", entity_name, exc)
            raise HTTPException(status_code=500, detail=f"Failed to build dossier: {exc}")
        if dossier is None:
            raise HTTPException(status_code=404, detail=f"Entity not found: {entity_name}")
        return dossier

    @app.post("/api/research/entity/{entity_name:path}/refresh")
    async def research_entity_refresh(
        entity_name: str,
        req_ollama_url: str = Query(None),
    ) -> dict:
        eff_url = req_ollama_url or ollama_url
        dossier = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: build_entity_dossier(db, entity_name, eff_url, force_refresh=True),
        )
        if dossier is None:
            raise HTTPException(status_code=404, detail=f"entity '{entity_name}' not found")
        return dossier

    # ── Cross-entity comparison (R2) ─────────────────────────────────────────

    @app.get("/api/research/compare")
    async def research_compare(
        entities: list[str] = Query(...),
        req_ollama_url: str = Query(None),
    ) -> dict:
        if len(entities) < 2:
            raise HTTPException(status_code=422, detail="At least 2 entities required")
        if len(entities) > 4:
            raise HTTPException(status_code=422, detail="At most 4 entities supported")
        from .research.compare import compare_entities as _compare_entities
        eff_url = req_ollama_url or ollama_url
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: _compare_entities(db, entities, eff_url),
        )
        return result

    # ── Deep-dive report (R5) ────────────────────────────────────────────────

    @app.get("/api/research/deepdive/{entity_name:path}")
    async def research_deepdive_get(entity_name: str) -> dict:
        from .research.deepdive import get_deepdive_cache
        cached = get_deepdive_cache(db, entity_name)
        if cached is None:
            raise HTTPException(status_code=404, detail="No cached deep-dive report. POST to generate one.")
        return cached

    @app.post("/api/research/deepdive/{entity_name:path}")
    async def research_deepdive_generate(
        entity_name: str,
        force_refresh: bool = Query(False),
        req_ollama_url: str = Query(None),
    ) -> dict:
        from .research.deepdive import generate_deepdive
        eff_url = req_ollama_url or ollama_url
        report = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: generate_deepdive(db, entity_name, eff_url, force_refresh=force_refresh),
        )
        if report is None:
            raise HTTPException(status_code=404, detail=f"entity '{entity_name}' not found")
        return report

    # ── Narrative entity graph (R4) ───────────────────────────────────────────

    @app.get("/api/research/graph")
    def research_graph(
        seed: str = Query(..., description="Seed entity name"),
        depth: int = Query(2, ge=1, le=3),
        window: str = Query("all", pattern="^(all|365d|180d|90d)$"),
        min_edge_weight: int = Query(3, ge=1),
        max_nodes: int = Query(30, ge=5, le=80),
    ) -> dict:
        graph = get_entity_graph(db, seed, depth=depth, window=window, min_edge_weight=min_edge_weight, max_nodes=max_nodes)
        if graph is None:
            raise HTTPException(status_code=404, detail=f"Entity '{seed}' not found")
        return graph

    # ── Entity home (R6) ─────────────────────────────────────────────────────

    @app.get("/api/home/entities")
    def entity_home(
        kind: str | None = Query(None),
        search: str | None = Query(None),
        sort: str = Query("count"),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        now: str | None = Query(None),
    ) -> dict:
        try:
            return get_entity_home_cards(db, kind=kind, search=search, sort=sort, limit=limit, offset=offset, now=now)
        except Exception as exc:
            logger.warning("entity_home failed: %s", exc)
            return {"total": 0, "entities": []}

    # ── Topic clusters (R3) ───────────────────────────────────────────────────

    @app.get("/api/topics")
    def topics_list(order: str = Query("size")) -> list[dict]:
        from .scrape.topics import get_topic_list
        return get_topic_list(db, order=order)

    @app.get("/api/topics/status")
    def topics_status() -> dict:
        # Merge transient last-run counters with persistent DB counts so the UI
        # never sees "topics: 0" while the topics table actually has rows.
        progress = dict(topic_clustering_progress)
        try:
            persisted_topics = db.execute("SELECT COUNT(*) FROM topics").fetchone()[0]
            persisted_articles = db.execute("SELECT COUNT(*) FROM topic_articles").fetchone()[0]
        except Exception:
            persisted_topics = persisted_articles = 0
        progress["topics_persisted"] = persisted_topics
        progress["articles_clustered"] = persisted_articles
        return progress

    @app.post("/api/topics/recluster")
    async def topics_recluster(req_ollama_url: str = Query(None)) -> dict:
        if qdrant is None:
            raise HTTPException(status_code=503, detail="Qdrant not available")
        from .scrape.topics import run_clustering, get_all_vectors_from_qdrant
        eff_url = req_ollama_url or ollama_url
        vectors = await asyncio.to_thread(get_all_vectors_from_qdrant, qdrant)
        if not vectors:
            return {"topics": 0, "articles": 0, "skipped": True, "reason": "no vectors in Qdrant"}
        result = await asyncio.to_thread(run_clustering, db, vectors, eff_url)
        return result

    @app.get("/api/topics/{topic_id}")
    def topics_detail(topic_id: int) -> dict:
        from .scrape.topics import get_topic
        result = get_topic(db, topic_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Topic not found")
        return result

    class _TopicPatch(BaseModel):
        pinned: bool | None = None
        custom_name: str | None = None

    @app.patch("/api/topics/{topic_id}")
    def topics_update(topic_id: int, body: _TopicPatch) -> dict:
        """Set the pinned flag and/or override the cluster name. Pinned topics
        survive future reclusterings (the cluster job re-inserts them rather
        than wiping).

        Distinguishes "field omitted from request" (no-op) from "field present
        with value null" (clear) using Pydantic's model_fields_set. So:
          - {} → no-op
          - {"pinned": true} → set pinned, leave custom_name untouched
          - {"custom_name": ""} or {"custom_name": null} → clear custom_name
          - {"custom_name": "Foo"} → set custom_name AND name to "Foo"
        """
        existing = db.execute("SELECT id, pinned, custom_name FROM topics WHERE id=?", (topic_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Topic not found")
        provided = body.model_fields_set
        fields = []
        args: list = []
        if "pinned" in provided and body.pinned is not None:
            fields.append("pinned = ?")
            args.append(1 if body.pinned else 0)
        if "custom_name" in provided:
            # Both null and empty-after-strip clear the custom_name. A real
            # value sets both custom_name and the displayed name.
            new_val: str | None
            if body.custom_name is None or not body.custom_name.strip():
                new_val = None
            else:
                new_val = body.custom_name.strip()
            fields.append("custom_name = ?")
            args.append(new_val)
            if new_val is not None:
                fields.append("name = ?")
                args.append(new_val)
        if not fields:
            return {"ok": True, "no_op": True}
        args.append(topic_id)
        db.execute(f"UPDATE topics SET {', '.join(fields)} WHERE id = ?", args)
        db.commit()
        row = db.execute("SELECT id, name, custom_name, pinned, article_count FROM topics WHERE id=?", (topic_id,)).fetchone()
        return dict(row)

    @app.get("/api/topics/{topic_id}/articles")
    def topics_articles(
        topic_id: int,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        order: str = Query("distance"),
    ) -> dict:
        from .scrape.topics import get_topic_articles, get_topic
        if not get_topic(db, topic_id):
            raise HTTPException(status_code=404, detail="Topic not found")
        total, rows = get_topic_articles(db, topic_id, page=page, page_size=page_size, order=order)
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "articles": [_row_to_dict(r) for r in rows],
        }

    # ── Ticker quotes ─────────────────────────────────────────────────────────

    from .enrich.quotes import get_or_refresh_quote, fetch_quote as _fetch_quote

    @app.get("/api/quotes/{symbol}")
    def quote_get(symbol: str) -> dict:
        return get_or_refresh_quote(db, symbol.upper())

    class QuoteRefreshBody(BaseModel):
        symbols: list[str]

    @app.post("/api/quotes/refresh")
    def quote_refresh(body: QuoteRefreshBody) -> list[dict]:
        symbols = [s.upper() for s in body.symbols[:20]]
        results = []
        for sym in symbols:
            data = _fetch_quote(sym)
            if "error" not in data:
                from .enrich.quotes import _upsert_quote
                _upsert_quote(db, sym, data)
            results.append(data)
        return results

    # ── Co-occurrence ─────────────────────────────────────────────────────────

    _VALID_KINDS = {"hashtag", "mention", "ticker", "author"}

    @app.get("/api/cooccurrence")
    def cooccurrence(
        seed_kind: str = Query(...),
        seed_value: str = Query(...),
        kind: str = Query(...),
        limit: int = Query(20, ge=1, le=100),
    ) -> list[dict]:
        if seed_kind not in _VALID_KINDS or kind not in _VALID_KINDS:
            raise HTTPException(status_code=422, detail=f"kind must be one of {sorted(_VALID_KINDS)}")
        return get_cooccurrence(db, seed_kind, seed_value, kind, limit=limit)

    @app.get("/api/cooccurrence/network")
    def cooccurrence_network(
        seed_kind: str = Query(...),
        seed_value: str = Query(...),
        limit: int = Query(10, ge=1, le=50),
    ) -> dict:
        if seed_kind not in _VALID_KINDS:
            raise HTTPException(status_code=422, detail=f"seed_kind must be one of {sorted(_VALID_KINDS)}")
        nodes: list[dict] = [{"id": f"{seed_kind}:{seed_value}", "kind": seed_kind, "value": seed_value, "count": 0}]
        edges: list[dict] = []
        for kind in _VALID_KINDS:
            hits = get_cooccurrence(db, seed_kind, seed_value, kind, limit=limit)
            for h in hits:
                nid = f"{kind}:{h['value']}"
                nodes.append({"id": nid, "kind": kind, "value": h["value"], "count": h["count"]})
                edges.append({"source": f"{seed_kind}:{seed_value}", "target": nid, "weight": h["count"]})
        return {"nodes": nodes, "edges": edges}

    @app.get("/api/search/hybrid")
    async def search_hybrid(
        q: str = Query(..., min_length=1),
        limit: int = Query(10, ge=1, le=50),
    ) -> list[dict]:
        kw_rows = keyword_search(db, q, limit * 2)
        scores: dict[int, dict] = {
            r["id"]: {"source": "keyword", "score": -r["score"]} for r in kw_rows
        }
        if qdrant is not None:
            try:
                sem_hits = await asyncio.to_thread(
                    search_similar, qdrant, q, limit * 2, ollama_url
                )
                for h in sem_hits:
                    if h["id"] in scores:
                        scores[h["id"]]["score"] += h["score"] * 10
                        scores[h["id"]]["source"] = "hybrid"
                    else:
                        scores[h["id"]] = {"source": "semantic", "score": h["score"] * 10}
            except Exception:
                pass
        ranked = sorted(scores.items(), key=lambda x: -x[1]["score"])[:limit]
        result = []
        for art_id, meta in ranked:
            row = get_article_by_id(db, art_id)
            if row:
                result.append({
                    **_row_to_dict(row),
                    "search_score": meta["score"],
                    "search_source": meta["source"],
                })
        return result

    @app.get("/api/search/semantic")
    async def semantic_search(
        q: str = Query(..., min_length=1),
        limit: int = Query(10, ge=1, le=50),
    ) -> list[dict]:
        if qdrant is None:
            raise HTTPException(status_code=503, detail="Qdrant unavailable")
        try:
            return await asyncio.to_thread(search_similar, qdrant, q, limit, ollama_url)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc))

    @app.get("/api/search/fuzzy")
    def fuzzy_search(
        q: str = Query(..., min_length=1),
        kind: str = Query("all"),
        limit: int = Query(20, ge=1, le=100),
    ) -> list[dict]:
        # Accept both singular and plural — `/api/search` uses singular; align here too.
        kind = {"article": "articles", "message": "messages", "chat": "chats"}.get(kind, kind)
        try:
            from rapidfuzz import process, fuzz
        except ImportError:
            raise HTTPException(status_code=501, detail="rapidfuzz not installed")

        results: list[dict] = []

        if kind in ("chats", "all"):
            name_to_id = {v: k for k, v in chat_names.items()}
            hits = process.extract(q, list(chat_names.values()), scorer=fuzz.WRatio, limit=limit, score_cutoff=60)
            for name, score, _ in hits:
                results.append({
                    "kind": "chat",
                    "chat_id": name_to_id.get(name, ""),
                    "chat_name": name,
                    "score": score,
                })

        if kind in ("messages", "all"):
            q_low = q.lower()
            msg_hits: list[tuple[int, str, str, str, str]] = []
            targets = chats
            for cid, export in targets.items():
                for entry in export.entries:
                    if not isinstance(entry, Message):
                        continue
                    score = fuzz.partial_ratio(q_low, entry.body.lower())
                    if score >= 70:
                        msg_hits.append((score, cid, chat_names[cid], entry.body, entry.timestamp.isoformat()))
            msg_hits.sort(key=lambda x: -x[0])
            for score, cid, cname, body, ts in msg_hits[:limit]:
                results.append({
                    "kind": "message",
                    "chat_id": cid,
                    "chat_name": cname,
                    "body": body,
                    "timestamp": ts,
                    "score": score,
                })

        if kind in ("articles", "all"):
            q_low = q.lower()
            # Build url → chat_id map once from in-memory chats.
            url_to_chat: dict[str, str] = {}
            for cid, export in chats.items():
                for entry in export.entries:
                    if not isinstance(entry, Message):
                        continue
                    for tok in entry.body.split():
                        if tok.startswith("http"):
                            url_to_chat.setdefault(tok.rstrip(".,)>]"), cid)
            rows = db.execute(
                "SELECT id, url, title, COALESCE(raw_text,'') AS raw_text, "
                "COALESCE(tweet_search_blob,'') AS blob FROM articles"
            ).fetchall()
            art_hits: list[tuple[int, int, str, str, str, str, str]] = []
            for row in rows:
                title = (row["title"] or "")
                blob = row["blob"]
                body = row["raw_text"][:2000]
                title_s = fuzz.partial_ratio(q_low, title.lower()) if title else 0
                blob_s = fuzz.partial_ratio(q_low, blob.lower()) if blob else 0
                body_s = fuzz.partial_ratio(q_low, body.lower()) if body else 0
                score = max(title_s, blob_s, body_s)
                if score >= 70:
                    if title_s == score and title:
                        match_source = "tweet_title"
                    elif blob_s == score and blob:
                        match_source = "tweet_meta"
                    else:
                        match_source = "tweet_body"
                    cid = url_to_chat.get(row["url"], "")
                    art_hits.append((
                        score, row["id"], row["url"], title or row["url"],
                        body[:200], match_source, cid,
                    ))
            art_hits.sort(key=lambda x: -x[0])
            for score, art_id, url, title, snippet, match_source, cid in art_hits[:limit]:
                results.append({
                    "kind": "article",
                    "article_id": art_id,
                    "url": url,
                    "title": title,
                    "snippet": snippet,
                    "match_source": match_source,
                    "chat_id": cid,
                    "chat_name": chat_names.get(cid, ""),
                    "score": score,
                })

        results.sort(key=lambda r: -r["score"])
        return results[:limit]

    # ── Author endpoints (I1) ─────────────────────────────────────────────────

    @app.get("/api/authors")
    def get_authors(
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
    ) -> dict:
        offset = (page - 1) * page_size
        rows = list_authors(db, limit=page_size, offset=offset)
        return {
            "page": page,
            "page_size": page_size,
            "authors": [dict(r) for r in rows],
        }

    @app.get("/api/authors/{handle}")
    def get_author(handle: str) -> dict:
        from collections import Counter
        metas = get_author_tweet_metas(db, handle)
        if not metas:
            raise HTTPException(status_code=404, detail="Author not found")

        hashtag_counter: Counter = Counter()
        mention_counter: Counter = Counter()
        total_favorites = total_retweets = total_views = 0
        first_seen: str | None = None
        last_seen: str | None = None
        display_name = handle
        is_verified = False

        for meta in metas:
            display_name = meta.get("author_name") or display_name
            is_verified = bool(meta.get("is_verified")) or is_verified
            for h in meta.get("hashtags") or []:
                hashtag_counter[h] += 1
            for m in meta.get("mentioned_handles") or []:
                mention_counter[m] += 1
            total_favorites += int(meta.get("favorite_count") or 0)
            total_retweets += int(meta.get("retweet_count") or 0)
            total_views += int(meta.get("view_count") or 0)
            fa = meta.get("_fetched_at")
            if fa:
                if not first_seen or fa < first_seen:
                    first_seen = fa
                if not last_seen or fa > last_seen:
                    last_seen = fa

        handle_lower = handle.lower()
        chats_shared_in = []
        for cid, export in chats.items():
            count = sum(1 for lnk in export.links if handle_lower in lnk.url.lower())
            if count > 0:
                chats_shared_in.append({
                    "chat_id": cid,
                    "chat_name": chat_names[cid],
                    "share_count": count,
                })
        chats_shared_in.sort(key=lambda x: -x["share_count"])

        return {
            "handle": handle,
            "display_name": display_name,
            "is_verified": is_verified,
            "tweet_count": len(metas),
            "total_favorites": total_favorites,
            "total_retweets": total_retweets,
            "total_views": total_views,
            "first_seen": first_seen,
            "last_seen": last_seen,
            "top_hashtags": [{"tag": t, "count": c} for t, c in hashtag_counter.most_common(10)],
            "top_mentioned": [{"handle": h, "count": c} for h, c in mention_counter.most_common(10)],
            "chats_shared_in": chats_shared_in,
        }

    @app.get("/api/authors/{handle}/tweets")
    def get_author_tweets(
        handle: str,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        order: str = Query("date"),
    ) -> dict:
        total, rows = get_tweets_by_author(db, handle, page, page_size, order)
        if total == 0:
            raise HTTPException(status_code=404, detail="Author not found")
        return {
            "handle": handle,
            "total": total,
            "page": page,
            "page_size": page_size,
            "tweets": [_row_to_dict(r) for r in rows],
        }

    # ── Hashtag + mention endpoints (I2) ─────────────────────────────────────

    @app.get("/api/hashtags")
    def get_hashtags(limit: int = Query(50, ge=1, le=500)) -> list[dict]:
        return [dict(r) for r in list_hashtags(db, limit)]

    @app.get("/api/hashtags/{tag}")
    def get_hashtag(tag: str) -> dict:
        from collections import Counter
        total, rows = get_tweets_by_hashtag(db, tag, page=1, page_size=10000)
        if total == 0:
            raise HTTPException(status_code=404, detail="Hashtag not found")

        import json as _json
        author_counter: Counter = Counter()
        first_seen: str | None = None
        last_seen: str | None = None
        for row in rows:
            h = None
            try:
                h = _json.loads(row["tweet_meta"] or "{}").get("author_handle")
            except (ValueError, TypeError):
                pass
            if h:
                author_counter[h] += 1
            fa = row["fetched_at"]
            if fa:
                if not first_seen or fa < first_seen:
                    first_seen = fa
                if not last_seen or fa > last_seen:
                    last_seen = fa

        tweet_urls = {row["url"] for row in rows}
        chat_counter: Counter = Counter()
        for cid, export in chats.items():
            count = sum(1 for lnk in export.links if lnk.url in tweet_urls)
            if count > 0:
                chat_counter[cid] = count

        return {
            "tag": tag,
            "count": total,
            "first_seen": first_seen,
            "last_seen": last_seen,
            "top_authors": [{"handle": h, "count": c} for h, c in author_counter.most_common(10)],
            "top_chats": [
                {"chat_id": cid, "chat_name": chat_names[cid], "count": c}
                for cid, c in chat_counter.most_common(10)
            ],
        }

    @app.get("/api/hashtags/{tag}/tweets")
    def get_hashtag_tweets(
        tag: str,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        order: str = Query("date"),
    ) -> dict:
        total, rows = get_tweets_by_hashtag(db, tag, page, page_size, order)
        if total == 0:
            raise HTTPException(status_code=404, detail="Hashtag not found")
        return {
            "tag": tag,
            "total": total,
            "page": page,
            "page_size": page_size,
            "tweets": [_row_to_dict(r) for r in rows],
        }

    @app.get("/api/mentions")
    def get_mentions(limit: int = Query(50, ge=1, le=500)) -> list[dict]:
        return [dict(r) for r in list_mentions(db, limit)]

    @app.get("/api/mentions/{handle}")
    def get_mention(handle: str) -> dict:
        from collections import Counter
        total, rows = get_tweets_by_mention(db, handle, page=1, page_size=10000)
        if total == 0:
            raise HTTPException(status_code=404, detail="Mention not found")

        import json as _json
        author_counter: Counter = Counter()
        first_seen: str | None = None
        last_seen: str | None = None
        for row in rows:
            h = None
            try:
                h = _json.loads(row["tweet_meta"] or "{}").get("author_handle")
            except (ValueError, TypeError):
                pass
            if h:
                author_counter[h] += 1
            fa = row["fetched_at"]
            if fa:
                if not first_seen or fa < first_seen:
                    first_seen = fa
                if not last_seen or fa > last_seen:
                    last_seen = fa

        return {
            "handle": handle,
            "count": total,
            "first_seen": first_seen,
            "last_seen": last_seen,
            "top_mentioners": [{"handle": h, "count": c} for h, c in author_counter.most_common(10)],
        }

    @app.get("/api/mentions/{handle}/tweets")
    def get_mention_tweets(
        handle: str,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        order: str = Query("date"),
    ) -> dict:
        total, rows = get_tweets_by_mention(db, handle, page, page_size, order)
        if total == 0:
            raise HTTPException(status_code=404, detail="Mention not found")
        return {
            "handle": handle,
            "total": total,
            "page": page,
            "page_size": page_size,
            "tweets": [_row_to_dict(r) for r in rows],
        }

    # ── Aggregation endpoints (I4) ───────────────────────────────────────────

    def _window_since(window: str, now_str: str = "") -> str | None:
        from datetime import date, timedelta
        if not window or window == "all":
            return None
        try:
            now = date.fromisoformat(now_str) if now_str else date.today()
            days = {"7d": 7, "30d": 30, "90d": 90}.get(window)
            return (now - timedelta(days=days)).isoformat() if days else None
        except (ValueError, TypeError):
            return None

    @app.get("/api/aggregations/top-authors")
    def agg_top_authors(
        window: str = Query("all"),
        limit: int = Query(20, ge=1, le=100),
        now: str = Query(""),
        weighted: str = Query(""),
    ) -> list[dict]:
        since = _window_since(window, now)
        use_weighted = weighted == "engagement"
        return [dict(r) for r in list_top_authors_windowed(db, limit, since, weighted=use_weighted)]

    @app.get("/api/aggregations/top-hashtags")
    def agg_top_hashtags(
        window: str = Query("all"),
        limit: int = Query(20, ge=1, le=100),
        now: str = Query(""),
        weighted: str = Query(""),
    ) -> list[dict]:
        since = _window_since(window, now)
        use_weighted = weighted == "engagement"
        return [dict(r) for r in list_top_hashtags_windowed(db, limit, since, weighted=use_weighted)]

    # ── Author profiles (E5) ──────────────────────────────────────────────────

    @app.post("/api/authors/profiles/rebuild")
    async def rebuild_author_profiles() -> dict:
        from .enrich.author_profile import rebuild_all_profiles
        n = await asyncio.to_thread(lambda: rebuild_all_profiles(db))
        return {"profiles_rebuilt": n}

    @app.get("/api/authors/profiles/{handle}")
    def get_author_profile_api(handle: str) -> dict:
        from .enrich.author_profile import get_author_profile
        profile = get_author_profile(db, handle)
        if profile is None:
            raise HTTPException(status_code=404, detail=f"No profile for @{handle}")
        return profile

    # ── Author influence + mention graph (E6) ────────────────────────────────

    @app.post("/api/authors/edges/rebuild")
    async def rebuild_author_edges_api() -> dict:
        from .enrich.author_graph import rebuild_author_edges
        n = await asyncio.to_thread(lambda: rebuild_author_edges(db))
        return {"edges_rebuilt": n}

    @app.get("/api/authors/{handle}/influence")
    def author_influence(handle: str) -> dict:
        from .enrich.author_graph import get_author_influence, ensure_schema
        ensure_schema(db)
        result = get_author_influence(db, handle)
        if result is None:
            raise HTTPException(status_code=404, detail=f"No influence data for @{handle}")
        return result

    @app.get("/api/research/author-graph")
    def research_author_graph(
        seed: str = Query(...),
        depth: int = Query(2, ge=1, le=3),
        kind: str = Query("all", pattern="^(all|mentions|quotes|replies)$"),
        max_nodes: int = Query(30, ge=5, le=80),
    ) -> dict:
        from .enrich.author_graph import get_author_graph, ensure_schema
        ensure_schema(db)
        graph = get_author_graph(db, seed, depth=depth, kind=kind, max_nodes=max_nodes)
        if graph is None:
            raise HTTPException(status_code=404, detail=f"No graph data for @{seed}")
        return graph

    @app.get("/api/aggregations/top-entities")
    def agg_top_entities(
        window: str = Query("all"),
        limit: int = Query(20, ge=1, le=100),
        now: str = Query(""),
    ) -> list[dict]:
        since = _window_since(window, now)
        return [dict(r) for r in list_top_entities_windowed(db, limit, since)]

    @app.get("/api/aggregations/top-tweets")
    def agg_top_tweets(
        by: str = Query("favorite_count"),
        window: str = Query("all"),
        limit: int = Query(10, ge=1, le=50),
        now: str = Query(""),
    ) -> list[dict]:
        since = _window_since(window, now)
        rows = list_top_tweets_windowed(db, by, limit, since)
        return [_row_to_dict(r) for r in rows]

    @app.get("/api/aggregations/sentiment")
    def agg_sentiment(
        window: str = Query("all"),
        group_by: str = Query("author"),
        now: str = Query(""),
    ) -> list[dict]:
        import json as _json
        since = _window_since(window, now)
        rows = list_sentiment_rows(db, since)

        groups: dict[str, dict] = {}

        if group_by == "hashtag":
            for row in rows:
                try:
                    meta = _json.loads(row["tweet_meta"] or "{}")
                    sentiment = row["sentiment"] or "n/a"
                    for tag in meta.get("hashtags") or []:
                        if tag not in groups:
                            groups[tag] = {"key": tag, "positive": 0, "negative": 0, "neutral": 0, "n/a": 0}
                        groups[tag][sentiment] = groups[tag].get(sentiment, 0) + 1
                except (ValueError, TypeError):
                    pass
        elif group_by == "chat":
            url_to_chat: dict[str, str] = {}
            for cid, export in chats.items():
                for lnk in export.links:
                    if lnk.url not in url_to_chat:
                        url_to_chat[lnk.url] = chat_names[cid]
            for row in rows:
                chat_name = url_to_chat.get(row["url"], "unknown")
                sentiment = row["sentiment"] or "n/a"
                if chat_name not in groups:
                    groups[chat_name] = {"key": chat_name, "positive": 0, "negative": 0, "neutral": 0, "n/a": 0}
                groups[chat_name][sentiment] = groups[chat_name].get(sentiment, 0) + 1
        else:  # author
            for row in rows:
                try:
                    meta = _json.loads(row["tweet_meta"] or "{}")
                    handle = meta.get("author_handle") or "unknown"
                    sentiment = row["sentiment"] or "n/a"
                    if handle not in groups:
                        groups[handle] = {"key": handle, "positive": 0, "negative": 0, "neutral": 0, "n/a": 0}
                    groups[handle][sentiment] = groups[handle].get(sentiment, 0) + 1
                except (ValueError, TypeError):
                    pass

        result = sorted(groups.values(), key=lambda x: -(x["positive"] + x["negative"] + x["neutral"] + x.get("n/a", 0)))
        return result

    # ── Time-series endpoints (I9) ────────────────────────────────────────────

    _MAX_BUCKETS = 60

    @app.get("/api/timeseries/messages")
    def ts_messages(
        group_by: str = Query("month"),
        chat_id: str = Query(""),
        author: str = Query(""),
        from_date: str = Query(""),
        to_date: str = Query(""),
    ) -> list[dict]:
        from collections import defaultdict
        fmt = {"day": "%Y-%m-%d", "week": "%Y-W%W", "month": "%Y-%m"}.get(group_by, "%Y-%m")
        counts: dict[str, int] = defaultdict(int)
        targets = {chat_id: chats[chat_id]} if chat_id and chat_id in chats else chats
        for export in targets.values():
            for entry in export.entries:
                if not isinstance(entry, Message):
                    continue
                if author and author.lower() not in entry.sender.lower():
                    continue
                d = entry.timestamp.date().isoformat()
                if from_date and d < from_date:
                    continue
                if to_date and d > to_date:
                    continue
                bucket = entry.timestamp.strftime(fmt)
                counts[bucket] += 1
        result = [{"bucket": b, "value": v} for b, v in sorted(counts.items())]
        return result[-_MAX_BUCKETS:]

    @app.get("/api/timeseries/tweets")
    def ts_tweets(
        group_by: str = Query("month"),
        author: str = Query(""),
        hashtag: str = Query(""),
        ticker: str = Query(""),
        from_date: str = Query(""),
        to_date: str = Query(""),
        metric: str = Query("count"),
    ) -> list[dict]:
        rows = timeseries_tweets(
            db,
            group_by=group_by,
            author=author or None,
            hashtag=hashtag or None,
            ticker=ticker or None,
            from_date=from_date or None,
            to_date=to_date or None,
            metric=metric,
        )
        result = [{"bucket": r["bucket"], "value": r["value"]} for r in rows if r["bucket"]]
        return result[-_MAX_BUCKETS:]

    @app.get("/api/timeseries/sentiment")
    def ts_sentiment(
        group_by: str = Query("month"),
        author: str = Query(""),
        hashtag: str = Query(""),
        from_date: str = Query(""),
        to_date: str = Query(""),
    ) -> list[dict]:
        rows = timeseries_sentiment(
            db,
            group_by=group_by,
            author=author or None,
            hashtag=hashtag or None,
            from_date=from_date or None,
            to_date=to_date or None,
        )
        result = [
            {"bucket": r["bucket"], "positive": r["positive"], "negative": r["negative"], "neutral": r["neutral"]}
            for r in rows if r["bucket"]
        ]
        return result[-_MAX_BUCKETS:]

    @app.post("/api/admin/backfill-tickers")
    def run_backfill_tickers() -> dict:
        count = backfill_tickers(db)
        return {"updated": count}

    # ── Senders endpoint (I3) ────────────────────────────────────────────────

    @app.get("/api/senders")
    def get_senders(chat_id: str = Query("")) -> list[str]:
        from collections import Counter
        counter: Counter = Counter()
        if chat_id:
            if chat_id not in chats:
                return []
            targets = {chat_id: chats[chat_id]}
        else:
            targets = chats
        for export in targets.values():
            for entry in export.entries:
                if isinstance(entry, Message):
                    counter[entry.sender] += 1
        return [s for s, _ in counter.most_common()]

    # ── Entity export endpoints ───────────────────────────────────────────────

    @app.get("/api/export/entities.json")
    def export_entities_json() -> list[dict]:
        import json as _json
        rows = get_articles_with_entities(db)
        result = []
        for r in rows:
            ents = _json.loads(r["entities"] or "[]")
            for ent in ents:
                result.append({
                    "article_id": r["id"],
                    "url": r["url"],
                    "title": r["title"],
                    "published_at": r["published_at"],
                    "entity_name": ent.get("name"),
                    "entity_kind": ent.get("kind"),
                    "entity_mention": ent.get("mention_text"),
                })
        return result

    @app.get("/api/export/entities.csv")
    def export_entities_csv():
        import csv
        import io
        import json as _json
        from fastapi.responses import StreamingResponse
        rows = get_articles_with_entities(db)
        buf = io.StringIO()
        writer = csv.DictWriter(
            buf,
            fieldnames=["article_id", "url", "title", "published_at",
                        "entity_name", "entity_kind", "entity_mention"],
        )
        writer.writeheader()
        for r in rows:
            ents = _json.loads(r["entities"] or "[]")
            for ent in ents:
                writer.writerow({
                    "article_id": r["id"],
                    "url": r["url"],
                    "title": r["title"],
                    "published_at": r["published_at"],
                    "entity_name": ent.get("name"),
                    "entity_kind": ent.get("kind"),
                    "entity_mention": ent.get("mention_text"),
                })
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=entities.csv"},
        )

    # ── Keyword bundle export (F5) ────────────────────────────────────────────

    @app.get("/api/export/keyword")
    def export_keyword(
        q: str = Query("", min_length=0),
        chat_id: str = Query(""),
        from_date: str = Query(""),
        to_date: str = Query(""),
        format: str = Query("md"),
        include_articles: int = Query(1),
        fuzzy: int = Query(0),
    ):
        import csv as _csv
        import io
        import json as _json
        from fastapi.responses import StreamingResponse
        from datetime import date as _date

        q_lower = q.strip().lower()
        slug = _SLUG_RE.sub("-", q_lower)[:40] if q_lower else (
            _SLUG_RE.sub("-", chat_names.get(chat_id, "all"))[:40] if chat_id else "all-messages"
        )
        today = datetime.utcnow().strftime("%Y%m%d")
        filename = f"{slug}-{today}"

        # Collect messages
        msg_hits: list[dict] = []
        targets = {chat_id: chats[chat_id]} if chat_id and chat_id in chats else chats
        for cid, export in targets.items():
            for entry in export.entries:
                if not isinstance(entry, Message):
                    continue
                if q_lower:
                    body_low = entry.body.lower()
                    match = q_lower in body_low
                    if not match and fuzzy:
                        try:
                            from rapidfuzz import fuzz as _fuzz
                            match = _fuzz.partial_ratio(q_lower, body_low) >= 70
                        except ImportError:
                            pass
                    if not match:
                        continue
                ts = entry.timestamp
                if from_date and ts.date() < _date.fromisoformat(from_date):
                    continue
                if to_date and ts.date() > _date.fromisoformat(to_date):
                    continue
                msg_hits.append({
                    "ts": ts,
                    "ts_str": ts.isoformat(),
                    "chat_id": cid,
                    "chat_name": chat_names[cid],
                    "sender": entry.sender,
                    "body": entry.body,
                })

        # Collect articles
        article_hits: list[dict] = []
        if include_articles:
            if q_lower:
                # Keyword-scoped: FTS + enrichment summary search
                try:
                    art_rows = get_enriched_articles_for_keyword(db, q, from_date or None, to_date or None)
                    for row in art_rows:
                        article_hits.append({
                            "id": row["id"],
                            "url": row["url"],
                            "title": row["title"],
                            "published_at": row["published_at"],
                            "raw_text": row["raw_text"] or "",
                            "summary": row["summary"] or "",
                            "categories": row["categories"] or "",
                            "sentiment": row["sentiment"] or "",
                        })
                except Exception:
                    pass
            elif chat_id and chat_id in chats:
                # No keyword: include articles scraped from this chat's links
                link_urls = [lnk.url for lnk in chats[chat_id].links]
                if link_urls:
                    url_rows = get_articles_by_urls(db, link_urls)
                    for url, row in url_rows.items():
                        if row["status"] != "ok":
                            continue
                        article_hits.append({
                            "id": row["id"],
                            "url": row["url"],
                            "title": row["title"] or url,
                            "published_at": row["published_at"] or "",
                            "raw_text": row["raw_text"] or "",
                            "summary": "",
                            "categories": "",
                            "sentiment": "",
                        })

        # Build URL → article map for message linking
        url_to_article = {a["url"]: a for a in article_hits}

        if format == "json":
            data = _json.dumps({"query": q, "messages": msg_hits, "articles": article_hits}, default=str)
            return StreamingResponse(
                io.BytesIO(data.encode()),
                media_type="application/json",
                headers={"Content-Disposition": f"attachment; filename={filename}.json"},
            )

        if format == "csv":
            buf = io.StringIO()
            writer = _csv.DictWriter(
                buf,
                fieldnames=["timestamp", "chat", "sender", "body", "url", "article_title", "article_summary"],
            )
            writer.writeheader()
            for m in msg_hits:
                # Find any URL in body that we have an article for
                url = next((u for u in url_to_article if u in m["body"]), "")
                art = url_to_article.get(url, {})
                writer.writerow({
                    "timestamp": m["ts_str"],
                    "chat": m["chat_name"],
                    "sender": m["sender"],
                    "body": m["body"],
                    "url": url,
                    "article_title": art.get("title", ""),
                    "article_summary": art.get("summary", ""),
                })
            buf.seek(0)
            return StreamingResponse(
                buf,
                media_type="text/csv",
                headers={"Content-Disposition": f"attachment; filename={filename}.csv"},
            )

        # Markdown (default)
        lines: list[str] = [
            f'# Keyword bundle: "{q}"',
            f"_Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} · "
            f"{len(msg_hits)} message{'s' if len(msg_hits) != 1 else ''} · "
            f"{len(article_hits)} article{'s' if len(article_hits) != 1 else ''}_",
            "",
        ]

        all_items: list[dict] = []
        for m in msg_hits:
            all_items.append({"kind": "message", "ts": m["ts"], **m})
        for a in article_hits:
            if a.get("published_at"):
                try:
                    a_ts = datetime.fromisoformat(a["published_at"][:19])
                except ValueError:
                    a_ts = datetime.utcnow()
            else:
                a_ts = datetime.utcnow()
            all_items.append({"kind": "article", "ts": a_ts, **a})
        all_items.sort(key=lambda x: x["ts"])

        for item in all_items:
            lines.append("---")
            if item["kind"] == "message":
                lines.append(f"## {item['ts'].strftime('%Y-%m-%d %H:%M')} · {item['chat_name']} · {item['sender']}")
                lines.append("")
                lines.append(item["body"])
                url_in_body = next((u for u in url_to_article if u in item["body"]), None)
                if url_in_body:
                    art = url_to_article[url_in_body]
                    summary = art.get("summary") or (art.get("raw_text") or "")[:300]
                    lines.append("")
                    lines.append(f"[Source link]({url_in_body}) — *{art.get('title', '')}*")
                    lines.append(f"> {summary}")
            else:
                lines.append(f"## {item['ts'].strftime('%Y-%m-%d %H:%M')} · Article · {item.get('title', '')}")
                lines.append("")
                lines.append(f"URL: {item['url']}")
                summary = item.get("summary") or (item.get("raw_text") or "")[:300]
                if summary:
                    lines.append("")
                    lines.append(f"> {summary}")
            lines.append("")

        md = "\n".join(lines)
        return StreamingResponse(
            io.BytesIO(md.encode("utf-8")),
            media_type="text/markdown",
            headers={"Content-Disposition": f"attachment; filename={filename}.md"},
        )

    # ── Scoped bundle exports (I14) ──────────────────────────────────────────

    def _collect_msgs_for_urls(
        url_set: set[str],
        from_date: str = "",
        to_date: str = "",
        restrict_chat_id: str = "",
    ) -> list[dict]:
        from datetime import date as _date
        hits: list[dict] = []
        targets = {restrict_chat_id: chats[restrict_chat_id]} if restrict_chat_id and restrict_chat_id in chats else chats
        for cid, export in targets.items():
            for entry in export.entries:
                if not isinstance(entry, Message):
                    continue
                body = entry.body or ""
                if not any(u in body for u in url_set):
                    continue
                ts = entry.timestamp
                if from_date and ts.date() < _date.fromisoformat(from_date):
                    continue
                if to_date and ts.date() > _date.fromisoformat(to_date):
                    continue
                hits.append({
                    "ts": ts,
                    "ts_str": ts.isoformat(),
                    "chat_id": cid,
                    "chat_name": chat_names[cid],
                    "sender": entry.sender,
                    "body": body,
                })
        return hits

    def _rows_to_export_dicts(rows) -> list[dict]:
        return [
            {
                "id": r["id"],
                "url": r["url"],
                "title": r["title"] or "",
                "published_at": r["published_at"] or "",
                "raw_text": r["raw_text"] or "",
                "summary": "",
            }
            for r in rows
        ]

    @app.get("/api/export/author/{handle}")
    def export_by_author(
        handle: str,
        format: str = Query("md"),
        from_date: str = Query(""),
        to_date: str = Query(""),
        include_articles: int = Query(1),
    ):
        rows = get_articles_for_export(db, from_date=from_date or None, to_date=to_date or None, author=handle)
        if not rows:
            raise HTTPException(status_code=404, detail=f"No articles found for author '{handle}'")
        article_hits = _rows_to_export_dicts(rows) if include_articles else []
        url_set = {a["url"] for a in article_hits}
        msg_hits = _collect_msgs_for_urls(url_set, from_date, to_date)
        slug = _SLUG_RE.sub("-", f"author-{handle.lower()}")[:40]
        return _export_bundle(f"@{handle}", slug, msg_hits, article_hits, format)

    @app.get("/api/export/hashtag/{tag}")
    def export_by_hashtag(
        tag: str,
        format: str = Query("md"),
        from_date: str = Query(""),
        to_date: str = Query(""),
        include_articles: int = Query(1),
    ):
        rows = get_articles_for_export(db, from_date=from_date or None, to_date=to_date or None, hashtag=tag)
        if not rows:
            raise HTTPException(status_code=404, detail=f"No articles found for hashtag '#{tag}'")
        article_hits = _rows_to_export_dicts(rows) if include_articles else []
        url_set = {a["url"] for a in article_hits}
        msg_hits = _collect_msgs_for_urls(url_set, from_date, to_date)
        slug = _SLUG_RE.sub("-", f"hashtag-{tag.lower()}")[:40]
        return _export_bundle(f"#{tag}", slug, msg_hits, article_hits, format)

    @app.get("/api/export/mention/{handle}")
    def export_by_mention(
        handle: str,
        format: str = Query("md"),
        from_date: str = Query(""),
        to_date: str = Query(""),
        include_articles: int = Query(1),
    ):
        rows = get_articles_for_export(db, from_date=from_date or None, to_date=to_date or None, mention=handle)
        if not rows:
            raise HTTPException(status_code=404, detail=f"No articles found mentioning '@{handle}'")
        article_hits = _rows_to_export_dicts(rows) if include_articles else []
        url_set = {a["url"] for a in article_hits}
        msg_hits = _collect_msgs_for_urls(url_set, from_date, to_date)
        slug = _SLUG_RE.sub("-", f"mention-{handle.lower()}")[:40]
        return _export_bundle(f"@{handle} mentions", slug, msg_hits, article_hits, format)

    @app.get("/api/export/ticker/{symbol}")
    def export_by_ticker(
        symbol: str,
        format: str = Query("md"),
        from_date: str = Query(""),
        to_date: str = Query(""),
        include_articles: int = Query(1),
    ):
        rows = get_articles_for_export(db, from_date=from_date or None, to_date=to_date or None, ticker=symbol.upper())
        if not rows:
            raise HTTPException(status_code=404, detail=f"No articles found for ticker '${symbol.upper()}'")
        article_hits = _rows_to_export_dicts(rows) if include_articles else []
        url_set = {a["url"] for a in article_hits}
        msg_hits = _collect_msgs_for_urls(url_set, from_date, to_date)
        slug = _SLUG_RE.sub("-", f"ticker-{symbol.lower()}")[:40]
        return _export_bundle(f"${symbol.upper()}", slug, msg_hits, article_hits, format)

    @app.get("/api/export/timerange")
    def export_timerange(
        from_date: str = Query(""),
        to_date: str = Query(""),
        chat_id: str = Query(""),
        format: str = Query("md"),
        include_articles: int = Query(1),
    ):
        from datetime import date as _date
        targets = {chat_id: chats[chat_id]} if chat_id and chat_id in chats else chats
        msg_hits: list[dict] = []
        for cid, export in targets.items():
            for entry in export.entries:
                if not isinstance(entry, Message):
                    continue
                ts = entry.timestamp
                if from_date and ts.date() < _date.fromisoformat(from_date):
                    continue
                if to_date and ts.date() > _date.fromisoformat(to_date):
                    continue
                msg_hits.append({
                    "ts": ts,
                    "ts_str": ts.isoformat(),
                    "chat_id": cid,
                    "chat_name": chat_names[cid],
                    "sender": entry.sender,
                    "body": entry.body or "",
                })

        article_hits: list[dict] = []
        if include_articles:
            rows = get_articles_in_timerange(db, from_date or None, to_date or None)
            article_hits = _rows_to_export_dicts(rows)

        label_parts = []
        if from_date:
            label_parts.append(f"from {from_date}")
        if to_date:
            label_parts.append(f"to {to_date}")
        label = "Time range: " + (" ".join(label_parts) if label_parts else "all")
        date_slug = f"{from_date or 'start'}--{to_date or 'end'}"
        slug = _SLUG_RE.sub("-", f"timerange-{date_slug}")[:40]
        return _export_bundle(label, slug, msg_hits, article_hits, format)

    @app.get("/api/export/chat/{chat_id_param}")
    def export_chat(
        chat_id_param: str,
        from_date: str = Query(""),
        to_date: str = Query(""),
        format: str = Query("md"),
        include_articles: int = Query(1),
    ):
        if chat_id_param not in chats:
            raise HTTPException(status_code=404, detail=f"Chat '{chat_id_param}' not found")
        from datetime import date as _date
        export = chats[chat_id_param]
        msg_hits: list[dict] = []
        for entry in export.entries:
            if not isinstance(entry, Message):
                continue
            ts = entry.timestamp
            if from_date and ts.date() < _date.fromisoformat(from_date):
                continue
            if to_date and ts.date() > _date.fromisoformat(to_date):
                continue
            msg_hits.append({
                "ts": ts,
                "ts_str": ts.isoformat(),
                "chat_id": chat_id_param,
                "chat_name": chat_names[chat_id_param],
                "sender": entry.sender,
                "body": entry.body or "",
            })

        article_hits: list[dict] = []
        if include_articles:
            link_urls = [lnk.url for lnk in export.links]
            if link_urls:
                url_rows = get_articles_by_urls(db, link_urls)
                article_hits = [
                    {
                        "id": r["id"],
                        "url": r["url"],
                        "title": r["title"] or "",
                        "published_at": r["published_at"] or "",
                        "raw_text": r["raw_text"] or "",
                        "summary": "",
                    }
                    for r in url_rows.values()
                    if r["status"] == "ok"
                ]

        name = chat_names.get(chat_id_param, chat_id_param)
        slug = _SLUG_RE.sub("-", f"chat-{name.lower()}")[:40]
        return _export_bundle(f"Chat: {name}", slug, msg_hits, article_hits, format)

    @app.get("/api/export/entity/{entity_name:path}")
    def export_by_entity(
        entity_name: str,
        format: str = Query("md"),
        from_date: str = Query(""),
        to_date: str = Query(""),
        include_articles: int = Query(1),
    ):
        from .scrape.db import get_articles_for_entity as _get_entity_articles, list_entities as _list_entities

        norm = entity_name.strip().lower()
        ent_row = db.execute(
            "SELECT id, name FROM entities WHERE normalized_name = ? AND canonical_id IS NULL", (norm,)
        ).fetchone()
        if not ent_row:
            ent_row = db.execute(
                "SELECT id, name FROM entities WHERE normalized_name LIKE ? AND canonical_id IS NULL LIMIT 1",
                (f"%{norm}%",),
            ).fetchone()
        if not ent_row:
            raise HTTPException(status_code=404, detail=f"Entity '{entity_name}' not found")

        result = _get_entity_articles(db, ent_row["id"], page=1, page_size=2000)
        rows = result["articles"]
        article_hits: list[dict] = []
        if include_articles:
            article_hits = [
                {
                    "id": r["id"],
                    "url": r["url"],
                    "title": r["title"] or "",
                    "published_at": r.get("published_at") or "",
                    "raw_text": r.get("raw_text") or "",
                    "summary": "",
                }
                for r in rows
                if r.get("status") == "ok" or r.get("url")
            ]
            if from_date or to_date:
                from datetime import date as _date
                def _in_range(a: dict) -> bool:
                    pub = a.get("published_at", "")
                    if not pub:
                        return True
                    try:
                        d = _date.fromisoformat(pub[:10])
                        if from_date and d < _date.fromisoformat(from_date):
                            return False
                        if to_date and d > _date.fromisoformat(to_date):
                            return False
                    except ValueError:
                        pass
                    return True
                article_hits = [a for a in article_hits if _in_range(a)]

        url_set = {a["url"] for a in article_hits}
        msg_hits = _collect_msgs_for_urls(url_set, from_date, to_date)
        label = ent_row["name"]
        slug = _SLUG_RE.sub("-", f"entity-{norm}")[:40]
        return _export_bundle(label, slug, msg_hits, article_hits, format)

    @app.get("/api/export/article/{article_id}")
    def export_article(article_id: int, format: str = Query("md")):
        row = get_article_by_id(db, article_id)
        if not row:
            raise HTTPException(status_code=404, detail="Article not found")
        d = _row_to_dict(row)
        label = d.get("title") or d.get("url") or f"article-{article_id}"
        slug = _SLUG_RE.sub("-", (d.get("title") or f"article-{article_id}").lower())[:40]
        return _export_bundle(label, slug, [], [d], format)

    @app.get("/api/export/research_bin/{bin_id}")
    def export_research_bin(bin_id: int, format: str = Query("md")):
        bin_row = get_research_bin(db, bin_id)
        if not bin_row:
            raise HTTPException(status_code=404, detail="Research bin not found")
        items = get_research_bin_items(db, bin_id)
        article_hits: list[dict] = []
        for item in items:
            if item["target_kind"] == "article":
                row = get_article_by_id(db, int(item["target_id"]))
                if row:
                    article_hits.append(_row_to_dict(row))
        label = bin_row["name"]
        slug = _SLUG_RE.sub("-", f"bin-{label.lower()}")[:40]
        return _export_bundle(label, slug, [], article_hits, format)

    @app.get("/api/export/collection/{collection_id}")
    def export_collection(collection_id: int, format: str = Query("md")):
        col_row = get_collection(db, collection_id)
        if not col_row:
            raise HTTPException(status_code=404, detail="Collection not found")
        items = get_collection_items(db, collection_id, page=1, page_size=1000)
        article_hits = [_row_to_dict(r) for r in items]
        label = col_row["name"] if "name" in col_row.keys() else f"collection-{collection_id}"
        slug = _SLUG_RE.sub("-", f"collection-{str(label).lower()}")[:40]
        return _export_bundle(label, slug, [], article_hits, format)

    @app.get("/api/export/search_results")
    def export_search_results(
        q: str = Query(..., min_length=1),
        kind: str = Query("all"),
        limit: int = Query(50, ge=1, le=500),
        format: str = Query("md"),
    ):
        # Reuse the lexical search code path to assemble the hit set, then bundle.
        normalised_kind = {"articles": "article", "messages": "message"}.get(kind, kind)
        article_hits: list[dict] = []
        msg_hits: list[dict] = []
        # Articles via FTS5 helper.
        if normalised_kind in ("article", "all"):
            try:
                rows = keyword_search_with_snippet(db, q, limit)
                for row in rows:
                    article_hits.append(_row_to_dict(row))
            except Exception:
                pass
        # Messages via in-memory scan — shape matches what `_export_bundle` expects
        # (ts, ts_str, chat_id, chat_name, sender, body — same as _collect_msgs_for_urls).
        if normalised_kind in ("message", "all"):
            q_low = q.lower()
            for cid, export in chats.items():
                for entry in export.entries:
                    if not isinstance(entry, Message):
                        continue
                    body = entry.body or ""
                    if q_low not in body.lower():
                        continue
                    ts = entry.timestamp
                    msg_hits.append({
                        "ts": ts,
                        "ts_str": ts.isoformat(),
                        "chat_id": cid,
                        "chat_name": chat_names[cid],
                        "sender": entry.sender,
                        "body": body,
                    })
                    if len(msg_hits) >= limit:
                        break
                if len(msg_hits) >= limit:
                    break
        label = f"search-{q}"
        slug = _SLUG_RE.sub("-", f"search-{q.lower()}")[:40]
        return _export_bundle(label, slug, msg_hits, article_hits, format)

    class _PreviewRequest(BaseModel):
        scope: str = "keyword"
        q: str = ""
        template: str = "raw_dump"
        from_date: str = ""
        to_date: str = ""
        include_articles: bool = True
        chat_id: str = ""
        fuzzy: bool = False

    _TEMPLATE_HEADERS: dict[str, str] = {
        "briefing": (
            "You are a research analyst. Using the exported messages and articles below, "
            "write a concise briefing: key developments, consensus views, and open questions.\n\n"
        ),
        "pros_cons": (
            "You are a balanced analyst. Review the material below and produce a structured "
            "Pros / Cons analysis with evidence from the sources.\n\n"
        ),
        "sentiment_timeline": (
            "You are a sentiment analyst. Review the messages and articles below in chronological order "
            "and produce a timeline of sentiment shifts with supporting quotes.\n\n"
        ),
        "raw_dump": "",
    }

    @app.post("/api/export/preview")
    def export_preview(body: _PreviewRequest):
        import io as _io
        import json as _json
        from datetime import date as _date

        scope = body.scope.strip()
        q = body.q.strip()
        fmt = "md"

        msg_hits: list[dict] = []
        article_hits: list[dict] = []

        if scope == "keyword":
            q_lower = q.lower()
            targets = {body.chat_id: chats[body.chat_id]} if body.chat_id and body.chat_id in chats else chats
            for cid, export in targets.items():
                for entry in export.entries:
                    if not isinstance(entry, Message):
                        continue
                    if q_lower:
                        body_low = entry.body.lower()
                        match = q_lower in body_low
                        if not match and body.fuzzy:
                            try:
                                from rapidfuzz import fuzz as _fuzz
                                match = _fuzz.partial_ratio(q_lower, body_low) >= 70
                            except ImportError:
                                pass
                        if not match:
                            continue
                    ts = entry.timestamp
                    if body.from_date and ts.date() < _date.fromisoformat(body.from_date):
                        continue
                    if body.to_date and ts.date() > _date.fromisoformat(body.to_date):
                        continue
                    msg_hits.append({
                        "ts": ts, "ts_str": ts.isoformat(),
                        "chat_id": cid, "chat_name": chat_names[cid],
                        "sender": entry.sender, "body": entry.body,
                    })
            if body.include_articles and q_lower:
                rows = get_enriched_articles_for_keyword(db, q, body.from_date or None, body.to_date or None)
                article_hits = [
                    {"id": r["id"], "url": r["url"], "title": r["title"] or "",
                     "published_at": r["published_at"] or "", "raw_text": r["raw_text"] or "",
                     "summary": r["summary"] or ""}
                    for r in rows
                ]
        elif scope in ("author", "hashtag", "mention", "ticker"):
            if not q:
                return {"markdown": "", "item_count": 0, "suggested_filename": "export.md", "template_used": body.template}
            kw = {
                "author": {"author": q},
                "hashtag": {"hashtag": q},
                "mention": {"mention": q},
                "ticker": {"ticker": q.upper()},
            }[scope]
            rows = get_articles_for_export(db, from_date=body.from_date or None, to_date=body.to_date or None, **kw)
            article_hits = _rows_to_export_dicts(rows) if body.include_articles else []
            url_set = {a["url"] for a in article_hits}
            msg_hits = _collect_msgs_for_urls(url_set, body.from_date, body.to_date)
        elif scope == "entity":
            if not q:
                return {"markdown": "", "item_count": 0, "suggested_filename": "export.md", "template_used": body.template}
            from .scrape.db import get_articles_for_entity as _gae
            norm = q.lower()
            ent_row = db.execute(
                "SELECT id, name FROM entities WHERE normalized_name = ? AND canonical_id IS NULL", (norm,)
            ).fetchone()
            if not ent_row:
                ent_row = db.execute(
                    "SELECT id, name FROM entities WHERE normalized_name LIKE ? AND canonical_id IS NULL LIMIT 1",
                    (f"%{norm}%",),
                ).fetchone()
            if not ent_row:
                raise HTTPException(status_code=404, detail=f"Entity '{q}' not found")
            result = _gae(db, ent_row["id"], page=1, page_size=2000)
            article_hits = [
                {"id": r["id"], "url": r["url"], "title": r["title"] or "",
                 "published_at": r.get("published_at") or "", "raw_text": r.get("raw_text") or "",
                 "summary": ""}
                for r in result["articles"]
            ] if body.include_articles else []
            url_set = {a["url"] for a in article_hits}
            msg_hits = _collect_msgs_for_urls(url_set, body.from_date, body.to_date)
        elif scope == "timerange":
            targets = {body.chat_id: chats[body.chat_id]} if body.chat_id and body.chat_id in chats else chats
            for cid, export in targets.items():
                for entry in export.entries:
                    if not isinstance(entry, Message):
                        continue
                    ts = entry.timestamp
                    if body.from_date and ts.date() < _date.fromisoformat(body.from_date):
                        continue
                    if body.to_date and ts.date() > _date.fromisoformat(body.to_date):
                        continue
                    msg_hits.append({
                        "ts": ts, "ts_str": ts.isoformat(),
                        "chat_id": cid, "chat_name": chat_names[cid],
                        "sender": entry.sender, "body": entry.body or "",
                    })
            if body.include_articles:
                rows = get_articles_in_timerange(db, body.from_date or None, body.to_date or None)
                article_hits = _rows_to_export_dicts(rows)

        # Build markdown string (reuse _export_bundle internals)
        generated = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        url_to_article = {a["url"]: a for a in article_hits}
        lines: list[str] = [
            f"# Export: {q or scope}",
            f"_Generated {generated} · "
            f"{len(msg_hits)} message{'s' if len(msg_hits) != 1 else ''} · "
            f"{len(article_hits)} article{'s' if len(article_hits) != 1 else ''}_",
            "",
        ]
        all_items: list[dict] = []
        for m in msg_hits:
            all_items.append({"kind": "message", "ts": m["ts"], **m})
        for a in article_hits:
            if a.get("published_at"):
                try:
                    a_ts = datetime.fromisoformat(a["published_at"][:19])
                except ValueError:
                    a_ts = datetime.utcnow()
            else:
                a_ts = datetime.utcnow()
            all_items.append({"kind": "article", "ts": a_ts, **a})
        all_items.sort(key=lambda x: x["ts"])
        for item in all_items:
            lines.append("---")
            if item["kind"] == "message":
                lines.append(
                    f"## {item['ts'].strftime('%Y-%m-%d %H:%M')} · {item.get('chat_name', '')} · {item.get('sender', '')}"
                )
                lines.append("")
                lines.append(item.get("body", ""))
                url_in_body = next((u for u in url_to_article if u in (item.get("body") or "")), None)
                if url_in_body:
                    art = url_to_article[url_in_body]
                    summary = art.get("summary") or (art.get("raw_text") or "")[:300]
                    lines.append("")
                    lines.append(f"[Source link]({url_in_body}) — *{art.get('title', '')}*")
                    lines.append(f"> {summary}")
            else:
                lines.append(f"## {item['ts'].strftime('%Y-%m-%d %H:%M')} · Article · {item.get('title', '')}")
                lines.append("")
                lines.append(f"URL: {item.get('url', '')}")
                summary = item.get("summary") or (item.get("raw_text") or "")[:300]
                if summary:
                    lines.append("")
                    lines.append(f"> {summary}")
            lines.append("")

        content_md = "\n".join(lines)
        template = body.template if body.template in _TEMPLATE_HEADERS else "raw_dump"
        header = _TEMPLATE_HEADERS[template]
        full_md = header + content_md

        today = datetime.utcnow().strftime("%Y%m%d")
        slug = _SLUG_RE.sub("-", q or scope)[:40] if (q or scope) else "export"
        suggested_filename = f"{slug}-{today}.md"

        return {
            "markdown": full_md,
            "item_count": len(msg_hits) + len(article_hits),
            "suggested_filename": suggested_filename,
            "template_used": template,
        }

    # ── RAG chatbot (F4) ──────────────────────────────────────────────────────

    @app.post("/api/chat/ask")
    async def chat_ask(req: _AskRequest):
        from fastapi.responses import StreamingResponse as _SR
        from .enrich.rag import build_context, stream_answer, _rag_fts_query
        from .scrape.db import get_articles_for_entity, list_entities
        import json as _json

        q = req.question
        k = max(1, min(req.k, 50))

        async def _generate():
            yield f"data: {_json.dumps({'phase': 'retrieving'})}\n\n"

            # Lexical message hits
            q_lower = q.lower()
            msg_hits: list[dict] = []
            targets = ({req.chat_id: chats[req.chat_id]} if req.chat_id and req.chat_id in chats else chats)
            for cid, export in targets.items():
                for entry in export.entries:
                    if not isinstance(entry, Message):
                        continue
                    if q_lower in entry.body.lower():
                        count = entry.body.lower().count(q_lower)
                        msg_hits.append({
                            "chat_id": cid,
                            "chat_name": chat_names[cid],
                            "ts": entry.timestamp.isoformat(),
                            "sender": entry.sender,
                            "body": entry.body,
                            "score": count * 0.5,
                        })
            msg_hits.sort(key=lambda x: -x["score"])

            art_hits: list[dict] = []
            seen_urls: set[str] = set()

            def _art_row_to_hit(row, score: float) -> dict | None:
                if row["url"] in seen_urls:
                    return None
                seen_urls.add(row["url"])
                enr = get_enrichment(db, row["id"])
                tweet_text = ""
                author_handle = ""
                raw_meta = row["tweet_meta"] if hasattr(row, "keys") else dict(row).get("tweet_meta")
                if raw_meta:
                    try:
                        tm = _json.loads(raw_meta)
                        tweet_text = tm.get("text") or ""
                        author_handle = tm.get("author_handle") or ""
                    except Exception:
                        pass
                return {
                    "article_id": row["id"],
                    "url": row["url"],
                    "title": row["title"],
                    "author_handle": author_handle,
                    "body": tweet_text or (row["raw_text"] or "")[:1000],
                    "tweet_meta": raw_meta,
                    "summary": _row_to_dict(enr).get("summary", "") if enr else "",
                    "score": score,
                }

            # Article FTS hits
            fts_q = _rag_fts_query(q)
            try:
                fts_rows = keyword_search(db, fts_q, k * 2)
                for row in fts_rows:
                    hit = _art_row_to_hit(row, -row["score"])
                    if hit:
                        art_hits.append(hit)
            except Exception:
                pass

            # Entity-aware retrieval
            try:
                all_entities = list_entities(db, limit=500)
                for ent in all_entities:
                    ent_name_lower = ent["name"].lower()
                    if ent_name_lower in q_lower and len(ent_name_lower) >= 3:
                        ent_arts = get_articles_for_entity(db, ent["id"], page=1, page_size=12)
                        for a in ent_arts["articles"]:
                            if a["url"] not in seen_urls:
                                seen_urls.add(a["url"])
                                art_hits.insert(0, {
                                    "article_id": a.get("id"),
                                    "url": a["url"],
                                    "title": a.get("title") or "",
                                    "author_handle": (a.get("tweet") or {}).get("author_handle") or "",
                                    "body": (a.get("tweet") or {}).get("text") or (a.get("raw_text") or "")[:1000],
                                    "tweet_meta": None,
                                    "summary": a.get("summary") or "",
                                    "score": 20.0,
                                })
                        break
            except Exception:
                pass

            # Semantic hits via Qdrant
            if qdrant is not None:
                try:
                    sem = await asyncio.to_thread(search_similar, qdrant, q, k, ollama_url)
                    for h in sem:
                        row = get_article_by_id(db, h["id"])
                        if row:
                            hit = _art_row_to_hit(row, h["score"] * 10)
                            if hit:
                                art_hits.append(hit)
                except Exception:
                    pass

            # Emit citations (top 5 art hits before LLM call)
            citations = [
                {
                    "article_id": a.get("article_id"),
                    "url": a["url"],
                    "title": a.get("title") or "",
                    "author_handle": a.get("author_handle") or "",
                    "snippet": (a.get("body") or "")[:200],
                    "score": a.get("score", 0),
                }
                for a in art_hits[:5]
            ]
            yield f"data: {_json.dumps({'phase': 'thinking', 'citations': citations})}\n\n"

            context_blocks = build_context(msg_hits[:k], art_hits[:k], max_chars=12000)

            try:
                for event in stream_answer(q, context_blocks, ollama_url):
                    yield event
            except Exception as exc:
                yield f"data: {_json.dumps({'error': str(exc)})}\n\n"
                yield "data: [DONE]\n\n"

        return _SR(
            _generate(),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
        )

    # ── Static files (must come last) ─────────────────────────────────────────
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

    return app
