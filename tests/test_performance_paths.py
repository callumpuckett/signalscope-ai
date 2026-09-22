"""Regression coverage for optional dashboard snapshots and nonblocking Yahoo hits."""
import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from unittest.mock import Mock, patch

import pandas as pd
import pytest

import app


@pytest.fixture
def dashboard_sources(monkeypatch):
    rows = copy.deepcopy(app.DEFAULT_RECOMMENDATIONS)
    news = [
        {"label": "LIVE NEWS", "article_url": "https://example.com/news", "headline": "Market update", "stock_links": []},
        {"label": "LIVE NEWS", "article_url": "https://example.com/setup", "headline": "Add NEWSAPI_KEY"},
        {"label": "CONTEXT", "article_url": "https://example.com/context", "headline": "Context"},
    ]
    sources = {}
    for name, value in {
        "get_recommendations": rows,
        "get_market_impact_radar": [{"headline": "Impact context"}],
        "safe_build_live_headlines": news,
        "get_stock_universe": [],
    }.items():
        sources[name] = Mock(return_value=value)
        monkeypatch.setattr(app, name, sources[name])
    sources["snapshots"] = Mock(side_effect=lambda symbol, name, market: {"symbol": symbol, "name": name, "market": market})
    monkeypatch.setattr(app, "fetch_symbol_snapshot", sources["snapshots"])
    monkeypatch.setattr(app, "DASHBOARD_CACHE", {})
    return sources


@pytest.mark.parametrize("route", ["/", "/?tab=", "/api/market-news"])
@pytest.mark.parametrize("fallback", [False, True])
def test_public_routes_never_fetch_unused_snapshots(dashboard_sources, route, fallback):
    with patch.object(app, "render_template_string", return_value="homepage"):
        if fallback:
            with patch.object(app, "get_cached_dashboard_data", return_value={}):
                response = app.app.test_client().get(route)
        else:
            response = app.app.test_client().get(route)
    assert response.status_code == 200
    dashboard_sources["snapshots"].assert_not_called()
    dashboard_sources["safe_build_live_headlines"].assert_called_once()
    if route == "/api/market-news":
        assert response.json["live_news_active"] is True
        assert len(response.json["items"]) == 1
        assert response.json["items"][0]["headline"] == "Market update"


@pytest.mark.parametrize("tab", ["overview", "signals", "radar", "watchlist"])
def test_dashboard_surfaces_receive_all_six_snapshots(dashboard_sources, tab):
    with patch.object(app, "render_template_string", return_value="dashboard") as render:
        assert app.app.test_client().get("/?tab=" + tab).status_code == 200
    assert [row["symbol"] for row in render.call_args.kwargs["market_snapshot"]] == [
        "^GSPC", "^IXIC", "SPY", "QQQ", "^FTSE", "BP.L",
    ]
    assert dashboard_sources["snapshots"].call_count == 6


def test_partial_cache_upgrade_preserves_outputs_and_original_expiry(dashboard_sources):
    with patch.object(app.time, "time", return_value=1000) as clock:
        partial = app.get_cached_dashboard_data(include_market_snapshots=False)
        assert partial["market_snapshot"] is None
        clock.return_value = 1100
        full = app.get_cached_dashboard_data()
        assert len(full["market_snapshot"]) == 6
        assert {k: v for k, v in full.items() if k != "market_snapshot"} == {
            k: v for k, v in partial.items() if k != "market_snapshot"
        }
        assert app.DASHBOARD_CACHE["timestamp"] == 1000
        assert app.get_cached_dashboard_data(include_market_snapshots=False) == full
        assert app.get_cached_dashboard_data() == full
        dashboard_sources["get_recommendations"].assert_called_once()
        dashboard_sources["safe_build_live_headlines"].assert_called_once()
        assert dashboard_sources["snapshots"].call_count == 6
        clock.return_value = 1000 + app.DASHBOARD_CACHE_TTL_SECONDS
        app.get_cached_dashboard_data(include_market_snapshots=False)
        assert dashboard_sources["get_recommendations"].call_count == 2
        assert app.DASHBOARD_CACHE["data"]["market_snapshot"] is None
        app.get_cached_dashboard_data()
        assert dashboard_sources["snapshots"].call_count == 12


def test_full_cache_reused_by_public_routes_and_force_refresh_resets_completeness(dashboard_sources):
    full = app.get_cached_dashboard_data()
    with patch.object(app, "render_template_string", return_value="homepage"):
        assert app.app.test_client().get("/").status_code == 200
        response = app.app.test_client().get("/api/market-news")
    assert response.json["items"] == app.serialize_market_news_items(full["live_headlines"])
    dashboard_sources["get_recommendations"].assert_called_once()
    partial = app.get_cached_dashboard_data(force_refresh=True, include_market_snapshots=False)
    assert partial["market_snapshot"] is None
    assert dashboard_sources["snapshots"].call_count == 6
    app.get_cached_dashboard_data()
    assert dashboard_sources["snapshots"].call_count == 12
    assert dashboard_sources["get_recommendations"].call_count == 2


def test_snapshot_omission_does_not_change_calculations_or_news(dashboard_sources):
    with patch.object(app, "datetime", wraps=datetime) as clock:
        clock.now.return_value = datetime(2026, 9, 22, 12)
        full = app.prepare_dashboard_data()
        partial = app.prepare_dashboard_data(include_market_snapshots=False)
    full.pop("market_snapshot")
    partial.pop("market_snapshot")
    assert full == partial
    assert len(full["live_headlines"]) == 1
    rows = dashboard_sources["get_recommendations"].return_value
    assert (full["buy_rows"], full["hold_rows"], full["sell_rows"], full["conviction_rows"]) == app.split_rows(rows)
    assert (full["buy_count"], full["hold_count"], full["sell_count"], full["high_conviction_count"]) == app.calculate_counts(rows)
    assert full["premium_decision_brief"] == app.build_premium_decision_brief(rows)


@pytest.fixture
def yahoo_sources(monkeypatch):
    monkeypatch.setattr(app, "YAHOO_HISTORY_CACHE", {})
    monkeypatch.setattr(app, "DIVIDEND_CONTEXT_CACHE", {})
    monkeypatch.setattr(app, "YAHOO_COOLDOWN_UNTIL", 0)
    now = app.time.time()
    history = pd.DataFrame({"Close": [100.]}, index=pd.to_datetime(["2026-09-21"]))
    context = {"income_status": app.INCOME_STATUS_AVAILABLE, "return_outlook": app.build_return_outlook({
        "regularMarketPrice": 100, "targetMeanPrice": 120, "targetHighPrice": 140,
        "targetLowPrice": 90, "numberOfAnalystOpinions": 20, "currency": "USD",
        "quoteType": "EQUITY", "regularMarketTime": now,
    }, now=now)}
    ticker = Mock()
    ticker.history.return_value = history
    monkeypatch.setattr(app.yf, "Ticker", Mock(return_value=ticker))
    metadata = Mock(side_effect=lambda _: copy.deepcopy(context))
    monkeypatch.setattr(app, "_fetch_dividend_context", metadata)
    return ticker, metadata, history, context


@pytest.mark.parametrize("reader", ["history", "metadata", "daily_metadata"])
@pytest.mark.parametrize("refresh", ["history", "metadata"])
def test_cached_ticker_returns_before_unrelated_refresh_finishes(yahoo_sources, reader, refresh):
    ticker, metadata, history, context = yahoo_sources
    def read():
        if reader == "history":
            return app.safe_history("COST", period="1mo")
        return app.get_dividend_context("COST", outlook_refresh_date=(
            app.outlook_retrieval_date(context["return_outlook"]) if reader == "daily_metadata" else None
        ))
    first = read()
    entered, release = threading.Event(), threading.Event()
    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return history if refresh == "history" else copy.deepcopy(context)
    if refresh == "history":
        ticker.history.side_effect = slow
        refresh_call = lambda: app.safe_history("MSFT", period="1mo")
    else:
        metadata.side_effect = slow
        refresh_call = lambda: app.get_dividend_context("MSFT")
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(refresh_call)
        try:
            assert entered.wait(2)
            result = pool.submit(read).result(timeout=1)
            assert not pending.done()
            if reader == "history":
                pd.testing.assert_frame_equal(first, result)
                result.iloc[0, 0] = 999
                assert read().iloc[0, 0] == 100
            else:
                assert result["return_outlook"]["quote_timestamp"] == first["return_outlook"]["quote_timestamp"]
                assert result["return_outlook"]["retrieved_at"] == first["return_outlook"]["retrieved_at"]
                assert result["return_outlook"]["cases"] == first["return_outlook"]["cases"]
                result["income_status"] = "changed"
                assert read()["income_status"] == app.INCOME_STATUS_AVAILABLE
        finally:
            release.set()
        pending.result(timeout=2)


@pytest.mark.parametrize("kind", ["history", "metadata"])
def test_simultaneous_cache_misses_recheck_and_share_refresh(yahoo_sources, kind):
    ticker, metadata, history, context = yahoo_sources
    entered, release, second_checked = threading.Event(), threading.Event(), threading.Event()
    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return history if kind == "history" else copy.deepcopy(context)
    provider = ticker.history if kind == "history" else metadata
    provider.side_effect = slow
    read = (lambda: app.safe_history("COST", period="1mo")) if kind == "history" else (lambda: app.get_dividend_context("COST"))
    helper_name = "_cached_yahoo_history" if kind == "history" else "_cached_dividend_context"
    original = getattr(app, helper_name)
    def checked(*args):
        result = original(*args)
        if entered.is_set():
            second_checked.set()
        return result
    with patch.object(app, helper_name, side_effect=checked), ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(read)
        try:
            assert entered.wait(2)
            second = pool.submit(read)
            assert second_checked.wait(1)
            assert not second.done()
            assert provider.call_count == 1
        finally:
            release.set()
        first.result(timeout=2)
        second.result(timeout=2)
    assert provider.call_count == 1


def test_header_logo_preserves_static_cache_and_conditional_requests():
    client = app.app.test_client()
    response = client.get("/static/stockradar-header-logo.png")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "public, max-age=3600"
    assert "Pragma" not in response.headers
    revalidated = client.get("/static/stockradar-header-logo.png", headers={"If-None-Match": response.headers["ETag"]})
    assert revalidated.status_code == 304
    assert revalidated.headers["Cache-Control"] == response.headers["Cache-Control"]
