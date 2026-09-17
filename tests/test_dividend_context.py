from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import json

import pandas as pd
import pytest

import app

@pytest.fixture(autouse=True)
def isolate_yahoo_refresh_state(monkeypatch):
    monkeypatch.setattr(app, "YAHOO_COOLDOWN_UNTIL", 0.0)
    monkeypatch.setattr(app, "YAHOO_HISTORY_CACHE", {})
    monkeypatch.setattr(app, "DIVIDEND_CONTEXT_CACHE", {})
    monkeypatch.setattr(app, "INCOME_HISTORY_CACHE", {})
    monkeypatch.setattr(app, "OPPORTUNITY_PAGE_CACHE", None)



@pytest.fixture(autouse=True)
def disable_live_yahoo_chart_requests(monkeypatch):
    monkeypatch.setattr(
        app,
        "urlopen",
        MagicMock(side_effect=RuntimeError("chart fallback unavailable")),
    )


def clear_dividend_cache():
    app.DIVIDEND_CONTEXT_CACHE.clear()
    app.INCOME_HISTORY_CACHE.clear()


def ticker_with_info(info):
    return SimpleNamespace(get_info=lambda: info)


def ticker_with_info_and_history(info, history):
    return SimpleNamespace(get_info=lambda: info, history=lambda **kwargs: history)


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
    assert context["currency"] == "USD"
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
    info = {
        **info,
        "dividendYield": 0,
        "trailingAnnualDividendRate": 0,
    }
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


def test_existing_history_restores_distribution_data_when_metadata_is_incomplete():
    clear_dividend_cache()
    info = {
        "quoteType": "ETF",
        "currency": "GBP",
        "regularMarketPrice": 105.675,
    }
    history = pd.DataFrame(
        {"Dividends": [0.222219, 0.223298, 0.246723, 0.244174]},
        index=pd.to_datetime(["2025-09-18", "2025-12-18", "2026-03-19", "2026-06-18"]),
    )
    app.cache_income_history("VUSA.L", history)

    with patch.object(app.yf, "Ticker", return_value=ticker_with_info(info)):
        context = app.get_dividend_context("VUSA.L")

    assert context["income_status"] == app.INCOME_STATUS_AVAILABLE
    assert context["is_etf"] is True
    assert context["dividend_yield"] == "0.89%"
    assert context["annual_dividend"] == "£0.94 per share annually"


def test_existing_history_corrects_cross_currency_income_units_generically():
    clear_dividend_cache()
    info = {
        "quoteType": "EQUITY",
        "currency": "GBp",
        "financialCurrency": "USD",
        "regularMarketPrice": 3220,
        "trailingAnnualDividendRate": 1.479,
        "trailingAnnualDividendYield": 0.000457,
    }
    history = pd.DataFrame(
        {"Dividends": [26.62, 26.85, 27.87, 29.18]},
        index=pd.to_datetime(["2025-08-14", "2025-11-13", "2026-02-19", "2026-05-21"]),
    )
    app.cache_income_history("SHEL.L", history)

    with patch.object(app.yf, "Ticker", return_value=ticker_with_info(info)):
        context = app.get_dividend_context("SHEL.L")

    assert context["income_status"] == app.INCOME_STATUS_AVAILABLE
    assert context["dividend_yield"] == "3.43%"
    assert context["annual_dividend"] == "110.52p per share annually"


def test_profile_without_income_evidence_is_not_confirmed_absent_when_history_fails():
    clear_dividend_cache()
    ticker = ticker_with_info_and_history(
        {"quoteType": "EQUITY", "regularMarketPrice": 100},
        pd.DataFrame(),
    )
    with patch.object(app.yf, "Ticker", return_value=ticker):
        context = app.get_dividend_context("NVDA")

    assert context["income_status"] == app.INCOME_STATUS_UNAVAILABLE
    assert context["no_data_message"] == "Dividend data is temporarily unavailable."


def test_successful_history_with_explicit_zero_payments_confirms_absence():
    clear_dividend_cache()
    info = {"quoteType": "EQUITY", "regularMarketPrice": 100}
    history = pd.DataFrame(
        {"Close": [99, 100], "Dividends": [0, 0]},
        index=pd.to_datetime(["2026-07-17", "2026-07-18"]),
    )
    with patch.object(
        app.yf,
        "Ticker",
        return_value=ticker_with_info_and_history(info, history),
    ):
        context = app.get_dividend_context("NVDA")

    assert context["income_status"] == app.INCOME_STATUS_ABSENT
    assert context["no_data_message"].startswith("No regular dividend found")


def test_direct_yahoo_chart_fallback_restores_distribution_after_yfinance_failure():
    clear_dividend_cache()
    payload = {
        "chart": {
            "result": [{
                "meta": {
                    "currency": "GBP",
                    "regularMarketPrice": 105.675,
                    "instrumentType": "ETF",
                },
                "timestamp": [1784332800],
                "events": {
                    "dividends": {
                        "one": {"amount": 0.222219, "date": 1758178800},
                        "two": {"amount": 0.223298, "date": 1766016000},
                        "three": {"amount": 0.246723, "date": 1773878400},
                        "four": {"amount": 0.244174, "date": 1781740800},
                    }
                },
            }],
            "error": None,
        }
    }
    response = MagicMock()
    response.read.return_value = json.dumps(payload).encode("utf-8")
    response.__enter__.return_value = response

    with (
        patch.object(app.yf, "Ticker", side_effect=RuntimeError("metadata temporarily unavailable")),
        patch.object(app, "urlopen", return_value=response),
    ):
        context = app.get_dividend_context("VUSA.L")

    assert context["income_status"] == app.INCOME_STATUS_AVAILABLE
    assert context["is_etf"] is True
    assert context["dividend_yield"] == "0.89%"
    assert context["annual_dividend"] == "£0.94 per share annually"
    assert context["ex_dividend_date"] != "Not available"


def test_direct_yahoo_chart_fallback_can_confirm_no_dividend_without_metadata():
    clear_dividend_cache()
    payload = {
        "chart": {
            "result": [{
                "meta": {
                    "currency": "USD",
                    "regularMarketPrice": 320.5,
                    "instrumentType": "EQUITY",
                },
                "timestamp": [1784332800],
            }],
            "error": None,
        }
    }
    response = MagicMock()
    response.read.return_value = json.dumps(payload).encode("utf-8")
    response.__enter__.return_value = response

    with (
        patch.object(app.yf, "Ticker", side_effect=RuntimeError("metadata temporarily unavailable")),
        patch.object(app, "urlopen", return_value=response),
    ):
        context = app.get_dividend_context("TSLA")

    assert context["income_status"] == app.INCOME_STATUS_ABSENT
    assert context["no_data_message"].startswith("No regular dividend found")


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
    info = {
        "quoteType": "EQUITY",
        "currentPrice": 320.5,
        "dividendYield": 0,
        "trailingAnnualDividendRate": 0,
    }
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


def valid_outlook_metadata(now):
    return {
        "quoteType": "EQUITY", "currency": "USD", "financialCurrency": "USD",
        "regularMarketPrice": 892.47, "regularMarketTime": now,
        "targetMeanPrice": 1069.2, "targetHighPrice": 1315, "targetLowPrice": 740,
        "numberOfAnalystOpinions": 35, "dividendYield": .5,
        "forwardAnnualDividendRate": 4,
    }


@pytest.mark.parametrize("failure", [RuntimeError("429 Too Many Requests"), TimeoutError("timeout")])
def test_successful_outlook_survives_failed_refresh_with_original_timestamps(failure):
    now = 1_789_646_400
    ticker = MagicMock()
    ticker.get_info.side_effect = [valid_outlook_metadata(now), failure]
    ticker.history.side_effect = failure
    with (
        patch.object(app.time, "time", return_value=now) as clock,
        patch.object(app.yf, "Ticker", return_value=ticker),
        patch.object(app, "urlopen", side_effect=failure) as fallback,
    ):
        first = app.get_dividend_context("COST")["return_outlook"]
        clock.return_value = now + 3601
        second = app.get_dividend_context("COST")["return_outlook"]
        assert second["metric"] == first["metric"] == "+19.8%"
        assert second["quote_timestamp"] == second["retrieved_at"] == now
        assert second["cached"] is True
        assert app.DIVIDEND_CONTEXT_CACHE["COST"]["timestamp"] == now
        if "429" in str(failure):
            ticker.history.assert_not_called()
            fallback.assert_not_called()
            app.get_dividend_context("MSFT")
            assert ticker.get_info.call_count == 2
        clock.return_value = now + 7 * 86400 + 1
        assert app.get_dividend_context("COST")["return_outlook"]["metric"] == "Unavailable"


def test_cached_outlook_age_is_checked_even_before_context_ttl_expires():
    now = 1_789_646_400
    outlook = app.build_return_outlook(valid_outlook_metadata(now), now=now)
    app.DIVIDEND_CONTEXT_CACHE["COST"] = {
        "timestamp": now + 7 * 86400,
        "context": {"income_status": app.INCOME_STATUS_AVAILABLE, "return_outlook": outlook},
    }
    with patch.object(app.time, "time", return_value=now + 7 * 86400 + 1), patch.object(app.yf, "Ticker") as provider:
        assert app.get_dividend_context("COST")["return_outlook"]["metric"] == "Unavailable"
        provider.assert_not_called()


def test_explicit_rate_limit_cooldown_expires_and_allows_recovery():
    now = 1_789_646_400
    ticker = MagicMock()
    ticker.get_info.side_effect = [RuntimeError("429"), valid_outlook_metadata(now)]
    with patch.object(app.time, "time", return_value=now) as clock, patch.object(app.yf, "Ticker", return_value=ticker):
        assert app.get_dividend_context("COST")["return_outlook"]["metric"] == "Unavailable"
        clock.return_value = now + 299
        app.get_dividend_context("MSFT")
        assert ticker.get_info.call_count == 1
        clock.return_value = now + 301
        assert app.get_dividend_context("COST")["return_outlook"]["metric"] == "+19.8%"
        assert ticker.get_info.call_count == 2


def test_simultaneous_metadata_requests_share_one_refresh():
    from concurrent.futures import ThreadPoolExecutor
    import threading

    now = app.time.time()
    start = threading.Barrier(2)
    ticker = MagicMock()
    ticker.get_info.return_value = valid_outlook_metadata(now)
    def fetch():
        start.wait(timeout=3)
        return app.get_dividend_context("COST")["return_outlook"]["metric"]
    with patch.object(app.yf, "Ticker", return_value=ticker), ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: fetch(), range(2)))
    assert results == ["+19.8%", "+19.8%"]
    ticker.get_info.assert_called_once()


def test_daily_outlook_refresh_bypasses_yesterday_cache_ttl_but_reuses_today():
    yesterday = datetime(2026, 7, 20, 22, 59, tzinfo=timezone.utc).timestamp()
    today = yesterday+120  # London midnight passed; metadata TTL has not expired.
    ticker = MagicMock()
    ticker.get_info.side_effect = [valid_outlook_metadata(yesterday), valid_outlook_metadata(today)]
    with patch.object(app.time, "time", return_value=yesterday) as clock, patch.object(app.yf, "Ticker", return_value=ticker):
        first = app.get_dividend_context("COST")["return_outlook"]
        clock.return_value = today
        refreshed = app.opportunity_return_outlook("COST", refresh_date="2026-07-21")
        assert refreshed["retrieved_at"] == today
        assert first["retrieved_at"] == yesterday
        clock.return_value = today+4000  # Same-day success remains reusable after hourly TTL.
        reused = app.opportunity_return_outlook("COST", refresh_date="2026-07-21")
        assert reused["retrieved_at"] == today
        assert ticker.get_info.call_count == 2


def test_daily_refresh_failure_keeps_original_dates_and_honours_retry():
    yesterday = datetime(2026, 7, 20, 22, 59, tzinfo=timezone.utc).timestamp()
    today = yesterday+120
    ticker = MagicMock()
    ticker.get_info.side_effect = [valid_outlook_metadata(yesterday), RuntimeError("429"), valid_outlook_metadata(today+301)]
    with patch.object(app.time, "time", return_value=yesterday) as clock, patch.object(app.yf, "Ticker", return_value=ticker):
        app.get_dividend_context("COST")
        clock.return_value = today
        failed = app.opportunity_return_outlook("COST", refresh_date="2026-07-21")
        assert failed["retrieved_at"] == yesterday
        assert failed["cached"] is True
        clock.return_value = today+299
        app.opportunity_return_outlook("COST", refresh_date="2026-07-21")
        assert ticker.get_info.call_count == 2
        clock.return_value = today+301
        recovered = app.opportunity_return_outlook("COST", refresh_date="2026-07-21")
        assert recovered["retrieved_at"] == today+301
        assert ticker.get_info.call_count == 3


def test_intentional_chart_deferrals_are_not_logged_as_provider_failures():
    with app.app.test_request_context('/stock/COST', headers={"User-Agent": "SemrushBot"}), patch.object(app.yf, "Ticker") as provider, patch.object(app.app.logger, "warning") as warning, patch.object(app.app.logger, "info") as info:
        app.stock_history("COST", "1mo")
        app.stock_lifetime_growth("COST")
        provider.assert_not_called()
        warning.assert_not_called()
        assert info.call_count == 2
        assert "cache-only crawler" in str(info.call_args.args[-1])
