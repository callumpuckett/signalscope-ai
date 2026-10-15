"""Focused report composition, privacy and zero-additional-provider-call coverage."""
import time
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
import app


@pytest.fixture
def report_sources(monkeypatch):
    context = {
        "ticker": "MSFT", "signal": "BUY", "confidence": "82%",
        "confidence_meter": "████████░░", "strength_label": "Strong",
        "reason": "PRIVATE_REPORT_REASON", "risk_view": "PRIVATE_REPORT_RISK",
        "watch_next": "PRIVATE_REPORT_TRIGGER", "momentum_view": "Existing prompt",
        **app.signal_provenance({"signal_source": "generated"}),
    }
    monkeypatch.setattr(app, "get_stock_ai_context", Mock(return_value=context))
    monkeypatch.setattr(app, "stock_history", Mock(return_value={"ok": False, "error": "Unavailable", "points": [], "direction": "hold"}))
    monkeypatch.setattr(app, "stock_lifetime_growth", Mock(return_value={}))
    monkeypatch.setattr(app, "get_dividend_context", Mock(return_value={}))
    monkeypatch.setattr(app, "DIVIDEND_CONTEXT_CACHE", {})
    monkeypatch.setattr(app, "DASHBOARD_CACHE", {"timestamp": 0, "data": {}})
    monkeypatch.setattr(app, "get_stock_universe", lambda: [{"ticker": "MSFT", "name": "Microsoft", "sector": "Technology"}])
    return context


def subscriber(monkeypatch, active=True):
    monkeypatch.setattr(app, "premium_entitlement_record", lambda **kwargs: {
        "premium_active": active, "entitlement_version": 2,
    } if kwargs.get("email") else None)
    client = app.app.test_client()
    with client.session_transaction() as state:
        state.update(premium_email="reader@example.test", premium_active=True)
    return client


@pytest.mark.parametrize("route", ["/stock/MSFT", "/premium-decision/MSFT"])
@pytest.mark.parametrize("access", ["free", "inactive", "active"])
def test_report_access_uses_server_entitlement_without_shared_html(monkeypatch, report_sources, route, access):
    client = subscriber(monkeypatch, access == "active") if access != "free" else app.app.test_client()
    builder = Mock(wraps=app.build_intelligence_report)
    monkeypatch.setattr(app, "build_intelligence_report", builder)
    response = client.get(route)
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    if access == "active":
        assert 'id="intelligence-report"' in page
        for private in ("PRIVATE_REPORT_REASON", "PRIVATE_REPORT_RISK", "PRIVATE_REPORT_TRIGGER"):
            assert private in page
        assert "no-store" in response.headers["Cache-Control"]
        assert "Cookie" in response.headers["Vary"]
        builder.assert_called_once()
    else:
        assert 'id="intelligence-report"' not in page
        for private in ("PRIVATE_REPORT_REASON", "PRIVATE_REPORT_RISK", "PRIVATE_REPORT_TRIGGER"):
            assert private not in page
        builder.assert_not_called()
        assert "/upgrade" in page


def test_active_session_is_revoked_on_next_report_request(monkeypatch, report_sources):
    client = subscriber(monkeypatch)
    assert b'id="intelligence-report"' in client.get("/premium-decision/MSFT").data
    monkeypatch.setattr(app, "premium_entitlement_record", lambda **kwargs: {"premium_active": False})
    response = client.get("/premium-decision/MSFT")
    assert b"PRIVATE_REPORT_REASON" not in response.data
    with client.session_transaction() as state:
        assert not state.get("premium_active")


def test_stock_uses_existing_fetches_and_direct_report_is_cache_only(monkeypatch, report_sources):
    client = subscriber(monkeypatch)
    assert client.get("/stock/MSFT").status_code == 200
    app.stock_history.assert_called_once_with("MSFT", "1mo")
    app.stock_lifetime_growth.assert_called_once_with("MSFT")
    app.get_dividend_context.assert_called_once_with("MSFT")
    app.get_dividend_context.reset_mock()
    monkeypatch.setattr(app, "fetch_url_json", Mock(side_effect=AssertionError("No provider calls allowed")))
    monkeypatch.setattr(app, "get_cached_dashboard_data", Mock(side_effect=AssertionError("No dashboard refresh allowed")))
    response = client.get("/premium-decision/MSFT")
    assert response.status_code == 200
    app.get_dividend_context.assert_not_called()
    app.fetch_url_json.assert_not_called()
    app.get_cached_dashboard_data.assert_not_called()


def test_score_uses_existing_formula_and_generated_source_is_explicit(report_sources):
    report = app.build_intelligence_report("MSFT", report_sources, {})
    assert report["score"] == app.opportunity_score_components(report_sources)
    assert report["provenance"]["signal_source"] == "generated"
    assert report["provenance"]["observed_at"] is None
    assert report["fundamentals"] == []
    assert report["upcoming"] is None
    assert report["news"] == []
    assert "General sector" in report["business"]["basis_label"]


def test_unknown_source_does_not_turn_default_confidence_into_a_score(report_sources):
    context = dict(report_sources, **app.signal_provenance({"signal_source": "unavailable"}))
    assert app.build_intelligence_report("MSFT", context, {})["score"] is None


def test_observation_age_is_recomputed_and_source_preserved(monkeypatch, report_sources):
    context = dict(report_sources, signal_source="calculated", observed_at="2020-01-01T12:00:00Z", signal_cache_state="cached")
    provenance = app.build_intelligence_report("MSFT", context, {})["provenance"]
    assert provenance["signal_source"] == "calculated"
    assert provenance["signal_freshness"] == "stale"
    assert provenance["signal_cache_state"] == "cached"
    assert "2020-01-01" in provenance["observed_at"]


def test_stale_financial_cache_is_labelled_and_scheduled_event_suppressed(monkeypatch, report_sources):
    financial = {"ticker": "MSFT", "fundamentals": [
        {"key": "pe-ratio", "label": "P/E Ratio", "value": "31.4"},
        {"key": "next-earnings", "label": "Next Earnings", "value": "24 October 2030"},
    ]}
    monkeypatch.setattr(app, "DIVIDEND_CONTEXT_CACHE", {"MSFT": {
        "timestamp": time.time() - app.DIVIDEND_CONTEXT_CACHE_TTL_SECONDS - app.DIVIDEND_CONTEXT_UNAVAILABLE_CACHE_TTL_SECONDS - 10,
        "context": financial,
    }})
    report = app.build_intelligence_report("MSFT", report_sources)
    assert report["fundamentals"][0]["value"] == "31.4"
    assert report["financial_state"] == "Stale cached data"
    assert report["financial_retrieved_at"].endswith("UTC")
    assert report["upcoming"] is None
    mismatch = dict(financial, ticker="AAPL")
    assert app.build_intelligence_report("MSFT", report_sources, mismatch)["fundamentals"] == []


def test_news_requires_symbol_association_and_never_upgrades_theme_to_company(monkeypatch, report_sources):
    def headline(ticker, association):
        return {"label": "LIVE NEWS", "headline": "Cached headline", "article_url": "https://example.test/article",
                "published_at": "2020-01-01T12:00:00Z", "stock_associations": [{"ticker": ticker, "association": association}]}
    monkeypatch.setattr(app, "DASHBOARD_CACHE", {"data": {"live_headlines": [
        headline("AAPL", "company_mention"), headline("MSFT", "sector_or_market"),
        headline("MSFT", "company_mention"),
        {"label": "LIVE NEWS", "headline": "Legacy ambiguous match", "stock_links": [{"ticker": "MSFT"}]},
    ]}})
    report = app.build_intelligence_report("MSFT", report_sources, {})
    assert len(report["news"]) == 2
    assert report["news"][0]["basis"] == "Sector or market association"
    assert report["news"][1]["basis"] == "Company-name mention"
    assert "2020-01-01" in report["news"][1]["published_at"]


def test_render_contains_all_sections_unavailable_states_and_escapes_content(monkeypatch, report_sources):
    report_sources["reason"] = '<script>alert("untrusted")</script>'
    page = subscriber(monkeypatch).get("/premium-decision/MSFT").get_data(as_text=True)
    for heading in ("Investment Overview", "02 · Opportunities", "03 · Risks", "Portfolio Intelligence", "What to Watch Next"):
        assert heading in page
    assert "&lt;script&gt;" in page
    assert '<script>alert("untrusted")</script>' not in page
    assert "Financial metrics unavailable" in page
    assert "Observation time unavailable" in page
    assert "Actual portfolio exposure and concentration are unavailable" in page
    assert "reliable upcoming event date is unavailable" in page
    assert "Company-specific news and relevant sector headlines are unavailable" in page
    assert "heuristic" in page


def test_existing_news_api_does_not_serialize_report_content(monkeypatch, report_sources):
    monkeypatch.setattr(app, "get_cached_dashboard_data", lambda **kwargs: {})
    response = app.app.test_client().get("/api/market-news")
    assert response.status_code == 200
    assert "PRIVATE_REPORT" not in response.get_data(as_text=True)


def test_fresh_cached_provider_values_and_event_date_are_reused(monkeypatch, report_sources):
    from datetime import timedelta
    earnings = (datetime.now(timezone.utc) + timedelta(days=4)).strftime("%d %B %Y")
    financial = {"ticker": "MSFT", "income_status": "available", "source_note": "Unit-test provider fixture", "fundamentals": [
        {"key": "pe-ratio", "label": "P/E Ratio", "value": "31.4"},
        {"key": "next-earnings", "label": "Next Earnings", "value": earnings + " · Estimated"},
    ]}
    monkeypatch.setattr(app, "DIVIDEND_CONTEXT_CACHE", {"MSFT": {"timestamp": time.time(), "context": financial}})
    report = app.build_intelligence_report("MSFT", report_sources)
    assert report["financial_state"] == "Cached"
    assert report["upcoming"] == earnings + " · Estimated"
    assert report["fundamentals"] == financial["fundamentals"][:1]
    page = subscriber(monkeypatch).get("/premium-decision/MSFT").get_data(as_text=True)
    assert "31.4" in page
    assert earnings in page
    assert "Reporting periods are unavailable here" in page
    assert "not the financial reporting period" in page


def test_incomplete_cache_records_and_bad_article_urls_fail_closed(monkeypatch, report_sources):
    monkeypatch.setattr(app, "DIVIDEND_CONTEXT_CACHE", {"MSFT": {"timestamp": time.time(), "context": {"ticker": "MSFT", "fundamentals": None}}})
    monkeypatch.setattr(app, "DASHBOARD_CACHE", {"data": {"live_headlines": [
        {"label": "LIVE NEWS", "stock_associations": [{"ticker": "MSFT", "association": "company_mention"}], "article_url": "https://[broken"},
        {"label": "LIVE NEWS", "stock_associations": None},
    ]}})
    report = app.build_intelligence_report("MSFT", report_sources)
    assert report["fundamentals"] == []
    assert report["news"] == []
