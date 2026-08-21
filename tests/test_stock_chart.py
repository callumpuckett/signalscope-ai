from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

import app


CHART_SCRIPT = Path(app.APP_ROOT) / "static" / "stock_chart.js"


def history_frame(prices, intraday=False):
    frequency = "15min" if intraday else "1D"
    index = pd.date_range("2026-08-18 09:00", periods=len(prices), freq=frequency)
    return pd.DataFrame({"Close": prices}, index=index)


def test_chart_has_only_one_permanent_latest_endpoint_and_no_smoothing():
    source = CHART_SCRIPT.read_text(encoding="utf-8")

    assert "pointRadius: function (context)" in source
    assert "context.dataIndex === points.length - 1 ? 4 : 0" in source
    assert "pointHoverRadius: 0" in source
    assert "tension: 0" in source
    assert "tension: 0.25" not in source


@pytest.mark.parametrize(
    ("prices", "expected"),
    (
        ([100, 101, 102], "buy"),
        ([102, 101, 100], "sell"),
        ([100, 115, 100], "hold"),
        ([100], "hold"),
        ([], "hold"),
    ),
)
def test_selected_timeframe_first_and_last_prices_determine_direction(prices, expected):
    assert app.chart_price_direction(prices) == expected


@pytest.mark.parametrize(
    ("currency", "expected"),
    (
        ("USD", "$1,234.50"),
        ("GBP", "£1,234.50"),
        ("EUR", "€1,234.50"),
        ("JPY", "¥1,234.50"),
        ("DKK", "DKK 1,234.50"),
        ("GBp", "1,234.50p"),
    ),
)
def test_chart_price_formatting_uses_security_currency(currency, expected):
    assert app.format_chart_price(1234.5, currency) == expected


def test_intraday_timestamp_format_preserves_observation_clock_time():
    timestamp = "2026-08-21T14:35:00+01:00"

    assert app.format_chart_timestamp(timestamp, "1h") == "14:35"
    assert app.format_chart_timestamp(timestamp, "24h") == "14:35"
    assert app.format_chart_timestamp(timestamp, "1w") == "Fri 14:35"


def test_longer_range_timestamp_format_adds_date_and_year_as_needed():
    timestamp = "2026-08-18T16:30:00+01:00"

    assert app.format_chart_timestamp(timestamp, "1mo") == "18 Aug"
    assert app.format_chart_timestamp(timestamp, "6mo") == "18 Aug"
    assert app.format_chart_timestamp(timestamp, "1y") == "18 Aug 2026"
    assert app.format_chart_timestamp(timestamp, "ytd") == "18 Aug 2026"


def test_invalid_null_and_infinite_prices_do_not_reach_chart_payload():
    index = pd.to_datetime(
        ["2026-08-18", "2026-08-18", "2026-08-19", "2026-08-20"]
    )
    history = pd.DataFrame({"Close": [10.0, None, float("inf"), 11.0]}, index=index)

    points = app.normalize_history_points(history, "AAPL")

    assert [point["price"] for point in points] == [10.0, 11.0]
    assert all(point["timestamp_ms"] is not None for point in points)


def test_empty_and_single_point_histories_render_safely():
    with patch.object(app, "safe_history", return_value=pd.DataFrame()):
        empty = app.stock_history("AAPL", "1mo")
    with patch.object(app, "safe_history", return_value=history_frame([182.46])):
        single = app.stock_history("AAPL", "1mo")

    assert empty["ok"] is False
    assert empty["points"] == []
    assert single["ok"] is True
    assert single["direction"] == "hold"
    assert len(single["points"]) == 1
    assert single["points"][0]["tooltip_label"] == "18 Aug"


def test_chart_template_supports_hover_touch_keyboard_and_accessible_summary():
    template = app.stock_detail_html
    source = CHART_SCRIPT.read_text(encoding="utf-8")

    assert 'aria-describedby="stock-chart-summary"' in template
    assert 'role="status" aria-live="polite"' in template
    assert 'data-latest-index="{{ chart_data.points|length - 1 }}"' in template
    assert "touch-action:pan-y" in template
    assert 'addEventListener("pointerdown"' in source
    assert 'addEventListener("pointermove"' in source
    assert 'addEventListener("pointerleave"' in source
    assert 'addEventListener("keydown"' in source
    assert "stockRadarInteractionGuide" in source


@pytest.mark.parametrize("range_key", tuple(app.CHART_RANGES))
def test_every_existing_stock_chart_timeframe_route_renders(range_key):
    history = history_frame([180.0, 181.25, 182.46], intraday=range_key in {"1h", "24h"})

    with (
        patch.object(app, "safe_history", return_value=history),
        patch.object(
            app,
            "get_dividend_context",
            return_value={"currency": "USD", "income_status": "unavailable"},
        ),
        patch.object(app, "premium_has_access", return_value=False),
    ):
        response = app.app.test_client().get(
            "/stock/AAPL",
            query_string={"range": range_key},
        )

    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'data-latest-index="2"' in page
    assert "/static/stock_chart.js" in page
    assert "stockChartPoints" in page
    assert "$182.46" in page


def test_stock_chart_static_module_is_served_locally():
    response = app.app.test_client().get("/static/stock_chart.js")

    assert response.status_code == 200
    assert b"StockRadarPriceChart" in response.data
