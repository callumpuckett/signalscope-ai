from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd

import app


FUTURE = datetime(2030, 10, 24, 12, 0, tzinfo=timezone.utc)
NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


def company_info(**overrides):
    info = {
        "quoteType": "EQUITY",
        "currency": "USD",
        "financialCurrency": "USD",
        "marketCap": 3_200_000_000_000,
        "trailingPE": 31.4,
        "trailingEps": 12.08,
        "beta": 0.92,
        "earningsTimestamp": FUTURE.timestamp(),
        "isEarningsDateEstimate": False,
    }
    info.update(overrides)
    return info


def dividend_context(symbol="MSFT", fundamentals=None, is_etf=False):
    label = "Distribution" if is_etf else "Dividend"
    return {
        "ticker": symbol,
        "is_etf": is_etf,
        "has_dividend_data": True,
        "dividend_label": label,
        "dividend_yield": "1.2%" if is_etf else "0.8%",
        "annual_dividend": "7.1 per share annually" if is_etf else "3.32 per share annually",
        "ex_dividend_date": "20 September 2026",
        "payout_ratio": "25%",
        "fundamentals": fundamentals or [],
        "beginner_explanation": "Existing income explanation.",
        "dividend_frequency_note": "Existing frequency note.",
        "risk_note": "Existing risk note.",
        "source_note": "Existing source note.",
    }


def ai_context(symbol="MSFT"):
    return {
        "ticker": symbol,
        "signal": "HOLD",
        "confidence": "65%",
        "confidence_meter": "██████░░░░",
        "strength_label": "Moderate",
        "reason": "Existing reason",
        "momentum_view": "Existing momentum view",
        "risk_view": "Existing risk view",
        "watch_next": "Existing watch trigger",
    }


def history_fixture():
    return pd.DataFrame(
        {"Close": [100.0, 102.0]},
        index=pd.to_datetime(["2026-01-01", "2026-01-02"]),
    )


def render_stock(symbol="MSFT", context=None, premium=False):
    client = app.app.test_client()
    if premium:
        with client.session_transaction() as current_session:
            current_session["owner_logged_in"] = True

    with (
        patch.object(app, "safe_history", return_value=history_fixture()),
        patch.object(app, "get_stock_ai_context", return_value=ai_context(symbol)),
        patch.object(app, "get_dividend_context", return_value=context),
        patch.object(app, "get_stock_universe", return_value=[]),
    ):
        return client.get(f"/stock/{symbol}")


def values_by_key(metrics):
    return {metric["key"]: metric["value"] for metric in metrics}


def test_company_provider_fields_map_to_the_five_approved_fundamentals():
    metrics = app.build_key_fundamentals(company_info(), now=NOW)

    assert values_by_key(metrics) == {
        "market-cap": "$3.2T",
        "pe-ratio": "31.4",
        "trailing-eps": "$12.08",
        "beta": "0.92",
        "next-earnings": "24 October 2030",
    }
    assert [metric["label"] for metric in metrics] == [
        "Market Cap",
        "P/E Ratio",
        "Trailing EPS",
        "Beta",
        "Next Earnings",
    ]


def test_market_cap_formats_millions_billions_and_trillions():
    assert app.format_fundamental_currency(740_000_000, "USD", compact=True) == "$740M"
    assert app.format_fundamental_currency(85_400_000_000, "USD", compact=True) == "$85.4B"
    assert app.format_fundamental_currency(3_200_000_000_000, "USD", compact=True) == "$3.2T"


def test_uk_currency_uses_financial_currency_for_market_cap_and_eps():
    metrics = app.build_key_fundamentals(
        company_info(
            currency="GBp",
            financialCurrency="GBP",
            marketCap=54_968_209_408,
            trailingEps=0.68,
            earningsTimestamp=None,
        ),
        now=NOW,
    )

    values = values_by_key(metrics)
    assert values["market-cap"] == "£55B"
    assert values["trailing-eps"] == "£0.68"


def test_negative_or_invalid_pe_and_past_or_invalid_earnings_are_omitted():
    past = datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp()
    metrics = app.build_key_fundamentals(
        company_info(trailingPE=-8.2, earningsTimestamp=past),
        now=NOW,
    )
    assert "pe-ratio" not in values_by_key(metrics)
    assert "next-earnings" not in values_by_key(metrics)

    invalid = app.build_key_fundamentals(
        company_info(trailingPE="Unknown", earningsTimestamp="N/A"),
        now=NOW,
    )
    assert "pe-ratio" not in values_by_key(invalid)
    assert "next-earnings" not in values_by_key(invalid)


def test_estimated_earnings_is_labelled_only_when_provider_marks_it():
    estimated = app.build_key_fundamentals(
        company_info(isEarningsDateEstimate=True),
        now=NOW,
    )
    assert values_by_key(estimated)["next-earnings"] == "24 October 2030 · Estimated"


def test_missing_values_and_etf_fields_create_no_fundamental_cards():
    missing = app.build_key_fundamentals(
        {
            "quoteType": "EQUITY",
            "marketCap": 0,
            "trailingPE": None,
            "trailingEps": "",
            "beta": "Unknown",
            "earningsTimestamp": 0,
        },
        now=NOW,
    )
    assert missing == []

    etf = app.build_key_fundamentals(company_info(quoteType="ETF"), is_etf=True, now=NOW)
    assert etf == []


def test_stock_page_renders_fundamentals_without_empty_cards_or_duplicate_yield():
    metrics = app.build_key_fundamentals(company_info(), now=NOW)
    response = render_stock(context=dividend_context(fundamentals=metrics))
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Key fundamentals" in page
    assert page.count('class="stock-fundamental-metric"') == 5
    for label in ("Market Cap", "P/E Ratio", "Trailing EPS", "Beta", "Next Earnings"):
        assert label in page
    assert page.count("Dividend yield") == 1
    assert page.index("Key fundamentals") < page.index("Premium locked preview")
    assert page.index("Premium locked preview") < page.index("Dividend snapshot")
    assert "N/A" not in page
    assert "Unknown" not in page


def test_entire_fundamentals_section_is_omitted_when_provider_values_are_absent():
    response = render_stock(context=dividend_context(fundamentals=[]))
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Key fundamentals" not in page
    assert 'class="stock-fundamental-metric"' not in page


def test_spy_keeps_one_distribution_yield_and_no_company_fundamentals():
    spy_info = company_info(
        quoteType="ETF",
        marketCap=None,
        trailingEps=None,
        beta=None,
        earningsTimestamp=None,
    )
    metrics = app.build_key_fundamentals(spy_info, is_etf=True, now=NOW)
    response = render_stock(
        symbol="SPY",
        context=dividend_context("SPY", fundamentals=metrics, is_etf=True),
    )
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert page.count("Distribution yield") == 1
    assert "Key fundamentals" not in page
    assert "Trailing EPS" not in page
    assert "Next Earnings" not in page
    assert "P/E Ratio" not in page


def test_existing_provider_request_is_reused_and_cached_for_fundamentals():
    app.DIVIDEND_CONTEXT_CACHE.clear()
    ticker = SimpleNamespace(get_info=Mock(return_value=company_info()))

    with (
        patch.object(app.yf, "Ticker", return_value=ticker) as ticker_factory,
        patch.object(app, "get_stock_universe", return_value=[]),
    ):
        first = app.get_dividend_context("MSFT")
        second = app.get_dividend_context("MSFT")

    ticker_factory.assert_called_once_with("MSFT")
    ticker.get_info.assert_called_once_with()
    assert first["fundamentals"] == second["fundamentals"]
    assert len(first["fundamentals"]) == 5


def test_free_and_premium_pages_share_basic_fundamentals_without_entitlement_changes():
    metrics = app.build_key_fundamentals(company_info(), now=NOW)
    context = dividend_context(fundamentals=metrics)
    free_page = render_stock(context=context).get_data(as_text=True)
    premium_page = render_stock(context=context, premium=True).get_data(as_text=True)

    assert "Key fundamentals" in free_page
    assert "Key fundamentals" in premium_page
    assert "Premium locked preview" in free_page
    assert "Today’s Context" not in free_page
    assert "Premium locked preview" not in premium_page
    assert "Today’s Context" in premium_page


def test_mobile_css_keeps_the_grid_compact_and_prevents_overflow():
    assert ".stock-fundamentals-grid{display:grid" in app.stock_detail_html
    assert "grid-template-columns:repeat(2,minmax(0,1fr))" in app.stock_detail_html
    assert ".stock-fundamental-metric{min-width:0" in app.stock_detail_html
    assert ".stock-fundamental-metric strong" in app.stock_detail_html
    assert "overflow-wrap:anywhere" in app.stock_detail_html
    assert "fetch(" not in app.stock_detail_html
