"""Stock-price lookup via yfinance with SQLite cache."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

try:
    import yfinance as yf
except ImportError:
    yf = None  # type: ignore[assignment]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_quote(symbol: str) -> dict:
    """Fetch last close + 1y daily history for a ticker symbol.

    Returns a dict with keys: symbol, last_price, change_pct, currency, history.
    On any failure returns {symbol, error}.
    """
    if yf is None:
        return {"symbol": symbol, "error": "yfinance not installed"}

    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="1y", interval="1d", auto_adjust=True)
        if hist.empty:
            return {"symbol": symbol, "error": "no data"}

        closes = hist["Close"]
        last_price = float(closes.iloc[-1])
        prev_price = float(closes.iloc[-2]) if len(closes) >= 2 else last_price
        change_pct = ((last_price - prev_price) / prev_price * 100) if prev_price else 0.0

        info = {}
        try:
            info = ticker.fast_info or {}
        except Exception:
            pass
        currency = getattr(info, "currency", None) or (ticker.info or {}).get("currency", "USD")

        history = [
            {"date": str(ts.date()), "close": round(float(v), 4)}
            for ts, v in closes.items()
        ]

        return {
            "symbol": symbol,
            "last_price": round(last_price, 4),
            "change_pct": round(change_pct, 4),
            "currency": currency,
            "history": history,
        }
    except Exception as exc:
        logger.warning("fetch_quote failed for %s: %s", symbol, exc)
        return {"symbol": symbol, "error": str(exc)}


def _upsert_quote(conn: sqlite3.Connection, symbol: str, data: dict) -> None:
    history_json = json.dumps(data.get("history") or [])
    conn.execute(
        "INSERT OR REPLACE INTO ticker_quotes (symbol, fetched_at, last_price, change_pct, currency, history_json)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            symbol,
            _now_iso(),
            data.get("last_price"),
            data.get("change_pct"),
            data.get("currency"),
            history_json,
        ),
    )
    conn.commit()


def _get_cached_quote(conn: sqlite3.Connection, symbol: str) -> dict | None:
    row = conn.execute("SELECT * FROM ticker_quotes WHERE symbol = ?", (symbol,)).fetchone()
    if not row:
        return None
    history = []
    try:
        history = json.loads(row["history_json"] or "[]")
    except (ValueError, TypeError):
        pass
    return {
        "symbol": row["symbol"],
        "last_price": row["last_price"],
        "change_pct": row["change_pct"],
        "currency": row["currency"],
        "history": history,
        "fetched_at": row["fetched_at"],
    }


def _is_stale(fetched_at: str, max_age_minutes: int) -> bool:
    try:
        ts = datetime.fromisoformat(fetched_at)
        age = (datetime.now(timezone.utc) - ts).total_seconds() / 60
        return age > max_age_minutes
    except (ValueError, TypeError):
        return True


def get_or_refresh_quote(
    conn: sqlite3.Connection,
    symbol: str,
    max_age_minutes: int = 60,
) -> dict:
    """Return a cached quote, refreshing if stale or missing."""
    cached = _get_cached_quote(conn, symbol)
    if cached and not _is_stale(cached["fetched_at"], max_age_minutes):
        return cached

    data = fetch_quote(symbol)
    if "error" not in data:
        _upsert_quote(conn, symbol, data)
    return data
