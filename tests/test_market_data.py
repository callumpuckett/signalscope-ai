from unittest.mock import patch

import pandas as pd

import app


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

