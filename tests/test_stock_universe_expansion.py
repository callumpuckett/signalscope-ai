import csv
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import app


ROOT_DIR = Path(__file__).resolve().parents[1]
UNIVERSE_PATH = ROOT_DIR / "stock_universe.csv"


def load_rows():
    with UNIVERSE_PATH.open(newline="", encoding="utf-8-sig") as csvfile:
        return list(csv.DictReader(csvfile))


def test_expanded_universe_shape_and_required_symbols():
    rows = load_rows()
    required_columns = {"ticker", "name", "exchange", "sector"}
    tickers = [row["ticker"].strip().upper() for row in rows]

    assert len(rows) >= 1000
    assert required_columns.issubset(rows[0])
    assert len(tickers) == len(set(tickers))
    assert {"MAERSK-B.CO", "MAERSK-A.CO", "AZN.L", "NVDA"}.issubset(tickers)


def test_expanded_universe_group_counts():
    rows = load_rows()

    assert sum(row.get("universe_group") == "US" for row in rows) == 750
    assert sum(row.get("universe_group") == "UK" for row in rows) == 250
    assert sum(row.get("universe_group") == "Global Extras" for row in rows) == 2


def test_maersk_aliases_resolve_to_yahoo_symbols():
    assert app.canonical_stock_symbol("Maersk") == "MAERSK-B.CO"
    assert app.canonical_stock_symbol("Maersk B") == "MAERSK-B.CO"
    assert app.canonical_stock_symbol("Maersk A") == "MAERSK-A.CO"
    assert app.canonical_stock_symbol("A P Moller Maersk") == "MAERSK-B.CO"
    assert app.canonical_stock_symbol("AP Moller Maersk") == "MAERSK-B.CO"


def test_bae_founder_alias_uses_yahoo_equity_symbol():
    assert app.canonical_stock_symbol("BAE.L") == "BA.L"
    assert app.canonical_stock_symbol("BAE Systems") == "BA.L"


def test_required_universe_searches_return_expected_names():
    client = app.app.test_client()
    searches = {
        "Maersk": ("Mærsk", "MAERSK-B.CO"),
        "MAERSK-B.CO": ("Mærsk", "MAERSK-B.CO"),
        "Apple": ("Apple", "AAPL"),
        "Nvidia": ("NVIDIA", "NVDA"),
        "AstraZeneca": ("AstraZeneca", "AZN.L"),
        "Rolls-Royce": ("Rolls-Royce", "RR.L"),
        "BAE Systems": ("BAE Systems", "BA.L"),
        "Halma": ("Halma", "HLMA.L"),
    }

    for query, (name, ticker) in searches.items():
        response = client.get("/universe", query_string={"q": query})
        page = response.get_data(as_text=True)

        assert response.status_code == 200
        assert name in page
        assert f"/stock/{ticker}" in page


def test_maersk_stock_page_returns_200_without_fabricated_chart_data():
    with patch.object(app, "safe_history", return_value=pd.DataFrame()):
        response = app.app.test_client().get("/stock/MAERSK-B.CO")

    assert response.status_code == 200
    assert b"MAERSK-B.CO" in response.data
    assert b"Chart unavailable" in response.data


def test_expanded_universe_stocks_receive_generated_research_prompts():
    symbols = {"MAERSK-B.CO", "MAERSK-A.CO", "RR.L", "BA.L", "HLMA.L"}

    with patch.object(app, "get_recommendations", return_value=[]):
        contexts = {symbol: app.get_stock_ai_context(symbol) for symbol in symbols}

    for symbol, context in contexts.items():
        assert context["ticker"] == symbol
        assert context["signal"] in {"BUY", "HOLD", "SELL"}
        assert context["signal"] != "WATCH"
        assert context["confidence"] != "50%"
        assert "StockRadar expanded universe" in context["reason"]
        assert "not currently inside the AI recommendation table" not in context["reason"]
        assert context["momentum_view"] != "Watchlist setup"


def test_maersk_share_classes_use_same_generated_signal_and_confidence():
    with patch.object(app, "get_recommendations", return_value=[]):
        class_b = app.get_stock_ai_context("MAERSK-B.CO")
        class_a = app.get_stock_ai_context("MAERSK-A.CO")

    assert class_b["ticker"] == "MAERSK-B.CO"
    assert class_a["ticker"] == "MAERSK-A.CO"
    assert class_b["signal"] != "WATCH"
    assert class_a["signal"] != "WATCH"
    assert class_a["signal"] == class_b["signal"]
    assert class_a["confidence"] == class_b["confidence"]
    assert "Class B" in class_b["reason"]
    assert "Class A" in class_a["reason"]


def test_unknown_ticker_keeps_generic_watch_fallback():
    with patch.object(app, "get_recommendations", return_value=[]):
        context = app.get_stock_ai_context("NOTAREALTICKER")

    assert context["signal"] == "WATCH"
    assert context["confidence"] == "50%"
    assert "not currently inside the AI recommendation table" in context["reason"]
    assert context["momentum_view"] == "Watchlist setup"


def test_requested_expanded_stock_pages_return_200():
    client = app.app.test_client()

    with patch.object(app, "safe_history", return_value=pd.DataFrame()):
        responses = {
            symbol: client.get(f"/stock/{symbol}")
            for symbol in ("MAERSK-B.CO", "MAERSK-A.CO", "BA.L", "RR.L")
        }

    assert all(response.status_code == 200 for response in responses.values())


def test_sitemap_includes_full_universe_and_www_domain():
    response = app.app.test_client().get("/sitemap.xml")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "https://www.stockradarhq.com/stock/MAERSK-B.CO" in page
    assert "https://www.stockradarhq.com/stock/AZN.L" in page
    assert "https://www.stockradarhq.com/stock/NVDA" in page
    assert "/admin/" not in page
    assert "/login" not in page
    assert "checkout" not in page
