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
