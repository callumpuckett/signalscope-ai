from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

import app


def clear_dividend_cache():
    app.DIVIDEND_CONTEXT_CACHE.clear()


def ticker_with_info(info):
    return SimpleNamespace(get_info=lambda: info)


def test_equity_dividend_context_formats_available_fields():
    clear_dividend_cache()
    info = {
        "quoteType": "EQUITY",
        "dividendYield": 0.034,
        "forwardAnnualDividendRate": 2.84,
        "exDividendDate": 1782432000,
        "payoutRatio": 0.62,
    }

    with patch.object(app.yf, "Ticker", return_value=ticker_with_info(info)):
        context = app.get_dividend_context("KO")

    assert context["is_etf"] is False
    assert context["dividend_label"] == "Dividend"
    assert context["dividend_yield"] == "3.4%"
    assert context["annual_dividend"] == "2.84 per share annually"
    assert context["payout_ratio"] == "62%"
    assert context["has_dividend_data"] is True


def test_etf_uses_distribution_wording():
    clear_dividend_cache()
    info = {
        "quoteType": "ETF",
        "category": "Large Blend",
        "trailingAnnualDividendYield": 0.0125,
        "trailingAnnualDividendRate": 7.1,
    }

    with patch.object(app.yf, "Ticker", return_value=ticker_with_info(info)):
        context = app.get_dividend_context("SPY")

    assert context["is_etf"] is True
    assert context["dividend_label"] == "Distribution"
    assert context["dividend_yield"] == "1.25%"
    assert "ETF distributions are payments" in context["beginner_explanation"]


def test_current_yfinance_percentage_field_is_not_multiplied_to_98_percent():
    clear_dividend_cache()
    info = {
        "quoteType": "ETF",
        "dividendYield": 0.98,
        "trailingAnnualDividendYield": 0.0075822915,
        "trailingAnnualDividendRate": 5.662,
        "regularMarketPrice": 745.135,
    }

    with patch.object(app.yf, "Ticker", return_value=ticker_with_info(info)):
        context = app.get_dividend_context("SPY")

    assert context["dividend_yield"] == "0.76%"


def test_missing_data_is_safe_for_equity_and_etf():
    for symbol, expected_text in (
        ("TSLA", "No regular dividend found"),
        ("QQQ", "No regular cash distribution found"),
    ):
        clear_dividend_cache()
        with patch.object(
            app.yf,
            "Ticker",
            return_value=ticker_with_info({"quoteType": "ETF" if symbol == "QQQ" else "EQUITY"}),
        ):
            context = app.get_dividend_context(symbol)

        assert context["has_dividend_data"] is False
        assert expected_text in context["no_data_message"]


def test_yfinance_failure_does_not_break_stock_page():
    clear_dividend_cache()
    with (
        patch.object(app.yf, "Ticker", side_effect=RuntimeError("unavailable")),
        patch.object(app, "safe_history", return_value=pd.DataFrame()),
    ):
        response = app.app.test_client().get("/stock/NVDA")

    assert response.status_code == 200
    assert b"Dividend snapshot" in response.data
    assert b"No regular dividend found" in response.data
