"""Background coordinators that scrape (and optionally enrich) links idempotently.

Two independent async loops:
- `run_scrape_loop` — fast, concurrency-3, gets articles populated quickly.
- `run_enrich_loop` — slow, concurrency-1, fires Ollama summarisation per scraped article.

Both are idempotent: re-running on an already-scraped/enriched article is a no-op.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

import httpx

# Model name must match what `ollama list` shows. The worker's enrich/ollama.py
# defaults to "qwen2.5:3b" but our ollama-init service pulls "qwen2.5:3b-instruct".
# Override via env so compose can pin the exact tag.
_GEN_MODEL = os.environ.get("OLLAMA_GEN_MODEL", "qwen2.5:3b-instruct")

# Python's sqlite3 module is not thread-safe for concurrent commits on a single
# shared Connection. The workers' open_db() returns one shared connection with
# check_same_thread=False, so we serialize every write site through this lock.
_DB_LOCK = threading.Lock()

from .article import (
    BlockedPaywall,
    Gone404,
    TransientError,
    UnsupportedMediaType,
    scrape_generic,
)
from .db import (
    get_article_by_id,
    get_article_by_url,
    get_articles_with_unprocessed_media,
    get_engagement_refresh_candidates,
    get_enrichment,
    sync_tweet_entities,
    sync_tweet_links,
    resolve_dangling_tweet_links,
    upsert_article,
    upsert_enrichment,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ollama_models_ready(ollama_url: str, model_name: str = _GEN_MODEL) -> bool:
    """Return True only if Ollama is reachable AND has the exact model installed.

    Avoids hammering /api/chat with 404s during the model-pull warmup window.
    """
    try:
        resp = httpx.get(f"{ollama_url}/api/tags", timeout=3)
        resp.raise_for_status()
        models = resp.json().get("models", [])
    except Exception:
        return False
    return any((m.get("name") or "") == model_name for m in models)


def _safe_upsert_article(db: sqlite3.Connection, *args, **kwargs) -> int | None:
    """Lock + try/except wrapper so a DB error in one URL doesn't kill the loop."""
    try:
        with _DB_LOCK:
            return upsert_article(db, *args, **kwargs)
    except Exception:
        logger.exception("DB upsert_article failed for %s", args[0] if args else "?")
        return None


def _scrape_one_sync(url: str, db: sqlite3.Connection, force: bool = False) -> dict[str, Any]:
    """Scrape a single URL. Returns a result dict — never raises.

    When `force=True`, skip the "already ok" early-return so a manual retry
    (e.g. via the rescrape button) can re-fetch and overwrite the row.
    """
    with _DB_LOCK:
        existing = get_article_by_url(db, url)
    if not force and existing and existing["status"] == "ok":
        return {"url": url, "skipped": True, "article_id": existing["id"]}

    try:
        result = scrape_generic(url)
    except BlockedPaywall as exc:
        _safe_upsert_article(db, url, "blocked", error=str(exc))
        return {"url": url, "ok": False, "kind": "blocked", "error": str(exc)}
    except Gone404 as exc:
        _safe_upsert_article(db, url, "failed", error=str(exc))
        return {"url": url, "ok": False, "kind": "gone", "error": str(exc)}
    except UnsupportedMediaType as exc:
        _safe_upsert_article(db, url, "blocked", error=f"unsupported: {exc}")
        return {"url": url, "ok": False, "kind": "unsupported", "error": str(exc)}
    except TransientError as exc:
        _safe_upsert_article(db, url, "failed", error=str(exc))
        return {"url": url, "ok": False, "kind": "transient", "error": str(exc)}
    except Exception as exc:
        logger.exception("scrape failed for %s", url)
        _safe_upsert_article(db, url, "failed", error=str(exc))
        return {"url": url, "ok": False, "kind": "unexpected", "error": str(exc)}

    article_id = _safe_upsert_article(
        db,
        result.url,
        "ok",
        title=result.title,
        author=result.author,
        published_at=result.published_at.isoformat() if result.published_at else None,
        raw_text=result.raw_text,
        og_image=result.og_image_url,
        scrape_method=result.scrape_method,
        fetched_at=_now_iso(),
        tweet_meta=json.dumps(result.tweet) if result.tweet else None,
    )
    if article_id is None:
        return {"url": url, "ok": False, "kind": "db_error"}

    if result.tweet:
        try:
            with _DB_LOCK:
                sync_tweet_entities(db, article_id, result.tweet)
        except Exception:
            logger.exception("sync_tweet_entities failed for article %d", article_id)

        try:
            with _DB_LOCK:
                sync_tweet_links(db, article_id, result.tweet)
        except Exception:
            logger.exception("sync_tweet_links failed for article %d", article_id)

        # Fill in any dangling dst_article_id rows that were waiting for this tweet
        tweet_id = str(result.tweet.get("tweet_id") or "").strip()
        if tweet_id:
            try:
                with _DB_LOCK:
                    resolve_dangling_tweet_links(db, article_id, tweet_id)
            except Exception:
                logger.exception("resolve_dangling_tweet_links failed for article %d", article_id)

    return {"url": url, "ok": True, "article_id": article_id}


def _enrich_one_sync(
    article_id: int,
    db: sqlite3.Connection,
    ollama_url: str,
    qdrant=None,
) -> dict[str, Any]:
    """Enrich a single article via Ollama. Returns a result dict — never raises."""
    with _DB_LOCK:
        if get_enrichment(db, article_id):
            return {"article_id": article_id, "skipped": True}
        row = get_article_by_id(db, article_id)
    if not row:
        return {"article_id": article_id, "ok": False, "kind": "missing"}
    if row["status"] != "ok":
        return {"article_id": article_id, "ok": False, "kind": "not_scraped"}

    # Inline import — module is fine to import even when Ollama is unreachable.
    from ..enrich.ollama import EnrichError, enrich_article

    tweet_meta: dict | None = None
    try:
        raw_meta = row["tweet_meta"]
        if raw_meta:
            tweet_meta = json.loads(raw_meta)
    except (ValueError, TypeError, KeyError):
        pass

    try:
        result = enrich_article(dict(row), ollama_url, model=_GEN_MODEL, tweet_meta=tweet_meta)
    except EnrichError as exc:
        return {"article_id": article_id, "ok": False, "kind": "enrich_error", "error": str(exc)}
    except Exception as exc:
        logger.exception("enrich failed for %d", article_id)
        return {"article_id": article_id, "ok": False, "kind": "unexpected", "error": str(exc)}

    entities_list = [e.model_dump() for e in result.entities]
    try:
        with _DB_LOCK:
            upsert_enrichment(
                db,
                article_id=article_id,
                summary=result.summary,
                categories=json.dumps(result.categories),
                entities=json.dumps(entities_list),
                sentiment=result.sentiment,
                model=_GEN_MODEL,
                enriched_at=_now_iso(),
            )
    except Exception as exc:
        logger.exception("DB upsert_enrichment failed for %d", article_id)
        return {"article_id": article_id, "ok": False, "kind": "db_error", "error": str(exc)}

    try:
        from ..enrich.entity_sync import sync_llm_entities
        with _DB_LOCK:
            sync_llm_entities(db, article_id, entities_list)
    except Exception:
        logger.exception("sync_llm_entities failed for article %d", article_id)

    if qdrant is not None:
        try:
            from ..scrape.vector import upsert_article_vector
            title = dict(row).get("title") or ""
            text_for_embed = f"{title} {result.summary}".strip()
            upsert_article_vector(
                qdrant,
                article_id,
                text_for_embed,
                {"url": dict(row).get("url", ""), "title": title},
                ollama_url,
            )
        except Exception:
            logger.warning("Vector upsert failed for article %d", article_id)

    return {"article_id": article_id, "ok": True}


async def run_scrape_loop(
    urls: Iterable[str],
    db: sqlite3.Connection,
    progress: dict[str, Any],
    concurrency: int = 3,
) -> None:
    """Drain `urls` through `_scrape_one_sync` with bounded concurrency.

    `progress` is mutated in place so callers can observe live counts.
    """
    url_list = list(dict.fromkeys(urls))  # dedupe, preserve order
    progress.update({
        "phase": "running",
        "total": len(url_list),
        "done": 0,
        "ok": 0,
        "skipped": 0,
        "failed": 0,
        "started_at": _now_iso(),
    })

    sem = asyncio.Semaphore(concurrency)

    async def _one(url: str) -> None:
        async with sem:
            result = await asyncio.to_thread(_scrape_one_sync, url, db)
        progress["done"] += 1
        if result.get("skipped"):
            progress["skipped"] += 1
        elif result.get("ok"):
            progress["ok"] += 1
        else:
            progress["failed"] += 1

    try:
        await asyncio.gather(*(_one(u) for u in url_list))
        progress["phase"] = "done"
    except Exception as exc:
        logger.exception("scrape loop crashed")
        progress["phase"] = "error"
        progress["error"] = str(exc)
    finally:
        progress["finished_at"] = _now_iso()


async def run_enrich_loop(
    db: sqlite3.Connection,
    ollama_url: str,
    progress: dict[str, Any],
    concurrency: int = 1,
    poll_interval: int = 30,
    qdrant=None,
) -> None:
    """Continuously enrich any scraped article that lacks an enrichment row.

    Long-running: sleeps `poll_interval` seconds between sweeps so newly-scraped
    articles get picked up after the initial scrape loop finishes too.
    """
    progress.update({"phase": "waiting_for_model", "total": 0, "done": 0, "ok": 0, "failed": 0, "skipped": 0})
    sem = asyncio.Semaphore(concurrency)

    while True:
        if not _ollama_models_ready(ollama_url):
            progress["phase"] = "waiting_for_model"
            await asyncio.sleep(poll_interval)
            continue

        rows = db.execute(
            "SELECT a.id FROM articles a "
            "LEFT JOIN article_enrichments e ON e.article_id = a.id "
            "WHERE a.status='ok' AND e.article_id IS NULL"
        ).fetchall()
        ids = [r["id"] for r in rows]

        if not ids:
            progress["phase"] = "idle"
            await asyncio.sleep(poll_interval)
            continue

        progress["phase"] = "running"
        progress["total"] = progress.get("done", 0) + len(ids)

        async def _one(aid: int) -> None:
            async with sem:
                result = await asyncio.to_thread(_enrich_one_sync, aid, db, ollama_url, qdrant)
            progress["done"] += 1
            if result.get("skipped"):
                progress["skipped"] += 1
            elif result.get("ok"):
                progress["ok"] += 1
            else:
                progress["failed"] += 1
                progress["last_error"] = result.get("error", result.get("kind"))

        await asyncio.gather(*(_one(aid) for aid in ids))
        # Loop again to catch any newly-scraped rows.


def _refresh_one_sync(article_id: int, db: sqlite3.Connection) -> dict[str, Any]:
    """Re-scrape one article to update engagement stats. Never raises."""
    with _DB_LOCK:
        row = get_article_by_id(db, article_id)
    if not row:
        return {"article_id": article_id, "ok": False, "kind": "missing"}

    url = row["url"]
    try:
        result = scrape_generic(url)
    except (BlockedPaywall, Gone404, UnsupportedMediaType, TransientError) as exc:
        return {"article_id": article_id, "ok": False, "kind": "scrape_error", "error": str(exc)}
    except Exception as exc:
        logger.exception("engagement refresh failed for %s", url)
        return {"article_id": article_id, "ok": False, "kind": "unexpected", "error": str(exc)}

    if result.tweet is None:
        return {"article_id": article_id, "ok": False, "kind": "no_tweet"}

    tweet_meta = result.tweet.copy()
    tweet_meta["refreshed_at"] = _now_iso()

    _safe_upsert_article(
        db,
        result.url,
        "ok",
        title=result.title,
        author=result.author,
        published_at=result.published_at.isoformat() if result.published_at else None,
        raw_text=result.raw_text,
        og_image=result.og_image_url,
        scrape_method=result.scrape_method,
        fetched_at=row["fetched_at"],  # preserve original scrape time
        tweet_meta=json.dumps(tweet_meta),
    )
    return {"article_id": article_id, "ok": True}


async def run_engagement_refresh_loop(
    db: sqlite3.Connection,
    progress: dict[str, Any],
    concurrency: int = 2,
    poll_interval: int | None = None,
    batch_size: int | None = None,
) -> None:
    """Periodically re-scrape hot tweets to update engagement stats.

    Reads REFRESH_INTERVAL_MIN (default 720) and REFRESH_BATCH_SIZE (default 50)
    from the environment.  Respects the global _DB_LOCK and rate-limits to
    ~1 request per 2 seconds via the semaphore + thread executor.
    """
    _interval = int(os.environ.get("REFRESH_INTERVAL_MIN", "720")) * 60
    _batch = int(os.environ.get("REFRESH_BATCH_SIZE", "50"))
    if poll_interval is not None:
        _interval = poll_interval
    if batch_size is not None:
        _batch = batch_size

    progress.update({
        "phase": "idle",
        "total": 0,
        "done": 0,
        "ok": 0,
        "failed": 0,
        "skipped": 0,
        "last_run_at": None,
    })
    sem = asyncio.Semaphore(concurrency)

    while True:
        await asyncio.sleep(_interval)

        with _DB_LOCK:
            candidates = get_engagement_refresh_candidates(db, batch_size=_batch)
        ids = [r["id"] for r in candidates]

        if not ids:
            progress["phase"] = "idle"
            continue

        progress["phase"] = "running"
        progress["total"] = progress.get("done", 0) + len(ids)

        async def _one(aid: int) -> None:
            async with sem:
                await asyncio.sleep(2)  # ~1 req/2s rate limit
                result = await asyncio.to_thread(_refresh_one_sync, aid, db)
            progress["done"] += 1
            if result.get("ok"):
                progress["ok"] += 1
            else:
                progress["failed"] += 1

        await asyncio.gather(*(_one(aid) for aid in ids))
        progress["phase"] = "idle"
        progress["last_run_at"] = _now_iso()


def _digest_one_sync(
    period: str,
    end_date,
    db: sqlite3.Connection,
    ollama_url: str,
    chats: dict,
) -> dict[str, Any]:
    """Generate a digest for the given period. Never raises."""
    try:
        from ..enrich.digest import generate_digest
        return generate_digest(db, ollama_url, period=period, chats=chats, end_date=end_date)
    except Exception as exc:
        logger.exception("digest generation failed for %s/%s", period, end_date)
        return {"ok": False, "error": str(exc)}


async def run_digest_loop(
    db: sqlite3.Connection,
    ollama_url: str,
    progress: dict[str, Any],
    chats: dict,
    poll_interval: int = 600,
) -> None:
    """Generate daily/weekly digests on schedule.

    Polls every `poll_interval` seconds; generates:
    - daily digest at midnight UTC (if today's digest is missing)
    - weekly digest on Monday after 06:00 UTC
    """
    progress.update({"phase": "idle", "daily_generated": 0, "weekly_generated": 0})

    while True:
        try:
            now = datetime.now(timezone.utc)
            today = now.date()

            # Daily digest: generate if today's row is missing and it's past midnight
            from datetime import timedelta as _td
            yesterday = str(today - _td(days=1))
            daily_exists = db.execute(
                "SELECT 1 FROM digests WHERE period='daily' AND period_start=?",
                (yesterday,),
            ).fetchone()
            if not daily_exists and now.hour >= 0:
                result = await asyncio.to_thread(_digest_one_sync, "daily", today, db, ollama_url, chats)
                if result.get("id"):
                    progress["daily_generated"] += 1

            # Weekly digest: Monday after 06:00
            if now.weekday() == 0 and now.hour >= 6:
                week_start = str(today - _td(days=7))
                weekly_exists = db.execute(
                    "SELECT 1 FROM digests WHERE period='weekly' AND period_start=?",
                    (week_start,),
                ).fetchone()
                if not weekly_exists:
                    result = await asyncio.to_thread(_digest_one_sync, "weekly", today, db, ollama_url, chats)
                    if result.get("id"):
                        progress["weekly_generated"] += 1

            progress["phase"] = "idle"
        except Exception as exc:
            logger.exception("digest loop error")
            progress["last_error"] = str(exc)

        await asyncio.sleep(poll_interval)


def _ocr_one_sync(article_id: int, db: sqlite3.Connection) -> dict[str, Any]:
    """OCR a single article's media. Returns a result dict — never raises."""
    try:
        from ..enrich.ocr import ocr_article_media
        with _DB_LOCK:
            result = ocr_article_media(article_id, db)
        return result
    except Exception as exc:
        logger.exception("OCR failed for article %d", article_id)
        return {"article_id": article_id, "ok": False, "kind": "unexpected", "error": str(exc)}


async def run_ocr_loop(
    db: sqlite3.Connection,
    progress: dict[str, Any],
    concurrency: int = 2,
    poll_interval: int = 60,
) -> None:
    """Continuously OCR tweet media for articles that have unprocessed images.

    Self-disables if tesseract isn't installed. Without this, the loop spins
    forever generating 100+ failures per 10min that fill the logs with
    `tesseract is not installed or it's not in your PATH` warnings. Bug #43.
    """
    progress.update({"phase": "idle", "total": 0, "done": 0, "ok": 0, "failed": 0, "skipped": 0})

    # Pre-flight: check if tesseract is actually available.
    try:
        import pytesseract  # type: ignore
        pytesseract.get_tesseract_version()
    except Exception as exc:
        logger.warning(
            "OCR loop self-disabling: tesseract not available (%s). "
            "Set BACKGROUND_OCR=false to silence, or install tesseract in the image.",
            exc.__class__.__name__,
        )
        progress["phase"] = "disabled"
        progress["reason"] = "tesseract not installed"
        return

    sem = asyncio.Semaphore(concurrency)

    while True:
        rows = get_articles_with_unprocessed_media(db)
        ids = [r["id"] for r in rows]

        if not ids:
            progress["phase"] = "idle"
            await asyncio.sleep(poll_interval)
            continue

        progress["phase"] = "running"
        progress["total"] = progress.get("done", 0) + len(ids)

        async def _one(aid: int) -> None:
            async with sem:
                result = await asyncio.to_thread(_ocr_one_sync, aid, db)
            progress["done"] += 1
            if result.get("skipped"):
                progress["skipped"] += 1
            elif result.get("processed", 0) > 0:
                progress["ok"] += 1
            else:
                progress["failed"] += 1

        await asyncio.gather(*(_one(aid) for aid in ids))


async def run_topic_clustering_loop(
    db: sqlite3.Connection,
    qdrant,
    ollama_url: str,
    progress: dict[str, Any],
    poll_interval: int = 6 * 3600,
) -> None:
    """Periodically cluster all Qdrant vectors into archive-wide topics."""
    progress.update({"phase": "idle", "topics": 0, "articles": 0})

    while True:
        if qdrant is None:
            progress["phase"] = "no_qdrant"
            await asyncio.sleep(poll_interval)
            continue

        from .topics import should_recluster, run_clustering, get_all_vectors_from_qdrant

        with _DB_LOCK:
            needs = should_recluster(db)

        if not needs:
            progress["phase"] = "idle"
            await asyncio.sleep(poll_interval)
            continue

        progress["phase"] = "running"
        try:
            vectors = await asyncio.to_thread(get_all_vectors_from_qdrant, qdrant)
            if vectors:
                def _cluster_sync():
                    with _DB_LOCK:
                        return run_clustering(db, vectors, ollama_url)
                result = await asyncio.to_thread(_cluster_sync)
                progress.update({"topics": result.get("topics", 0), "articles": result.get("articles", 0)})
        except Exception as exc:
            logger.exception("topic clustering failed")
            progress["phase"] = "error"
            progress["last_error"] = str(exc)
        else:
            progress["phase"] = "idle"

        await asyncio.sleep(poll_interval)


async def run_profile_refresh_loop(
    db: sqlite3.Connection,
    progress: dict[str, Any],
    poll_interval: int = 3 * 3600,
) -> None:
    """Periodically rebuild materialised author_profiles."""
    progress.update({"phase": "idle", "profiles": 0})
    while True:
        progress["phase"] = "running"
        try:
            from ..enrich.author_profile import rebuild_all_profiles
            def _rebuild():
                with _DB_LOCK:
                    return rebuild_all_profiles(db)
            n = await asyncio.to_thread(_rebuild)
            progress["profiles"] = n
        except Exception as exc:
            logger.exception("author profile rebuild failed")
            progress["phase"] = "error"
            progress["last_error"] = str(exc)
        else:
            progress["phase"] = "idle"
        await asyncio.sleep(poll_interval)
