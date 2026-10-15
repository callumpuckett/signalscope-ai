from datetime import datetime, timezone
from unittest.mock import patch

import pytest
import app


def test_expansion_preserves_supplied_source_and_marks_generated_rows():
    rows = app.expand_recommendations([
        {"ticker": "MSFT", "signal": "BUY", "confidence": "82%", "signal_source": "calculated",
         "observed_at": "2026-01-01T12:00:00Z"},
        {"ticker": "AAPL", "signal": "HOLD", "confidence": "58%"},
    ])
    assert rows[0]["signal_source"] == "calculated"
    assert rows[0]["observed_at"] == "2026-01-01T12:00:00+00:00"
    assert rows[1]["signal_source"] == "supplied"
    assert rows[1]["observed_at"] is None
    assert all(row["signal_source"] == "generated" for row in rows[2:])
    with patch.object(app, "get_recommendations", return_value=rows):
        context = app.get_stock_ai_context(rows[2]["ticker"])
    assert context["signal_source"] == "generated"
    assert "generated prompt" in context["risk_view"]


@pytest.mark.parametrize("stamp", [None, "invalid", "2026-01-01", "2026-01-01T12:00:00", "2999-01-01T12:00:00Z"])
def test_invalid_or_unverifiable_observation_is_not_current(stamp):
    result = app.signal_provenance({"signal_source": "supplied", "observed_at": stamp})
    assert result["observed_at"] is None
    assert result["signal_freshness"] == "unavailable"


def test_cache_hit_ages_observation_without_changing_source_or_cached_rows(monkeypatch):
    now = datetime(2026, 1, 1, 12, tzinfo=timezone.utc).timestamp()
    row = {"ticker": "MSFT", "signal": "BUY", "confidence": "82%", "signal_source": "calculated",
           "observed_at": "2026-01-01T12:00:00Z"}
    monkeypatch.setattr(app, "RECOMMENDATIONS_CACHE", {"timestamp": now, "rows": [row]})
    with patch.object(app.time, "time", return_value=now + 1):
        fresh = app.get_recommendations()[0]
    assert fresh["signal_freshness"] == "current"
    assert fresh["signal_cache_state"] == "cached"
    # Cache can remain valid after its underlying observation is stale.
    row["observed_at"] = "2025-12-31T12:00:00Z"
    with patch.object(app.time, "time", return_value=now + 2):
        stale = app.get_recommendations()[0]
    assert stale["signal_freshness"] == "stale"
    assert stale["signal_source"] == "calculated"
    assert "signal_cache_state" not in row


def test_csv_metadata_and_default_fallback_are_distinct(monkeypatch, tmp_path):
    path = tmp_path / "signals.csv"
    path.write_text("ticker,signal,confidence,signal_source,observed_at\nMSFT,BUY,82%,calculated,2026-01-01T12:00:00Z\n")
    monkeypatch.setattr(app, "CSV_CANDIDATES", [str(path)])
    monkeypatch.setattr(app, "RECOMMENDATIONS_CACHE", {"timestamp": 0, "rows": None})
    imported = app.get_recommendations()[0]
    assert imported["signal_source"] == "calculated"
    assert imported["observed_at"]
    monkeypatch.setattr(app, "CSV_CANDIDATES", [])
    monkeypatch.setattr(app, "RECOMMENDATIONS_CACHE", {"timestamp": 0, "rows": None})
    fallback = app.get_recommendations()[0]
    assert fallback["signal_source"] == "fallback"
    assert fallback["observed_at"] is None
    assert fallback["signal_cache_state"] == "freshly loaded"


def test_synthetic_sources_never_acquire_observation_dates():
    for source in ("generated", "fallback", "unavailable"):
        result = app.signal_provenance({"signal_source": source, "observed_at": "2026-01-01T12:00:00Z"})
        assert result["observed_at"] is None
        assert result["signal_freshness"] == "unavailable"


def test_news_theme_is_not_company_news_and_name_mention_is_not_verification():
    theme = app.news_stock_associations("Semiconductor chips gain as demand grows")
    assert theme
    assert all(row["association"] == "sector_or_market" for row in theme)
    direct = app.news_stock_associations("Nvidia expands semiconductor production")
    assert next(row for row in direct if row["ticker"] == "NVDA")["association"] == "company_mention"
    assert all(row["verified_company_news"] is False for row in direct)
    unrelated = app.news_stock_associations("Unrelated headline with no company names")
    assert all(row["association"] == "sector_or_market" for row in unrelated)
    plain = app.build_today_context({}, "Chips demand grows")
    assert "not confirmed company news" in plain["news_basis"]


def test_score_values_unchanged_by_source_metadata():
    row = {"ticker": "MSFT", "signal": "BUY", "confidence": "82%"}
    base = app.opportunity_score_components(row)
    assert app.opportunity_score_components(dict(row, signal_source="fallback")) == base
    assert base["fundamentals_score"] == 15
    assert base["valuation_score"] == 3
    assert "Fundamentals category (heuristic)" in app.opportunities_html
    assert "Valuation allowance (heuristic)" in app.opportunities_html


def test_premium_report_source_is_visible_only_with_existing_entitlement():
    context = app.get_stock_ai_context("MSFT")
    context["reason"] = "PRIVATE_PREREQUISITE_REASON"
    context.update(app.signal_provenance({"signal_source": "fallback"}))
    with patch.object(app, "get_stock_ai_context", return_value=context):
        with patch.object(app, "premium_has_access", return_value=False):
            locked = app.app.test_client().get("/premium-decision/MSFT").get_data(as_text=True)
        with patch.object(app, "premium_has_access", return_value=True):
            unlocked = app.app.test_client().get("/premium-decision/MSFT").get_data(as_text=True)
    assert "PRIVATE_PREREQUISITE_REASON" not in locked
    assert "PRIVATE_PREREQUISITE_REASON" in unlocked
    assert "Fallback research prompt" in unlocked
    assert "Observation time unavailable" in unlocked


def test_news_classification_survives_public_feed_serialization():
    article = {"title": "Nvidia expands semiconductor production", "source": "Reuters",
               "url": "https://www.reuters.com/example", "published_at": "2026-01-01T12:00:00Z"}
    with patch.object(app, "fetch_live_market_news", return_value=[article]):
        headlines = app.build_live_headlines([], [])
    links = headlines[0]["stock_links"]
    assert next(link for link in links if link["ticker"] == "NVDA")["association"] == "company_mention"
    assert any(link["association"] == "sector_or_market" for link in links)
    # The feed serializer is used by the public polling endpoint.
    with patch.object(app, "is_public_live_market_headline", return_value=True):
        items = app.serialize_market_news_items(headlines)
    assert items[0]["stock_links"][0]["association"] in {"company_mention", "sector_or_market"}
