import inspect
from unittest.mock import patch

import pandas as pd

import app


def stock_history_fixture():
    return pd.DataFrame(
        {"Close": [100.0, 102.0]},
        index=pd.to_datetime(["2026-01-01", "2026-01-02"]),
    )


def supplied_context(signal="BUY", confidence="82%"):
    return {
        "ticker": "MSFT",
        "signal": signal,
        "confidence": confidence,
        "confidence_meter": "████████░░",
        "strength_label": "Strong",
        "reason": "UNIQUE_PHASE18_SETUP_REASON",
        "momentum_view": "UNIQUE_PHASE18_MOMENTUM_VIEW",
        "risk_view": "UNIQUE_PHASE18_FULL_RISK_VIEW",
        "watch_next": "UNIQUE_PHASE18_FULL_WATCH_NEXT",
    }


def render_stock_page(symbol="MSFT", premium=False, context=None):
    client = app.app.test_client()
    if premium:
        with client.session_transaction() as current_session:
            current_session["owner_logged_in"] = True

    with (
        patch.object(app, "safe_history", return_value=stock_history_fixture()),
        patch.object(app, "get_dividend_context", return_value=None),
        patch.object(app, "get_stock_ai_context", return_value=context or supplied_context()),
    ):
        return client.get(f"/stock/{symbol}")


def test_today_context_reuses_supplied_values_without_a_second_score():
    source = supplied_context()
    result = app.build_today_context(source)

    assert result["signal"] == source["signal"]
    assert result["confidence"] == source["confidence"]
    assert result["strength_label"] == source["strength_label"]
    assert result["confidence_label"] == "82% · Strong"
    assert result["setup_reason"] == source["reason"]
    assert result["risk_today"] == source["risk_view"]
    assert result["watch_next"] == source["watch_next"]
    assert "score" not in result


def test_today_context_has_deterministic_wording_for_all_signals():
    expected_wording = {
        "BUY": "constructive",
        "HOLD": "balanced",
        "SELL": "cautious",
        "WATCH": "watchlist research candidate",
    }

    for signal, phrase in expected_wording.items():
        result = app.build_today_context(supplied_context(signal=signal))
        assert phrase in result["plain_english_summary"]


def test_today_context_changes_when_current_context_changes():
    first = app.build_today_context(supplied_context("BUY", "82%"))
    changed_source = supplied_context("SELL", "44%")
    changed_source.update({
        "strength_label": "Early",
        "reason": "Changed reason",
        "risk_view": "Changed risk",
        "watch_next": "Changed watch trigger",
    })
    second = app.build_today_context(changed_source)

    assert first != second
    assert second["signal"] == "SELL"
    assert second["confidence"] == "44%"
    assert second["setup_reason"] == "Changed reason"
    assert second["risk_today"] == "Changed risk"
    assert second["watch_next"] == "Changed watch trigger"


def test_business_education_is_stable_and_has_no_signal_inputs():
    parameters = inspect.signature(app.build_business_education).parameters
    assert "signal" not in parameters
    assert "confidence" not in parameters

    role = app.classify_portfolio_role("MSFT")
    buy_role = {**role, "signal": "BUY", "confidence": "90%"}
    sell_role = {**role, "signal": "SELL", "confidence": "20%"}

    assert app.build_business_education("MSFT", "Technology", "Microsoft", buy_role) == app.build_business_education(
        "MSFT", "Technology", "Microsoft", sell_role
    )


def test_business_education_covers_phase18_examples():
    examples = {
        "MSFT": ("Technology", "Microsoft", "Technology and software", "software"),
        "KO": ("Consumer Defensive", "Coca-Cola", "Consumer staples", "frequently purchased products"),
        "SPY": ("ETF", "SPDR S&P 500 ETF Trust", "Broad-market ETF", "broad market index"),
        "JPM": ("Financial Services", "JPMorgan Chase", "Banks and financials", "lending, deposits"),
        "XOM": ("Energy", "Exxon Mobil", "Energy", "producing, refining"),
    }

    for symbol, (sector, name, education_type, business_phrase) in examples.items():
        result = app.build_business_education(
            symbol,
            sector,
            name,
            app.classify_portfolio_role(symbol),
        )
        assert result["basis_label"] == "General sector education"
        assert result["education_type"] == education_type
        assert business_phrase in result["business_model"]
        assert result["growth_drivers"]
        assert result["business_risks"]
        assert result["strengthen_case"]
        assert result["weaken_case"]
        assert result["research_question"].endswith("?")


def test_etf_education_covers_exposure_holdings_concentration_and_overlap():
    for symbol, sector, expected_type in (
        ("SPY", "ETF", "Broad-market ETF"),
        ("SMH", "ETF", "Sector or technology ETF"),
        ("TLT", "Bond ETF", "Bond ETF"),
        ("GLD", "Commodity ETF", "Commodity ETF"),
    ):
        result = app.build_business_education(
            symbol,
            sector,
            symbol,
            app.classify_portfolio_role(symbol),
        )
        combined = " ".join(str(value) for value in result.values()).lower()
        assert result["is_etf"] is True
        assert result["education_type"] == expected_type
        assert "exposure" in combined or "portfolio" in result["business_model"].lower()
        assert "inspect" in result["holdings_check"].lower()
        assert "concentration" in combined
        assert "overlap" in combined


def test_unknown_company_uses_labelled_fallback_education():
    result = app.build_business_education(
        "UNKNOWN",
        "",
        "Unknown Company",
        {"key": "research", "label": "Research candidate"},
    )

    assert result["basis_label"] == "General sector education"
    assert result["education_type"] == "General research candidate"
    assert result["is_etf"] is False
    assert result["research_question"]


def test_logged_out_stock_source_does_not_receive_phase18_premium_content():
    response = render_stock_page()
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Premium locked preview" in page
    assert 'href="/upgrade"' in page
    for phrase in (
        "Today’s Context can change",
        "How the company generally makes money",
        "Main growth drivers",
        "What could strengthen the business case",
        "UNIQUE_PHASE18_SETUP_REASON",
        "UNIQUE_PHASE18_FULL_RISK_VIEW",
        "UNIQUE_PHASE18_FULL_WATCH_NEXT",
    ):
        assert phrase not in page


def test_premium_stock_source_receives_both_phase18_sections():
    response = render_stock_page(premium=True)
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Today’s Context" in page
    assert "Today’s Context can change" in page
    assert "UNIQUE_PHASE18_SETUP_REASON" in page
    assert "UNIQUE_PHASE18_FULL_RISK_VIEW" in page
    assert "UNIQUE_PHASE18_FULL_WATCH_NEXT" in page
    assert "Understand the Business" in page
    assert "General sector education" in page
    assert "How the company generally makes money" in page
    assert page.index("Today’s Context") < page.index("Premium decision summary")
    assert page.index("Think Before You Invest") < page.index("Understand the Business")
    assert page.index("Understand the Business") < page.index("Explore the reasoning")
