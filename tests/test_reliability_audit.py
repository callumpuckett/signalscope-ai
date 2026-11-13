import json
import threading
import time
from unittest.mock import Mock, patch

import pandas as pd
import pytest

import app
import history_cache
import yahoo_chart
from datetime import datetime
from zoneinfo import ZoneInfo
from market_calendar import scheduled_session_open


@pytest.fixture
def direct(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "YAHOO_HISTORY_TRANSPORT", "chart")
    monkeypatch.setattr(app, "YAHOO_HISTORY_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(app, "YAHOO_HISTORY_CACHE", {})
    monkeypatch.setattr(app, "YAHOO_CHART_COOLDOWN_UNTIL", 0)
    monkeypatch.setattr(app, "YAHOO_COOLDOWN_UNTIL", 0)


def frame(values=(10, 12)):
    return pd.DataFrame({"Close": values}, index=pd.date_range("2026-01-01", periods=len(values), tz="UTC"))


def test_metadata_throttle_does_not_disable_chart_endpoint(direct, monkeypatch):
    monkeypatch.setattr(app, "YAHOO_COOLDOWN_UNTIL", app.time.time() + 300)
    with patch.object(app, "fetch_chart_history", return_value=frame()) as fetch:
        assert app.stock_history("MSFT", "1mo")["ok"]
        assert fetch.call_count == 1


def test_chart_429_blocks_requests_until_cooldown_then_recovers(direct):
    with patch.object(app.time, "time", return_value=1000) as clock, patch.object(
        app, "fetch_chart_history", side_effect=[RuntimeError("429"), frame()]
    ) as fetch:
        assert not app.stock_history("MSFT", "1mo")["ok"]
        assert not app.stock_history("AAPL", "1mo")["ok"]
        assert fetch.call_count == 1
        clock.return_value = 1301
        assert app.stock_history("MSFT", "1mo")["ok"]
        assert fetch.call_count == 2


def test_history_survives_process_cache_loss_and_failed_refresh(direct):
    with patch.object(app.time, "time", return_value=1000) as clock, patch.object(
        app, "fetch_chart_history", return_value=frame()
    ) as fetch:
        app.safe_history("MSFT", period="1mo", interval="1d", timeout=6)
        app.YAHOO_HISTORY_CACHE.clear()
        assert app.safe_history("MSFT", period="1mo", interval="1d", timeout=6).iloc[-1, 0] == 12
        assert fetch.call_count == 1
        clock.return_value = 1301
        fetch.side_effect = TimeoutError("private provider diagnostics")
        result = app.stock_history("MSFT", "1mo")
        assert result["ok"]
        assert "Stale cached history" in result["freshness"]
        assert next(iter(app.YAHOO_HISTORY_CACHE.values()))["timestamp"] == 1000


def test_hour_range_is_latest_hour_and_preserves_subcent_precision():
    history = pd.DataFrame({"Close": [0.001234 + n / 100000 for n in range(30)]},
                           index=pd.date_range("2026-11-13 14:00", periods=30, freq="5min", tz="UTC"))
    with patch.object(app, "safe_history", return_value=history):
        result = app.stock_history("MSFT", "1h")
    assert len(result["points"]) == 13
    assert result["points"][-1]["timestamp_ms"] - result["points"][0]["timestamp_ms"] == 3600000
    assert result["prices"][0] > 0


def test_different_ticker_column_never_used_as_requested_price():
    history = frame()
    history.columns = pd.MultiIndex.from_tuples([("Close", "AAPL")])
    assert app.extract_history_price_series(history, "MSFT").empty


def test_lifetime_uses_adjustment_and_rejects_invalid_baseline():
    with patch.object(app, "safe_history", return_value=frame((0, -1, 10, 12))) as history:
        result = app.stock_lifetime_growth("MSFT")
    assert history.call_args.kwargs["auto_adjust"] is True
    assert result["change_percent"] == "+20.00%"
    with patch.object(app, "safe_history", return_value=frame((0.00001, 12))):
        assert app.stock_lifetime_growth("MSFT")["change_percent"] == "—"


def test_chart_errors_do_not_expose_provider_details():
    with patch.object(app, "safe_history", side_effect=RuntimeError("credential=private-secret")):
        result = app.stock_history("MSFT", "1mo")
    assert "private-secret" not in result["error"]


def test_template_cache_preserves_request_context_and_escaping():
    app._compiled_inline_template.cache_clear()
    with app.app.test_request_context("/"):
        app.session["audit"] = "first"
        assert app.render_template_string("{{ session.audit }} {{ value }}", value="<script>") == "first &lt;script&gt;"
    with app.app.test_request_context("/"):
        app.session["audit"] = "second"
        assert app.render_template_string("{{ session.audit }} {{ value }}", value="safe") == "second safe"
    assert app._compiled_inline_template.cache_info().hits == 1


@pytest.mark.parametrize("payload", [
    {}, {"chart": {"error": {"code": "Not Found"}}},
    {"chart": {"result": [{"meta": {"symbol": "AAPL"}}]}},
])
def test_direct_chart_rejects_malformed_or_wrong_symbol(payload):
    response = Mock()
    response.read.return_value = json.dumps(payload).encode()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    with patch.object(yahoo_chart, "urlopen", return_value=response), pytest.raises(ValueError):
        yahoo_chart.fetch_chart_history("MSFT")


def test_direct_chart_parses_adjusted_values_and_drops_invalid_observations():
    payload = {"chart": {"result": [{"meta": {"symbol": "MSFT", "currency": "USD"},
               "timestamp": [1700000000, 1700086400, 1700172800],
               "indicators": {"quote": [{"close": [100, 0, 120]}],
                              "adjclose": [{"adjclose": [50, 0, 60]}]}}]}}
    response = Mock()
    response.read.return_value = json.dumps(payload).encode()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    with patch.object(yahoo_chart, "urlopen", return_value=response):
        history = yahoo_chart.fetch_chart_history("MSFT", auto_adjust=True)
    assert list(history["Close"]) == [50, 60]
    assert history.attrs["currency"] == "USD"


def test_corrupt_persistent_history_is_ignored(tmp_path):
    history_cache.cache_path(tmp_path, "key").write_text("not json")
    assert history_cache.load_history(tmp_path, "key", 1000) is None


def test_concurrent_history_calls_fetch_once(direct):
    with patch.object(app, "fetch_chart_history", return_value=frame()) as fetch:
        results = []
        threads = [threading.Thread(target=lambda: results.append(app.safe_history("MSFT", period="1mo"))) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(3)
        assert len(results) == 8
        assert fetch.call_count == 1


@pytest.mark.parametrize(("stamp", "market", "expected"), [
    ("2026-11-26T11:00", "US", False),
    ("2026-11-27T12:59", "US", True),
    ("2026-11-27T13:00", "US", False),
    ("2026-12-24T12:30", "UK", False),
    ("2026-12-31T12:29", "UK", True),
    ("2026-12-31T12:30", "UK", False),
    ("2027-03-26T11:00", "UK", False),
    ("2027-05-03T11:00", "UK", False),
    ("2028-01-03T11:00", "UK", False),
    ("2028-07-03T13:00", "US", False),
    ("2028-12-22T12:30", "UK", False),
])
def test_published_holidays_and_early_closes(stamp, market, expected):
    zone = ZoneInfo("America/New_York" if market == "US" else "Europe/London")
    assert scheduled_session_open(datetime.fromisoformat(stamp).replace(tzinfo=zone), market) is expected


def test_short_price_history_cannot_overwrite_annual_income_cache(monkeypatch):
    monkeypatch.setattr(app, "INCOME_HISTORY_CACHE", {})
    annual = pd.DataFrame({"Close": [100, 110], "Dividends": [2, 2]},
                          index=pd.to_datetime(["2025-11-13", "2026-11-13"]))
    short = frame()
    short["Dividends"] = 0
    app.cache_income_history("MSFT", annual)
    app.cache_income_history("MSFT", short)
    assert app.cached_income_history("MSFT")["Dividends"].sum() == 4
    assert app.enrich_income_info_from_history({}, short) == ({}, False)


def test_homepage_cannot_describe_default_signal_as_current():
    preview = app.build_homepage_free_report_preview([{"ticker": "MSFT", "signal": "BUY", "confidence": "82%", "signal_source": "fallback"}])
    assert "not calculated from current market evidence" in preview["explanation"]
    assert "latest free research prompt" not in preview["explanation"]


def test_failed_metadata_preserves_validated_fundamentals(monkeypatch):
    context = {"provider_response_received": True, "fundamentals": [{"value": "$1T"}],
               "income_status": app.INCOME_STATUS_AVAILABLE, "return_outlook": app.build_return_outlook({})}
    monkeypatch.setattr(app, "DIVIDEND_CONTEXT_CACHE", {"MSFT": {"timestamp": 1000, "context": context}})
    monkeypatch.setattr(app, "YAHOO_COOLDOWN_UNTIL", 0)
    with patch.object(app.time, "time", return_value=10000), patch.object(
        app, "_fetch_dividend_context", return_value={"provider_response_received": False}
    ):
        result = app.get_dividend_context("MSFT")
    assert result["fundamentals"] == context["fundamentals"]
    assert result["stale"]
    assert app.DIVIDEND_CONTEXT_CACHE["MSFT"]["timestamp"] == 1000


def test_slow_other_history_request_cannot_block_uncached_chart(direct):
    entered, release = threading.Event(), threading.Event()
    def hold():
        with app.YAHOO_HISTORY_REFRESH_LOCK:
            entered.set()
            release.wait(2)
    thread = threading.Thread(target=hold)
    thread.start()
    assert entered.wait(1)
    try:
        start = time.perf_counter()
        result = app.stock_history("MSFT", "1mo")
        assert time.perf_counter() - start < 0.5
        assert not result["ok"] and "being refreshed" in result["error"]
    finally:
        release.set()
        thread.join(2)


def test_slow_metadata_refresh_is_background_and_deduplicated(monkeypatch):
    monkeypatch.setattr(app, "YAHOO_METADATA_ASYNC_ENABLED", True)
    monkeypatch.setattr(app, "DIVIDEND_CONTEXT_CACHE", {})
    monkeypatch.setattr(app, "YAHOO_METADATA_PENDING", set())
    monkeypatch.setattr(app, "YAHOO_COOLDOWN_UNTIL", 0)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    calls = []
    real_fetch = app._fetch_dividend_context
    def fetch(symbol, *, allow_network=True):
        if not allow_network:
            return real_fetch(symbol, allow_network=False)
        calls.append(symbol)
        entered.set()
        assert release.wait(2)
        finished.set()
        return {"provider_response_received": True, "return_outlook": app.build_return_outlook({})}
    monkeypatch.setattr(app, "_fetch_dividend_context", fetch)
    try:
        with app.app.test_request_context("/stock/MSFT"):
            start = time.perf_counter()
            result = app.get_dividend_context("MSFT")
            assert time.perf_counter() - start < .5
            assert entered.wait(1)
            app.get_dividend_context("MSFT")
            assert calls == ["MSFT"]
            assert "refresh pending" in result["source_note"]
    finally:
        release.set()
        assert finished.wait(2)
        deadline = time.monotonic() + 2
        while app.YAHOO_METADATA_PENDING and time.monotonic() < deadline:
            time.sleep(.01)
        assert not app.YAHOO_METADATA_PENDING
