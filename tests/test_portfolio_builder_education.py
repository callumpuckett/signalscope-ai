from unittest.mock import patch

import pandas as pd

import app


def stock_history_fixture():
    return pd.DataFrame(
        {"Close": [100.0, 102.0]},
        index=pd.to_datetime(["2026-01-01", "2026-01-02"]),
    )


def render_stock_page(symbol, premium=False):
    client = app.app.test_client()
    if premium:
        with client.session_transaction() as current_session:
            current_session["owner_logged_in"] = True

    with (
        patch.object(app, "safe_history", return_value=stock_history_fixture()),
        patch.object(app, "get_dividend_context", return_value=None),
    ):
        return client.get(f"/stock/{symbol}")


def test_portfolio_builder_rules_cover_phase_16_examples():
    expected = {
        "MSFT": ("growth", "technology-heavy ETFs", "satellite"),
        "KO": ("dividend", "slow-growth", "defensive satellite"),
        "SPY": ("broad_market_etf", "largest holdings", "core role"),
        "PLTR": ("growth", "same growth drivers", "satellite"),
        "JPM": ("cyclical", "interest-rate, credit and economic conditions", "satellite"),
        "XOM": ("cyclical", "commodity prices and geopolitical risks", "satellite"),
    }

    for symbol, (role_key, overlap_text, core_text) in expected.items():
        role = app.classify_portfolio_role(symbol)
        education = app.build_portfolio_builder_education(symbol, role)

        assert role["key"] == role_key
        assert overlap_text in education["overlap"]
        assert core_text in education["core_label"].lower()
        assert education["role_meaning"].startswith("This type of holding may serve as")
        assert len(education["checklist"]) == 7
        assert len(education["principle"].split()) <= 50


def test_free_stock_pages_do_not_render_portfolio_builder_content():
    premium_only_phrases = (
        "How this may fit in a portfolio",
        "Portfolio overlap to check",
        "Core or satellite?",
        "Why position size matters",
        "Before adding this holding",
        "Portfolio mistake to avoid",
        "Portfolio principle",
        "If this holding fell sharply",
    )

    for symbol in ("MSFT", "KO", "SPY"):
        response = render_stock_page(symbol)
        page = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "Premium locked preview" in page
        assert 'href="/upgrade"' in page
        for phrase in premium_only_phrases:
            assert phrase not in page


def test_premium_stock_pages_render_builder_in_the_expected_hierarchy():
    for symbol in ("MSFT", "KO", "SPY", "PLTR", "JPM", "XOM"):
        response = render_stock_page(symbol, premium=True)
        page = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "Premium decision summary" in page
        assert "Learn From This Stock" in page
        assert "How this may fit in a portfolio" in page
        assert "Portfolio overlap to check" in page
        assert "Core or satellite?" in page
        assert "Why position size matters" in page
        assert "Before adding this holding" in page
        assert "Portfolio mistake to avoid" in page
        assert "Portfolio principle" in page
        assert "Explore the reasoning" in page
        assert page.index("Learn From This Stock") < page.index("How this may fit in a portfolio")
        assert page.index("How this may fit in a portfolio") < page.index("Explore the reasoning")


def test_compare_portfolio_roles_is_premium_only():
    free_response = app.app.test_client().get("/compare/MSFT/PLTR")
    free_page = free_response.get_data(as_text=True)

    premium_client = app.app.test_client()
    with premium_client.session_transaction() as current_session:
        current_session["owner_logged_in"] = True

    with patch.object(app, "get_dividend_context", return_value={
        "has_dividend_data": False,
        "no_data_message": "No regular dividend found.",
        "source_note": "Educational data context.",
    }):
        premium_response = premium_client.get("/compare/MSFT/PLTR")
    premium_page = premium_response.get_data(as_text=True)

    assert free_response.status_code == 200
    assert "Compare portfolio roles" not in free_page
    assert premium_response.status_code == 200
    assert "Compare portfolio roles" in premium_page
    assert "Two strong companies may still serve the same role" in premium_page
    assert "Similar exposure to check" in premium_page


def test_portfolio_builder_uses_general_stock_and_etf_education_only():
    for symbol in ("MSFT", "KO", "SPY", "PLTR", "JPM", "XOM"):
        education = app.build_portfolio_builder_education(
            symbol,
            app.classify_portfolio_role(symbol),
        )
        combined = " ".join(
            str(value)
            for key, value in education.items()
            if key != "checklist"
        ).lower()

        assert "crypto" not in combined
        assert "bitcoin" not in combined

