import pytest
from unittest.mock import patch

import pandas as pd

import app

@pytest.fixture(autouse=True)
def isolate_yahoo_refresh_state(monkeypatch):
    monkeypatch.setattr(app, "YAHOO_COOLDOWN_UNTIL", 0.0)
    monkeypatch.setattr(app, "YAHOO_HISTORY_CACHE", {})
    monkeypatch.setattr(app, "DIVIDEND_CONTEXT_CACHE", {})
    monkeypatch.setattr(app, "INCOME_HISTORY_CACHE", {})
    monkeypatch.setattr(app, "OPPORTUNITY_PAGE_CACHE", None)



def test_flat_close_dataframe_normalizes():
    index = pd.to_datetime(["2026-01-01", "2026-01-02"])
    history = pd.DataFrame({"Close": [10.25, 11.5]}, index=index)

    points = app.normalize_history_points(history, "AAPL")

    assert [point["price"] for point in points] == [10.25, 11.5]
    assert points[0]["date"].startswith("2026-01-01")


def test_flat_adjusted_close_dataframe_normalizes_without_close():
    index = pd.to_datetime(["2026-01-01", "2026-01-02"])
    history = pd.DataFrame({"Adj Close": [20.0, 21.75]}, index=index)

    points = app.normalize_history_points(history, "AAPL")

    assert [point["price"] for point in points] == [20.0, 21.75]


def test_multiindex_price_then_ticker_dataframe_normalizes_spcx():
    index = pd.to_datetime(["2026-01-01", "2026-01-02"])
    columns = pd.MultiIndex.from_tuples([
        ("Open", "SPCX"),
        ("Close", "SPCX"),
        ("Volume", "SPCX"),
    ])
    history = pd.DataFrame([[9.0, 10.0, 0], [10.0, 12.5, 0]], index=index, columns=columns)

    points = app.normalize_history_points(history, "SPCX")

    assert [point["price"] for point in points] == [10.0, 12.5]


def test_multiindex_ticker_then_price_dataframe_normalizes_spcx():
    index = pd.to_datetime(["2026-01-01", "2026-01-02"])
    columns = pd.MultiIndex.from_tuples([
        ("SPCX", "Open"),
        ("SPCX", "Close"),
    ])
    history = pd.DataFrame([[9.0, 10.0], [10.0, 12.5]], index=index, columns=columns)

    points = app.normalize_history_points(history, "SPCX")

    assert [point["price"] for point in points] == [10.0, 12.5]


def test_empty_dataframe_returns_unavailable_without_fake_points():
    with patch.object(app, "safe_history", return_value=pd.DataFrame()):
        result = app.stock_history("EMPTY", "1mo")

    assert result["ok"] is False
    assert result["labels"] == []
    assert result["prices"] == []


def test_invalid_symbol_returns_unavailable_without_fake_points():
    with patch.object(app, "safe_history", side_effect=RuntimeError("invalid symbol")):
        result = app.stock_history("INVALID", "1mo")

    assert result["ok"] is False
    assert result["labels"] == []
    assert result["prices"] == []


def test_spcx_backend_uses_real_mocked_rows():
    index = pd.to_datetime(["2026-01-01", "2026-01-02"])
    history = pd.DataFrame({"Close": [150.0, 160.5], "Volume": [0, 0]}, index=index)

    with patch.object(app, "safe_history", return_value=history):
        result = app.stock_history("SPCX", "1mo")

    assert result["ok"] is True
    assert result["prices"] == [150.0, 160.5]
    assert result["labels"] == ["2026-01-01 00:00", "2026-01-02 00:00"]


def test_company_name_aliases_are_canonicalized_for_chart_helpers():
    assert app.canonical_stock_symbol("palantir") == "PLTR"
    assert app.canonical_stock_symbol("Palantir Technologies") == "PLTR"
    assert app.canonical_stock_symbol("spacex") == "SPCX"
    assert app.canonical_stock_symbol("pltr") == "PLTR"


def test_palantir_is_in_default_universe():
    tickers = {item["ticker"] for item in app.expand_recommendations(app.DEFAULT_RECOMMENDATIONS)}
    assert "PLTR" in tickers


def test_stock_history_uses_canonical_symbol_for_company_name():
    index = pd.to_datetime(["2026-01-01", "2026-01-02"])
    history = pd.DataFrame({"Close": [22.0, 24.5]}, index=index)

    with patch.object(app, "safe_history", return_value=history) as mocked_history:
        alias_result = app.stock_history("palantir", "1mo")

    with patch.object(app, "safe_history", return_value=history):
        ticker_result = app.stock_history("PLTR", "1mo")

    mocked_history.assert_called_once_with(
        "PLTR",
        period="1mo",
        interval="1d",
        timeout=6,
    )
    assert alias_result == ticker_result


def test_history_cache_is_parameter_specific_bounded_and_returns_copies():
    from unittest.mock import Mock
    ticker = Mock()
    ticker.history.return_value = pd.DataFrame({"Close": [10.0]})
    with patch.object(app.yf, "Ticker", return_value=ticker), patch.object(app, "YAHOO_HISTORY_CACHE_LIMIT", 3):
        first = app.safe_history("cost", period="1mo", interval="1d", timeout=6)
        first.iloc[0, 0] = 999
        assert app.safe_history("COST", period="1mo", interval="1d", timeout=6).iloc[0, 0] == 10
        assert ticker.history.call_count == 1
        app.safe_history("COST", period="1y", interval="1d", timeout=6)
        app.safe_history("COST", period="1mo", interval="1wk", timeout=6)
        app.safe_history("COST", period="1mo", interval="1d", timeout=6, auto_adjust=True)
        assert ticker.history.call_count == 4
        assert len(app.YAHOO_HISTORY_CACHE) == 3


def test_history_cache_expires_and_rate_limit_blocks_other_symbols_and_fallback():
    from unittest.mock import Mock
    ticker = Mock()
    ticker.history.side_effect = [pd.DataFrame({"Close": [10.0]}), RuntimeError("429")]
    with patch.object(app.time, "time", return_value=1000) as clock, patch.object(app.yf, "Ticker", return_value=ticker), patch.object(app, "urlopen") as fallback:
        app.safe_history("COST", period="1mo")
        clock.return_value = 1299
        app.safe_history("COST", period="1mo")
        assert ticker.history.call_count == 1
        clock.return_value = 1301
        with pytest.raises(RuntimeError):
            app.safe_history("COST", period="1mo")
        with pytest.raises(RuntimeError, match="deferred"):
            app.safe_history("MSFT", period="max")
        with pytest.raises(RuntimeError, match="deferred"):
            app.fetch_yahoo_income_history("MSFT")
        assert ticker.history.call_count == 2
        fallback.assert_not_called()


@pytest.mark.parametrize("agent", ["Googlebot", "bingbot"])
def test_search_crawlers_keep_normal_refresh_access(agent):
    from unittest.mock import Mock
    ticker = Mock()
    ticker.history.return_value = pd.DataFrame({"Close": [10.0]})
    with app.app.test_request_context("/stock/COST", headers={"User-Agent": agent}), patch.object(app.yf, "Ticker", return_value=ticker):
        assert app.safe_history("COST", period="1mo").iloc[0, 0] == 10
        ticker.history.assert_called_once()


def test_semrush_can_read_cached_history_but_cannot_refresh_any_yahoo_entry_point():
    from unittest.mock import Mock
    ticker = Mock()
    ticker.history.return_value = pd.DataFrame({"Close": [10.0]})
    with patch.object(app.yf, "Ticker", return_value=ticker):
        app.safe_history("COST", period="1mo")
    with app.app.test_request_context("/stock/COST", headers={"User-Agent": "SemrushBot/7"}), patch.object(app.yf, "Ticker") as provider, patch.object(app, "urlopen") as fallback:
        assert app.safe_history("COST", period="1mo").iloc[0, 0] == 10
        assert app.get_dividend_context("COST")["return_outlook"]["metric"] == "Unavailable"
        with pytest.raises(RuntimeError):
            app.safe_history("COST", period="max")
        with pytest.raises(RuntimeError):
            app.fetch_yahoo_income_history("COST")
        provider.assert_not_called()
        fallback.assert_not_called()
