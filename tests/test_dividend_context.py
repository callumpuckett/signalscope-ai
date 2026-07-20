from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

import app


def clear_dividend_cache():
    app.DIVIDEND_CONTEXT_CACHE.clear()


def ticker_with_info(info):
    return SimpleNamespace(get_info=lambda: info)


def test_equity_dividend_context_formats_available_fields():
    clear_dividend_cache()
    info = {
        "quoteType": "EQUITY",
        "financialCurrency": "USD",
        "dividendYield": 0.034,
        "forwardAnnualDividendRate": 2.84,
        "exDividendDate": 1782432000,
        "payoutRatio": 0.62,
    }

    with patch.object(app.yf, "Ticker", return_value=ticker_with_info(info)):
        context = app.get_dividend_context("KO")

    assert context["is_etf"] is False
    assert context["dividend_label"] == "Dividend"
    assert context["income_status"] == app.INCOME_STATUS_AVAILABLE
    assert context["dividend_yield"] == "3.4%"
    assert context["annual_dividend"] == "$2.84 per share annually"
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
    assert context["income_status"] == app.INCOME_STATUS_AVAILABLE
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


@pytest.mark.parametrize(
    ("symbol", "info", "expected_is_etf", "expected_annual"),
    (
        (
            "PG",
            {
                "quoteType": "EQUITY",
                "financialCurrency": "USD",
                "dividendYield": 0.025,
                "forwardAnnualDividendRate": 4.2,
            },
            False,
            "$4.2 per share annually",
        ),
        (
            "SHEL.L",
            {
                "quoteType": "EQUITY",
                "financialCurrency": "GBP",
                "dividendYield": 0.041,
                "forwardAnnualDividendRate": 1.15,
            },
            False,
            "£1.15 per share annually",
        ),
        (
            "MAERSK-B.CO",
            {
                "quoteType": "EQUITY",
                "financialCurrency": "DKK",
                "dividendYield": 0.032,
                "forwardAnnualDividendRate": 1120,
            },
            False,
            "DKK 1120 per share annually",
        ),
        (
            "VWRL.L",
            {
                "quoteType": "ETF",
                "financialCurrency": "GBP",
                "trailingAnnualDividendYield": 0.018,
                "trailingAnnualDividendRate": 2.03,
            },
            True,
            "£2.03 per share annually",
        ),
    ),
)
def test_available_income_is_generic_across_markets_and_instrument_types(
    symbol,
    info,
    expected_is_etf,
    expected_annual,
):
    clear_dividend_cache()
    with patch.object(app.yf, "Ticker", return_value=ticker_with_info(info)):
        context = app.get_dividend_context(symbol)

    assert context["income_status"] == app.INCOME_STATUS_AVAILABLE
    assert context["has_dividend_data"] is True
    assert context["is_etf"] is expected_is_etf
    assert context["annual_dividend"] == expected_annual


@pytest.mark.parametrize(
    ("symbol", "info", "expected_message"),
    (
        (
            "TSLA",
            {"quoteType": "EQUITY", "currentPrice": 320.5},
            "No regular dividend found",
        ),
        (
            "VUSA.L",
            {"quoteType": "ETF", "regularMarketPrice": 105.2},
            "No regular cash distribution found",
        ),
    ),
)
def test_complete_provider_profile_can_confirm_income_is_absent(
    symbol,
    info,
    expected_message,
):
    clear_dividend_cache()
    with patch.object(app.yf, "Ticker", return_value=ticker_with_info(info)):
        context = app.get_dividend_context(symbol)

    assert context["income_status"] == app.INCOME_STATUS_ABSENT
    assert context["data_available"] is True
    assert context["has_dividend_data"] is False
    assert expected_message in context["no_data_message"]


def test_multiple_explicit_zero_fields_confirm_absence_without_treating_none_as_zero():
    clear_dividend_cache()
    info = {
        "quoteType": "EQUITY",
        "dividendYield": 0,
        "forwardAnnualDividendRate": 0,
        "payoutRatio": None,
    }
    with patch.object(app.yf, "Ticker", return_value=ticker_with_info(info)):
        context = app.get_dividend_context("TSLA")

    assert context["income_status"] == app.INCOME_STATUS_ABSENT
    assert "No regular dividend found" in context["no_data_message"]


@pytest.mark.parametrize("provider_info", ({}, {"quoteType": "EQUITY"}, None))
def test_empty_or_incomplete_provider_responses_are_unavailable(provider_info):
    clear_dividend_cache()
    with patch.object(app.yf, "Ticker", return_value=ticker_with_info(provider_info)):
        context = app.get_dividend_context("NVDA")

    assert context["income_status"] == app.INCOME_STATUS_UNAVAILABLE
    assert context["data_available"] is False
    assert context["no_data_message"] == "Dividend data is temporarily unavailable."
    assert "No regular dividend found" not in context["no_data_message"]


def test_cached_unavailable_context_is_retried_before_successful_data_expires():
    clear_dividend_cache()
    unavailable_context = {
        "income_status": app.INCOME_STATUS_UNAVAILABLE,
        "no_data_message": "Dividend data is temporarily unavailable.",
    }
    app.DIVIDEND_CONTEXT_CACHE["MSFT"] = {
        "timestamp": 1000,
        "context": unavailable_context,
    }
    info = {
        "quoteType": "EQUITY",
        "financialCurrency": "USD",
        "trailingAnnualDividendYield": 0.009,
        "trailingAnnualDividendRate": 3.56,
    }

    with (
        patch.object(
            app.time,
            "time",
            return_value=(
                1000 + app.DIVIDEND_CONTEXT_UNAVAILABLE_CACHE_TTL_SECONDS + 1
            ),
        ),
        patch.object(app.yf, "Ticker", return_value=ticker_with_info(info)) as ticker,
    ):
        context = app.get_dividend_context("MSFT")

    assert ticker.call_count == 1
    assert context["income_status"] == app.INCOME_STATUS_AVAILABLE
    assert context["dividend_yield"] == "0.9%"


@pytest.mark.parametrize(
    ("symbol", "universe_item", "expected_title", "expected_message"),
    (
        (
            "NVDA",
            {"ticker": "NVDA", "name": "NVIDIA Corporation", "sector": "Technology"},
            "Dividend snapshot",
            "Dividend data is temporarily unavailable.",
        ),
        (
            "VUSA.L",
            {"ticker": "VUSA.L", "name": "Vanguard S&P 500 UCITS ETF", "sector": "ETF"},
            "Distribution snapshot",
            "Distribution data is temporarily unavailable.",
        ),
    ),
)
def test_provider_failure_uses_generic_instrument_type_and_unavailable_message(
    symbol,
    universe_item,
    expected_title,
    expected_message,
):
    clear_dividend_cache()
    with (
        patch.object(app.yf, "Ticker", side_effect=TimeoutError("provider timed out")),
        patch.object(app, "get_stock_universe", return_value=[universe_item]),
    ):
        context = app.get_dividend_context(symbol)

    rendered = app.render_dividend_snapshot_html(context)
    summary = app.income_summary_text(context)

    assert context["income_status"] == app.INCOME_STATUS_UNAVAILABLE
    assert context["is_etf"] is (universe_item["sector"] == "ETF")
    assert expected_title in rendered
    assert expected_message in rendered
    assert summary == expected_message
    assert "No regular dividend found" not in rendered
    assert "No regular cash distribution found" not in rendered


def test_yfinance_failure_does_not_break_stock_page():
    clear_dividend_cache()
    with (
        patch.object(app.yf, "Ticker", side_effect=RuntimeError("unavailable")),
        patch.object(app, "safe_history", return_value=pd.DataFrame()),
    ):
        response = app.app.test_client().get("/stock/NVDA")

    assert response.status_code == 200
    assert b"Dividend snapshot" in response.data
    assert b"Dividend data is temporarily unavailable" in response.data
    assert b"No regular dividend found" not in response.data


def test_confirmed_non_dividend_company_keeps_truthful_absent_message_on_stock_page():
    clear_dividend_cache()
    info = {"quoteType": "EQUITY", "currentPrice": 320.5}
    with (
        patch.object(app.yf, "Ticker", return_value=ticker_with_info(info)),
        patch.object(app, "safe_history", return_value=pd.DataFrame()),
    ):
        response = app.app.test_client().get("/stock/TSLA")

    assert response.status_code == 200
    assert b"Dividend snapshot" in response.data
    assert b"No regular dividend found" in response.data
    assert b"Dividend data is temporarily unavailable" not in response.data


def test_yfinance_failure_does_not_remove_etf_distribution_snapshot():
    clear_dividend_cache()
    universe_item = {
        "ticker": "VUSA.L",
        "name": "Vanguard S&P 500 UCITS ETF",
        "sector": "ETF",
    }
    with (
        patch.object(app.yf, "Ticker", side_effect=RuntimeError("unavailable")),
        patch.object(app, "get_stock_universe", return_value=[universe_item]),
        patch.object(app, "safe_history", return_value=pd.DataFrame()),
    ):
        response = app.app.test_client().get("/stock/VUSA.L")

    assert response.status_code == 200
    assert b"Distribution snapshot" in response.data
    assert b"Distribution data is temporarily unavailable" in response.data
    assert b"No regular cash distribution found" not in response.data
