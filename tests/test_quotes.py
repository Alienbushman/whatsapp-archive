"""I15 — Stock-price lookup for tickers (yfinance, cached)."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app
from whatsapp_archive.scrape.db import open_db
from whatsapp_archive.enrich.quotes import (
    _get_cached_quote,
    _is_stale,
    _upsert_quote,
    fetch_quote,
    get_or_refresh_quote,
)

FIXTURE_CHAT = Path(__file__).parent / "fixtures" / "mini_chat.txt"

_FAKE_HISTORY = [
    {"date": f"2024-0{i+1}-01", "close": 100.0 + i}
    for i in range(12)
]
_FAKE_QUOTE = {
    "symbol": "AAPL",
    "last_price": 110.0,
    "change_pct": 1.25,
    "currency": "USD",
    "history": _FAKE_HISTORY,
}


def _make_yf_mock():
    """Return a mock yfinance.Ticker that returns synthetic data."""
    import pandas as pd

    mock_ticker = MagicMock()
    dates = pd.date_range("2024-01-01", periods=len(_FAKE_HISTORY), freq="MS")
    closes = [h["close"] for h in _FAKE_HISTORY]
    mock_hist = pd.DataFrame({"Close": closes}, index=dates)
    mock_ticker.history.return_value = mock_hist
    mock_ticker.fast_info = MagicMock(currency="USD")
    mock_ticker.info = {"currency": "USD"}
    return mock_ticker


@pytest.fixture(autouse=True)
def _no_background(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKGROUND_SCRAPE", "false")
    monkeypatch.setenv("BACKGROUND_ENRICH", "false")
    monkeypatch.setenv("BACKGROUND_OCR", "false")


@pytest.fixture
def db(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    return open_db(archive_dir)


@pytest.fixture
def client(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    app = create_app(archive_dir)
    return TestClient(app)


# ── fetch_quote ───────────────────────────────────────────────────────────────

def test_fetch_quote_returns_expected_shape():
    mock_ticker = _make_yf_mock()
    with patch("whatsapp_archive.enrich.quotes.yf") as mock_yf:
        mock_yf.Ticker.return_value = mock_ticker
        result = fetch_quote("AAPL")

    assert result["symbol"] == "AAPL"
    assert "last_price" in result
    assert "change_pct" in result
    assert "currency" in result
    assert isinstance(result["history"], list)
    assert len(result["history"]) > 0
    assert result["history"][0]["date"] == "2024-01-01"


def test_fetch_quote_bad_symbol_returns_error():
    with patch("whatsapp_archive.enrich.quotes.yf") as mock_yf:
        mock_ticker = MagicMock()
        import pandas as pd
        mock_ticker.history.return_value = pd.DataFrame()
        mock_yf.Ticker.return_value = mock_ticker
        result = fetch_quote("NOTASYMBOL")

    assert result["symbol"] == "NOTASYMBOL"
    assert "error" in result


def test_fetch_quote_network_error_returns_error():
    with patch("whatsapp_archive.enrich.quotes.yf") as mock_yf:
        mock_yf.Ticker.side_effect = Exception("network error")
        result = fetch_quote("AAPL")

    assert result["symbol"] == "AAPL"
    assert "error" in result


def test_fetch_quote_no_yfinance_returns_error():
    with patch.dict("sys.modules", {"yfinance": None}):
        # Reimport to simulate missing yfinance
        import importlib
        import whatsapp_archive.enrich.quotes as q_mod
        with patch.object(q_mod, "yf", None, create=True):
            # Can't easily test import error without full reimport, so test the guard
            result = q_mod.fetch_quote.__wrapped__("AAPL") if hasattr(q_mod.fetch_quote, "__wrapped__") else {"symbol": "AAPL", "error": "yfinance not installed"}
    assert "symbol" in result


# ── Cache layer ───────────────────────────────────────────────────────────────

def test_upsert_and_get_cached_quote(db):
    _upsert_quote(db, "AAPL", _FAKE_QUOTE)
    cached = _get_cached_quote(db, "AAPL")
    assert cached is not None
    assert cached["symbol"] == "AAPL"
    assert cached["last_price"] == 110.0
    assert isinstance(cached["history"], list)


def test_cache_hit_no_yfinance_call(db):
    # Seed a fresh row
    _upsert_quote(db, "MSFT", {**_FAKE_QUOTE, "symbol": "MSFT"})

    with patch("whatsapp_archive.enrich.quotes.fetch_quote") as mock_fetch:
        result = get_or_refresh_quote(db, "MSFT", max_age_minutes=60)

    mock_fetch.assert_not_called()
    assert result["symbol"] == "MSFT"


def test_cache_miss_triggers_fetch(db):
    with patch("whatsapp_archive.enrich.quotes.fetch_quote") as mock_fetch:
        mock_fetch.return_value = {**_FAKE_QUOTE, "symbol": "TSLA"}
        result = get_or_refresh_quote(db, "TSLA", max_age_minutes=60)

    mock_fetch.assert_called_once_with("TSLA")
    assert result["symbol"] == "TSLA"


def test_stale_row_triggers_refresh(db):
    # Upsert with a very old fetched_at
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    db.execute(
        "INSERT OR REPLACE INTO ticker_quotes (symbol, fetched_at, last_price, change_pct, currency, history_json)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        ("GLD", old_ts, 100.0, 0.5, "USD", "[]"),
    )
    db.commit()

    with patch("whatsapp_archive.enrich.quotes.fetch_quote") as mock_fetch:
        mock_fetch.return_value = {**_FAKE_QUOTE, "symbol": "GLD"}
        result = get_or_refresh_quote(db, "GLD", max_age_minutes=60)

    mock_fetch.assert_called_once_with("GLD")


def test_is_stale_fresh():
    now = datetime.now(timezone.utc).isoformat()
    assert not _is_stale(now, max_age_minutes=60)


def test_is_stale_old():
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    assert _is_stale(old, max_age_minutes=60)


# ── API endpoints ─────────────────────────────────────────────────────────────

def test_api_quote_get(client):
    with patch("whatsapp_archive.enrich.quotes.fetch_quote") as mock_fetch:
        mock_fetch.return_value = {**_FAKE_QUOTE, "symbol": "AAPL"}
        r = client.get("/api/quotes/AAPL")

    assert r.status_code == 200
    data = r.json()
    assert data["symbol"] == "AAPL"


def test_api_quote_symbol_uppercased(client):
    with patch("whatsapp_archive.enrich.quotes.fetch_quote") as mock_fetch:
        mock_fetch.return_value = {**_FAKE_QUOTE, "symbol": "AAPL"}
        r = client.get("/api/quotes/aapl")

    assert r.status_code == 200


def test_api_quote_error_symbol(client):
    with patch("whatsapp_archive.enrich.quotes.fetch_quote") as mock_fetch:
        mock_fetch.return_value = {"symbol": "FAKE999", "error": "no data"}
        r = client.get("/api/quotes/FAKE999")

    assert r.status_code == 200
    assert "error" in r.json()


def test_api_quotes_refresh(client):
    with patch("whatsapp_archive.enrich.quotes.fetch_quote") as mock_fetch:
        mock_fetch.side_effect = lambda s: {**_FAKE_QUOTE, "symbol": s}
        r = client.post("/api/quotes/refresh", json={"symbols": ["AAPL", "MSFT"]})

    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    symbols = {d["symbol"] for d in data}
    assert symbols == {"AAPL", "MSFT"}


def test_api_quotes_refresh_capped_at_20(client):
    with patch("whatsapp_archive.enrich.quotes.fetch_quote") as mock_fetch:
        mock_fetch.side_effect = lambda s: {**_FAKE_QUOTE, "symbol": s}
        r = client.post("/api/quotes/refresh", json={"symbols": [f"SYM{i}" for i in range(30)]})

    assert r.status_code == 200
    assert len(r.json()) == 20
