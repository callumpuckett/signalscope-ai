from unittest.mock import patch

import pandas as pd

import app


def test_stock_display_labels_use_universe_company_names():
    microsoft = app.stock_display_label("MSFT")
    apple = app.stock_display_label("AAPL")
    palantir = app.stock_display_label("PLTR")

    assert "Microsoft" in microsoft and "(MSFT)" in microsoft
    assert "Apple" in apple and "(AAPL)" in apple
    assert "Palantir" in palantir and "(PLTR)" in palantir


def test_stock_display_label_falls_back_safely_for_unknown_ticker():
    assert app.stock_display_label("UNKNOWN") == "UNKNOWN"


def test_stock_display_label_handles_unavailable_universe():
    with patch.object(app, "get_stock_universe", side_effect=RuntimeError("unavailable")):
        assert app.stock_display_label("UNKNOWN") == "UNKNOWN"


def test_stock_display_label_avoids_duplicate_ticker_names():
    rows = [{"ticker": "MSFT", "name": "MSFT"}]

    with patch.object(app, "get_stock_universe", return_value=rows):
        assert app.stock_display_label("MSFT") == "MSFT"


def test_universe_search_displays_company_and_ticker_links():
    client = app.app.test_client()

    microsoft_response = client.get("/universe?q=microsoft")
    microsoft_page = microsoft_response.get_data(as_text=True)
    apple_response = client.get("/universe?q=apple")
    apple_page = apple_response.get_data(as_text=True)

    assert microsoft_response.status_code == 200
    assert "Microsoft" in microsoft_page
    assert "(MSFT)" in microsoft_page
    assert 'href="/stock/MSFT"' in microsoft_page

    assert apple_response.status_code == 200
    assert "Apple" in apple_page
    assert "(AAPL)" in apple_page
    assert 'href="/stock/AAPL"' in apple_page


def test_palantir_alias_redirects_and_displays_company_with_ticker():
    index = pd.to_datetime(["2026-01-01", "2026-01-02"])
    history = pd.DataFrame({"Close": [22.0, 24.5]}, index=index)

    with patch.object(app, "safe_history", return_value=history):
        response = app.app.test_client().get("/stock/palantir", follow_redirects=True)

    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert response.request.path == "/stock/PLTR"
    assert "Palantir Technologies Inc. (PLTR)" in page
    assert 'href="/premium-decision/PLTR"' in page


def test_generated_stock_links_keep_ticker_urls_and_add_display_labels():
    links = app.build_stock_links_with_signals(["MSFT"])

    assert links[0]["url"] == "/stock/MSFT"
    assert "Microsoft" in links[0]["display_label"]
    assert "(MSFT)" in links[0]["display_label"]


def test_premium_watchlist_and_portfolio_fit_use_display_labels():
    client = app.app.test_client()
    with client.session_transaction() as current_session:
        current_session["owner_logged_in"] = True

    watchlist = client.get("/premium-watchlist").get_data(as_text=True)
    portfolio = client.post(
        "/portfolio-fit",
        data={"holdings": "MSFT,AAPL"},
    ).get_data(as_text=True)

    assert "Microsoft" in watchlist and "(MSFT)" in watchlist
    assert "Microsoft" in portfolio and "(MSFT)" in portfolio
    assert "Apple" in portfolio and "(AAPL)" in portfolio
