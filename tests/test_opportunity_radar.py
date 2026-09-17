import pytest
from datetime import datetime, timezone
from unittest.mock import patch

import app
import newsletter_storage

@pytest.fixture(autouse=True)
def isolate_yahoo_refresh_state(monkeypatch):
    monkeypatch.setattr(app, "YAHOO_COOLDOWN_UNTIL", 0.0)
    monkeypatch.setattr(app, "YAHOO_HISTORY_CACHE", {})
    monkeypatch.setattr(app, "DIVIDEND_CONTEXT_CACHE", {})
    monkeypatch.setattr(app, "INCOME_HISTORY_CACHE", {})
    monkeypatch.setattr(app, "OPPORTUNITY_PAGE_CACHE", None)



def recommendation(ticker, signal="BUY", confidence="80%", reason="Strong quality momentum"):
    return {"ticker": ticker, "signal": signal, "confidence": confidence, "reason": reason}


def no_market_data(_ticker):
    return {"current_price": None, "current_price_label": "Unavailable", "period_change": None}


def test_ranking_is_deterministic_bounded_and_tie_breaks_by_ticker():
    rows = [
        recommendation("MSFT", confidence="88%"),
        recommendation("AAPL", confidence="88%"),
        recommendation("TSLA", signal="HOLD", confidence="55%", reason="Mixed volatility"),
    ]

    first = app.rank_stockradar_opportunities(rows)
    second = app.rank_stockradar_opportunities(list(reversed(rows)))

    assert [row["ticker"] for row in first] == [row["ticker"] for row in second]
    assert [row["ticker"] for row in first[:2]] == ["AAPL", "MSFT"]
    assert all(0 <= row["opportunity_score"] <= 100 for row in first)
    assert all(
        row["opportunity_score"]
        == row["signal_score"] + row["conviction_score"] + row["momentum_score"]
        + row["fundamentals_score"] + row["risk_score"] + row["valuation_score"]
        for row in first
    )


def test_snapshot_tracks_new_rising_falling_unchanged_and_exited():
    previous = app.build_opportunity_snapshot(
        [
            recommendation("AAPL", confidence="70%"),
            recommendation("MSFT", confidence="90%"),
            recommendation("GOOGL", confidence="80%"),
            recommendation("META", confidence="75%"),
            recommendation("AMZN", confidence="72%"),
        ],
        now=datetime(2026, 8, 30, tzinfo=timezone.utc),
        market_data_provider=no_market_data,
    )
    current = app.build_opportunity_snapshot(
        [
            recommendation("AAPL", confidence="99%"),
            recommendation("MSFT", confidence="60%"),
            recommendation("GOOGL", confidence="80%"),
            recommendation("META", confidence="75%"),
            recommendation("NVDA", confidence="78%"),
        ],
        previous_snapshot=previous,
        now=datetime(2026, 8, 31, tzinfo=timezone.utc),
        market_data_provider=no_market_data,
    )
    statuses = {row["ticker"]: row["status"] for row in current["opportunities"]}

    assert statuses["AAPL"] == "RISING"
    assert statuses["MSFT"] == "FALLING"
    assert statuses["GOOGL"] == "UNCHANGED"
    assert statuses["NVDA"] == "NEW"
    assert {row["ticker"] for row in current["exited"]} == {"AMZN"}
    assert current["exited"][0]["status"] == "EXITED"


def test_history_returns_last_thirty_daily_scores_and_points():
    state = {"snapshots": {}}
    for day in range(1, 33):
        date = f"2026-07-{day:02d}"
        state["snapshots"][date] = {
            "opportunities": [{"ticker": "MSFT", "opportunity_score": day, "rank": 1}]
        }

    history = app.opportunity_history(state, "MSFT")

    assert len(history) == 30
    assert history[0]["score"] == 3
    assert history[-1]["score"] == 32
    assert len(app.opportunity_history_points(history).split()) == 30


def test_alert_events_are_opt_in_filtered_and_deduplicated():
    alert_state = {
        "preferences": {
            "owner": {
                **app.default_opportunity_alert_preferences(),
                "enabled": True,
                "tickers": ["MSFT"],
            }
        },
        "events": [],
    }
    previous = {"opportunities": [{"ticker": "MSFT", "signal": "HOLD", "risk": "Medium", "rank": 2}]}
    snapshot = {
        "snapshot_date": "2026-08-31",
        "generated_at": "2026-08-31T08:00:00+00:00",
        "opportunities": [{
            "ticker": "MSFT", "signal": "BUY", "risk": "Managed", "rank": 1,
            "rank_change": 1, "score_change": 3, "status": "RISING",
        }],
    }

    def update(_store, updater):
        updater(alert_state)
        return True

    with (
        patch.object(app, "newsletter_storage_load", return_value=alert_state),
        patch.object(app, "newsletter_storage_update", side_effect=update),
    ):
        assert app.record_opportunity_alert_events(snapshot, previous)
        assert app.record_opportunity_alert_events(snapshot, previous)

    assert len(alert_state["events"]) == 1
    assert alert_state["events"][0]["delivery_status"] == "tracked"
    assert len(alert_state["events"][0]["reasons"]) == 4


def test_alert_events_include_top_five_exits():
    alert_state = {
        "preferences": {
            "owner": {
                **app.default_opportunity_alert_preferences(),
                "enabled": True,
                "tickers": ["MSFT"],
            }
        },
        "events": [],
    }
    previous = {"opportunities": [{"ticker": "MSFT", "signal": "BUY", "risk": "Managed", "rank": 2}]}
    snapshot = {
        "snapshot_date": "2026-08-31",
        "generated_at": "2026-08-31T08:00:00+00:00",
        "opportunities": [],
        "exited": [{"ticker": "MSFT", "status": "EXITED", "signal": "BUY", "risk": "Managed", "rank": 2}],
    }

    def update(_store, updater):
        updater(alert_state)
        return True

    with (
        patch.object(app, "newsletter_storage_load", return_value=alert_state),
        patch.object(app, "newsletter_storage_update", side_effect=update),
    ):
        assert app.record_opportunity_alert_events(snapshot, previous)

    assert alert_state["events"][0]["reasons"] == ["exited Top 5"]


def test_free_preview_is_locked_and_only_reads_existing_opportunity_storage():
    with (
        patch.object(app, "premium_has_access", return_value=False),
        patch.object(app, "get_dividend_context", return_value={}),
        patch.object(app, "get_recommendations", return_value=[recommendation("MSFT")]),
        patch.object(app, "newsletter_storage_load", return_value={}) as storage_load,
        patch.object(app, "newsletter_storage_update") as storage_update,
    ):
        response = app.app.test_client().get("/opportunities")

    assert response.status_code == 200
    assert b"Premium preview" in response.data
    assert b"Get StockRadar Weekly free" in response.data
    storage_load.assert_called_once_with("opportunity_radar")
    storage_update.assert_not_called()


def test_premium_page_shows_full_ranking_history_and_deferred_alerts():
    snapshot = app.build_opportunity_snapshot(
        [recommendation("MSFT")],
        now=datetime(2026, 8, 31, tzinfo=timezone.utc),
        market_data_provider=no_market_data,
    )
    state = {"snapshots": {snapshot["snapshot_date"]: snapshot}}
    with (
        patch.object(app, "premium_has_access", return_value=True),
        patch.object(app, "owner_has_access", return_value=True),
        patch.object(app, "ensure_daily_opportunity_snapshot", return_value=(snapshot, state, False)),
        patch.object(app, "get_dividend_context", return_value={}),
        patch.object(app, "newsletter_storage_load", side_effect=AssertionError("HTML must not read alert storage")),
    ):
        response = app.app.test_client().get("/opportunities")

    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "MSFT" in page
    assert "Historical Opportunity Score" in page
    assert "Premium watchlist alerts" in page
    assert 'href="/opportunities/alerts"' in page
    assert "window.addEventListener('load'" not in page
    assert page.index('id="alerts"') < page.index("(async function () {")
    assert "})();\n</script>" in page
    assert 'action="/opportunities/alerts"' not in page


def test_premium_user_can_save_sanitized_alert_preferences():
    stored = {}

    def update(_store, updater):
        updater(stored)
        return True

    with (
        patch.object(app, "premium_has_access", return_value=True),
        patch.object(app, "owner_has_access", return_value=True),
        patch.object(app, "newsletter_storage_update", side_effect=update),
    ):
        response = app.app.test_client().post(
            "/opportunities/alerts",
            data={
                "enabled": "on",
                "signal_changes": "on",
                "score_changes": "on",
                "tickers": "msft, aapl, msft",
            },
        )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/opportunities#alerts")
    preferences = stored["preferences"]["owner"]
    assert preferences["enabled"] is True
    assert preferences["tickers"] == ["MSFT", "AAPL"]
    assert preferences["risk_changes"] is False


def test_opportunity_storage_defaults_are_postgresql_application_state_stores():
    assert newsletter_storage.state_default("opportunity_radar") == {
        "snapshots": {}, "latest_snapshot_date": "",
    }
    assert newsletter_storage.state_default("opportunity_alerts") == {
        "preferences": {}, "events": [],
    }
    assert {"opportunity_radar", "opportunity_alerts"}.issubset(
        newsletter_storage.APPLICATION_STATE_STORES
    )


def test_shared_header_uses_flagship_desktop_brand_with_proportionate_mobile_scale():
    with app.app.test_request_context("/opportunities"):
        header = app.stockradar_header_navigation("app")

    assert ".public-header .logo{display:block;width:310px;max-width:310px" in header
    assert 'src="/static/stockradar-header-logo.png"' in header
    assert "width:100%;max-width:310px;max-height:none;height:auto" in header
    assert "padding:3px max(24px" in header
    assert "flex-wrap:nowrap" in header
    assert ".public-header .logo{width:190px;}" in header
    assert "max-width:190px;max-height:none" in header


def test_dense_authenticated_header_uses_accessible_overflow_menu():
    with (
        app.app.test_request_context("/opportunities"),
        patch.object(app, "premium_has_access", return_value=True),
        patch.object(app, "owner_has_access", return_value=True),
    ):
        header = app.stockradar_header_navigation("app")

    assert 'class="public-header-inner nav-density-high"' in header
    assert 'data-stockradar-menu-toggle' in header
    assert '.public-header-inner.nav-density-high.nav-menu-open .public-nav-links{display:grid;}' in header
    assert 'href="/opportunities"' in header
    assert 'href="/premium-watchlist"' in header
    assert 'href="/compare"' in header
    assert 'href="/beginner"' in header
    assert 'action="/logout"' in header


def test_header_reserves_logo_column_and_mobile_overrides_dense_layout():
    with app.app.test_request_context("/"):
        header = app.stockradar_header_navigation("public")

    assert "grid-template-columns:310px minmax(0,1fr) auto" in header
    assert ".public-header{box-sizing:border-box;" in header
    assert "@media(max-width:1100px)" in header
    assert ".stockradar-menu-toggle,.public-header-inner.nav-density-high .stockradar-menu-toggle{display:inline-flex;grid-column:2;}" in header
    assert ".public-nav-links,.public-header-inner.nav-density-high .public-nav-links{grid-column:1/-1;right:0;left:0;width:auto;" in header


def test_homepage_promotes_daily_opportunity_research_without_extra_marketing_section():
    dashboard_data = {
        "market_status": {
            "uk_status": "CLOSED", "uk_time": "00:00",
            "us_status": "CLOSED", "us_time": "00:00",
        }
    }
    with (
        patch.object(app, "get_cached_dashboard_data", return_value=dashboard_data),
        patch.object(app, "get_stock_universe", return_value=[]),
    ):
        free_page = app.app.test_client().get("/").get_data(as_text=True)

    assert "Opportunity Radar" in free_page
    assert "StockRadar scans daily to highlight the strongest research opportunities" in free_page
    assert 'href="/upgrade"><span>Upgrade to Premium' in free_page
    assert 'href="/opportunities"><span>See today’s strongest' not in free_page


def test_premium_homepage_and_upgrade_page_link_directly_to_opportunities():
    dashboard_data = {
        "market_status": {
            "uk_status": "CLOSED", "uk_time": "00:00",
            "us_status": "CLOSED", "us_time": "00:00",
        }
    }
    client = app.app.test_client()
    with client.session_transaction() as current_session:
        current_session["owner_logged_in"] = True
    with (
        patch.object(app, "get_cached_dashboard_data", return_value=dashboard_data),
        patch.object(app, "get_stock_universe", return_value=[]),
    ):
        premium_home = client.get("/").get_data(as_text=True)
        premium_upgrade = client.get("/upgrade").get_data(as_text=True)

    assert 'href="/opportunities"><span>See today’s strongest StockRadar research opportunities</span>' in premium_home
    assert 'href="/opportunities">Open StockRadar Opportunities</a>' in premium_upgrade


def test_free_upgrade_page_explains_and_links_to_opportunity_preview():
    with patch.object(app, "premium_has_access", return_value=False):
        page = app.app.test_client().get("/upgrade").get_data(as_text=True)

    assert "Daily Opportunity Radar" in page
    assert "See today’s strongest StockRadar research opportunities" in page
    assert 'href="/opportunities">Preview Opportunities</a>' in page


def test_alert_panel_reads_fresh_account_scoped_preferences_and_latest_ten_events():
    state = {
        "preferences": {"owner": {"enabled": True, "tickers": ["MSFT"]}},
        "events": [
            {"account_key": "owner", "ticker": f"T{i}", "reasons": ["changed"],
             "snapshot_date": "2026-09-15"} for i in range(12)
        ] + [{"account_key": "other", "ticker": "PRIVATE", "reasons": ["secret"]}],
    }
    with (
        patch.object(app, "premium_has_access", return_value=True),
        patch.object(app, "owner_has_access", return_value=True),
        patch.object(app, "newsletter_storage_load", return_value=state) as load,
    ):
        client = app.app.test_client()
        response = client.get("/opportunities/alerts")
        page = response.get_data(as_text=True)
        state["preferences"]["owner"]["tickers"] = ["AAPL"]
        fresh = client.get("/opportunities/alerts").get_data(as_text=True)

    assert response.status_code == 200
    assert "no-store" in response.headers["Cache-Control"]
    assert 'name="enabled" checked' in page
    assert 'name="signal_changes" checked' in page
    assert 'value="MSFT"' in page
    assert 'value="AAPL"' in fresh
    assert "PRIVATE" not in page
    assert "<strong>T0</strong>" not in page
    assert "<strong>T1</strong>" not in page
    assert page.index("<strong>T11</strong>") < page.index("<strong>T2</strong>")
    assert 'name="csrf_token"' in page
    assert 'method="post" action="/opportunities/alerts"' in page
    assert "External email/push delivery is not enabled" in page
    assert load.call_count == 2
    load.assert_called_with("opportunity_alerts")


def test_alert_get_and_post_require_premium_and_account_identity():
    for premium, account_key, expected in [(False, "", 302), (True, "", 403)]:
        with (
            patch.object(app, "premium_has_access", return_value=premium),
            patch.object(app, "opportunity_account_key", return_value=account_key),
            patch.object(app, "newsletter_storage_load") as load,
            patch.object(app, "newsletter_storage_update") as update,
        ):
            client = app.app.test_client()
            for method in (client.get, client.post):
                response = method("/opportunities/alerts")
                assert response.status_code == expected
                if not premium:
                    assert response.headers["Location"].endswith("/upgrade")
            load.assert_not_called()
            update.assert_not_called()


def test_alert_panel_escapes_stored_content():
    with (
        patch.object(app, "premium_has_access", return_value=True),
        patch.object(app, "opportunity_account_key", return_value="owner"),
        patch.object(app, "newsletter_storage_load", return_value={
            "events": [{"account_key": "owner", "ticker": "<script>bad()</script>",
                        "reasons": ["<img src=x onerror=bad()>"], "snapshot_date": "today"}],
        }),
    ):
        page = app.app.test_client().get("/opportunities/alerts").get_data(as_text=True)
    assert "<script>bad()" not in page
    assert "&lt;script&gt;" in page
    assert "&lt;img" in page


def test_alert_save_still_requires_csrf():
    app.app.config["WTF_CSRF_ENABLED"] = True
    with (
        patch.object(app, "premium_has_access", return_value=True),
        patch.object(app, "opportunity_account_key", return_value="owner"),
        patch.object(app, "newsletter_storage_update") as update,
    ):
        response = app.app.test_client().post("/opportunities/alerts", data={"enabled": "on"})
    assert response.status_code == 400
    update.assert_not_called()


def outlook_info(**overrides):
    info = {
        "quoteType": "EQUITY", "currency": "USD",
        "regularMarketPrice": 100, "regularMarketTime": 1_789_646_400,
        "targetMeanPrice": 120, "targetHighPrice": 150, "targetLowPrice": 80,
        "numberOfAnalystOpinions": 12, "revenueGrowth": .12,
        "earningsGrowth": .08, "operatingMargins": .20, "trailingPE": 25,
        "earningsTimestamp": 1_800_000_000,
    }
    return dict(info, **overrides)


def test_return_outlook_uses_provider_targets_and_company_evidence():
    outlook = app.build_return_outlook(outlook_info(), now=1_789_646_400)
    assert outlook["metric"] == "+20.0%"
    assert outlook["cases"] == {
        "Base case": "USD 120.00 (+20.0%)",
        "Bull case": "USD 150.00 (+50.0%)",
        "Downside case": "USD 80.00 (-20.0%)",
    }
    assert len(outlook["drivers"]) == 3
    assert "12.0%" in outlook["drivers"][0]
    assert "25.0×" in outlook["valuation"]
    assert "Next reported earnings date" in outlook["catalyst"]
    negative = app.build_return_outlook(outlook_info(targetMeanPrice=90), now=1_789_646_400)
    assert negative["metric"] == "-10.0%"


def test_return_outlook_rejects_unreliable_or_ambiguous_data():
    for change in (
        {"regularMarketPrice": 0}, {"regularMarketPrice": float("nan")},
        {"regularMarketPrice": True}, {"regularMarketPrice": 1e-308},
        {"targetMeanPrice": float("inf")},
        {"targetHighPrice": None}, {"targetLowPrice": 130},
        {"numberOfAnalystOpinions": 1}, {"numberOfAnalystOpinions": 2.5},
        {"regularMarketTime": 1}, {"regularMarketTime": 1_789_646_401},
        {"currency": "GBp"}, {"currency": ""}, {"quoteType": "ETF"},
    ):
        result = app.build_return_outlook(outlook_info(**change), now=1_789_646_400)
        assert result["metric"] == "Unavailable", change
        assert result["cases"] == {}, change
    absent = app.build_return_outlook({})
    assert absent["drivers"] == []
    assert "Insufficient" in absent["risk"]
    assert "unavailable" in absent["valuation"]


def test_return_outlook_free_metric_and_server_side_premium_gate():
    outlook = app.build_return_outlook(outlook_info(), now=1_789_646_400)
    snapshot = app.build_opportunity_snapshot([recommendation("MSFT")], market_data_provider=no_market_data)
    snapshot["opportunities"][0]["return_outlook"] = outlook
    with (
        patch.object(app.time, "time", return_value=1_789_646_400),
        patch.object(app, "newsletter_storage_load", return_value={}),
        patch.object(app, "get_recommendations", return_value=[recommendation("MSFT")]),
        patch.object(app, "get_dividend_context", return_value={"return_outlook": outlook}),
        patch.object(app, "get_opportunity_page_snapshot", return_value=(snapshot, {"snapshots": {}})),
        patch.object(app, "premium_has_access", return_value=False),
    ):
        free = app.app.test_client().get("/opportunities").get_data(as_text=True)
        with patch.object(app, "premium_has_access", return_value=True):
            premium = app.app.test_client().get("/opportunities").get_data(as_text=True)
    assert "+20.0%" in free and "+20.0%" in premium
    assert "12 analysts" in free and "not a guaranteed return" in free
    assert "Revenue growth reported at 12.0%" not in free
    assert "USD 150.00" not in free
    assert "Revenue growth reported at 12.0%" in premium
    for heading in ("Base case", "Bull case", "Downside case", "What Has To Go Right?", "<summary>Valuation</summary>"):
        assert heading in premium
    assert snapshot["opportunities"][0]["return_outlook"] == outlook


def test_return_outlook_provider_failure_is_unavailable():
    with patch.object(app, "get_dividend_context", side_effect=RuntimeError("offline")):
        assert app.opportunity_return_outlook("MSFT")["metric"] == "Unavailable"


def test_return_outlook_reuses_existing_metadata_fetch_and_cache():
    from types import SimpleNamespace
    from unittest.mock import Mock

    provider = Mock(return_value=outlook_info(
        regularMarketTime=app.time.time(), dividendYield=.8,
        forwardAnnualDividendRate=1.0, financialCurrency="USD",
    ))
    with (
        patch.object(app, "DIVIDEND_CONTEXT_CACHE", {}),
        patch.object(app.yf, "Ticker", return_value=SimpleNamespace(get_info=provider)),
    ):
        first = app.opportunity_return_outlook("MSFT")
        second = app.opportunity_return_outlook("MSFT")
    assert first["metric"] == second["metric"] == "+20.0%"
    provider.assert_called_once_with()


def render_outlook_preview(outlook, market=None, premium=True):
    snapshot = app.build_opportunity_snapshot(
        [recommendation("COST")], market_data_provider=lambda _: market or no_market_data("COST"),
    )
    snapshot["opportunities"][0]["return_outlook"] = outlook
    with (
        patch.object(app.time, "time", return_value=1_789_646_400),
        patch.object(app, "newsletter_storage_load", return_value={}),
        patch.object(app, "get_recommendations", return_value=[recommendation("COST")]),
        patch.object(app, "get_opportunity_page_snapshot", return_value=(snapshot, {"snapshots": {}})),
        patch.object(app, "opportunity_return_outlook", return_value=outlook),
        patch.object(app, "premium_has_access", return_value=premium),
    ):
        return app.app.test_client().get("/opportunities").get_data(as_text=True)


def test_outlook_research_is_collapsed_and_scenario_percentages_lead():
    from html.parser import HTMLParser

    class DetailsParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.details = []
        def handle_starttag(self, tag, attrs):
            if tag == "details":
                self.details.append(dict(attrs))

    page = render_outlook_preview(app.build_return_outlook(outlook_info(), now=1_789_646_400))
    parser = DetailsParser()
    parser.feed(page)
    assert len(parser.details) == 6
    assert all("open" not in attrs for attrs in parser.details)
    for heading in ("Why it could rise", "What could go wrong", "What to watch", "Valuation", "Detailed research", "How is 12M Upside calculated?"):
        assert f"<summary>{heading}</summary>" in page
    assert '<strong class="scenario-value">+50.0%</strong><span class="scenario-target">USD 150.00</span>' in page
    assert page.index('What Has To Go Right?') < page.index('<summary>Why it could rise')
    assert page.index('<summary>Detailed research') < page.index('Daily score change:')
    assert page.index('<summary>Detailed research') < page.index('Rank movement:')
    assert page.index('Historical Opportunity Score') < page.index('<summary>How is 12M Upside calculated?')


def test_current_price_uses_validated_reference_only_when_chart_price_missing():
    outlook = app.build_return_outlook(outlook_info(), now=1_789_646_400)
    fallback = render_outlook_preview(outlook)
    assert 'Current price:</strong> USD 100.00' in fallback
    assert 'Yahoo Finance reference quote ·' in fallback
    chart = render_outlook_preview(outlook, {"current_price": 99, "current_price_label": "$99.00"})
    assert 'Current price:</strong> $99.00' in chart
    assert '(latest available chart close)' in chart
    assert 'Yahoo Finance reference quote ·' not in chart
    missing = render_outlook_preview(app.build_return_outlook({}))
    assert 'Current price:</strong> Unavailable' in missing
    assert 'No reliable target reference' not in missing
    assert 'class="scenario-grid"' not in missing
    assert 'class="upside-value">Unavailable' not in missing
    assert '<summary>Why it could rise</summary>' not in missing
    assert '12M Upside unavailable' not in missing
    assert 'Analyst outlook data is currently unavailable.' in missing
    assert 'nan%' not in missing


def test_daily_snapshot_enrichment_persists_and_survives_process_cache_restart():
    import copy

    now = 1_789_646_400
    stamp = datetime.fromtimestamp(now, timezone.utc)
    snapshot = app.build_opportunity_snapshot([recommendation("COST")], now=stamp, market_data_provider=no_market_data)
    state = {"snapshots": {snapshot["snapshot_date"]: snapshot}, "latest_snapshot_date": snapshot["snapshot_date"]}
    stored = copy.deepcopy(state)
    outlook = app.build_return_outlook(outlook_info(), now=now)
    def update(_store, updater):
        updater(stored)
        return True
    with (
        patch.object(app.time, "time", return_value=now),
        patch.object(app, "newsletter_london_now", return_value=stamp),
        patch.object(app, "newsletter_storage_load", side_effect=lambda _: copy.deepcopy(stored)),
        patch.object(app, "newsletter_storage_update", side_effect=update),
        patch.object(app, "opportunity_return_outlook", return_value=outlook) as provider,
    ):
        enriched, _ = app.get_opportunity_page_snapshot()
        assert enriched["opportunities"][0]["return_outlook"]["metric"] == "+20.0%"
        assert stored["snapshots"][snapshot["snapshot_date"]]["opportunities"][0]["return_outlook"]["retrieved_at"] == now
        app.OPPORTUNITY_PAGE_CACHE = None
        app.DIVIDEND_CONTEXT_CACHE.clear()
        restored, _ = app.get_opportunity_page_snapshot()
        assert restored["opportunities"][0]["return_outlook"] == outlook
        provider.assert_called_once_with("COST", refresh_date=snapshot["snapshot_date"])
        assert restored["opportunities"][0]["opportunity_score"] == snapshot["opportunities"][0]["opportunity_score"]


def test_missing_outlook_enrichment_retries_at_most_every_five_minutes_even_after_restart():
    import copy

    now = 1_789_646_400
    stamp = datetime.fromtimestamp(now, timezone.utc)
    snapshot = app.build_opportunity_snapshot([recommendation("COST")], now=stamp, market_data_provider=no_market_data)
    stored = {"snapshots": {snapshot["snapshot_date"]: snapshot}}
    def update(_store, updater):
        updater(stored)
        return True
    with (
        patch.object(app.time, "time", return_value=now) as clock,
        patch.object(app, "newsletter_london_now", return_value=stamp),
        patch.object(app, "newsletter_storage_load", side_effect=lambda _: copy.deepcopy(stored)),
        patch.object(app, "newsletter_storage_update", side_effect=update),
        patch.object(app, "opportunity_return_outlook", return_value=app.build_return_outlook({})) as provider,
    ):
        app.get_opportunity_page_snapshot()
        app.get_opportunity_page_snapshot()
        app.OPPORTUNITY_PAGE_CACHE = None
        app.get_opportunity_page_snapshot()
        provider.assert_called_once()
        clock.return_value = now + 301
        app.get_opportunity_page_snapshot()
        assert provider.call_count == 2


def test_persisted_prior_day_outlook_is_available_during_cooldown_but_not_after_age_limit():
    now = 1_789_646_400
    prior = app.build_opportunity_snapshot([recommendation("COST")], now=datetime.fromtimestamp(now, timezone.utc), market_data_provider=no_market_data)
    prior["opportunities"][0]["return_outlook"] = app.build_return_outlook(outlook_info(), now=now)
    current = app.build_opportunity_snapshot([recommendation("COST")], now=datetime.fromtimestamp(now + 86400, timezone.utc), market_data_provider=no_market_data)
    with patch.object(app.time, "time", return_value=now + 86400), patch.object(app, "YAHOO_COOLDOWN_UNTIL", now + 86400 + 300), patch.object(app, "opportunity_return_outlook") as provider:
        with patch.object(app, "newsletter_storage_update", return_value=True):
            app.enrich_opportunity_outlooks(current, {"snapshots": {prior["snapshot_date"]: prior}})
        saved = current["opportunities"][0]["return_outlook"]
        assert saved["metric"] == "+20.0%"
        assert saved["quote_timestamp"] == now
        provider.assert_not_called()
    assert app.eligible_return_outlook(saved, now=now + 7 * 86400 + 1)["metric"] == "Unavailable"


def test_unavailable_ui_is_compact_and_valid_cache_is_labelled_without_timestamp_changes():
    for premium in (False, True):
        absent = render_outlook_preview(app.build_return_outlook({}), premium=premium)
        assert '12M Upside unavailable' not in absent
        assert 'Not enough reliable analyst-target data' not in absent
        assert '<div class="upside-headline">' not in absent
        assert '<summary>How is 12M Upside calculated?</summary>' not in absent
        status = 'Analyst outlook data is currently unavailable.'
        if premium:
            assert absent.index('<summary>Detailed research</summary>') < absent.index(status)
        else:
            assert status not in absent
        assert 'class="upside-value">Unavailable' not in absent
        assert 'class="scenario-grid"' not in absent
        assert '<summary>What could go wrong</summary>' not in absent
        assert '<summary>What to watch</summary>' not in absent
        assert 'Insufficient evidence' not in absent
    outlook = app.build_return_outlook(outlook_info(), now=1_789_646_400)
    page = render_outlook_preview(outlook)
    assert f'Cached / delayed · reference quote {outlook["price_date"]}' in page
    assert 'class="scenario-grid"' in page
    assert 'What Has To Go Right?' in page
    assert '<summary>How is 12M Upside calculated?</summary>' in page


def test_free_preview_reuses_persisted_outlook_after_restart_without_provider_calls():
    now = 1_789_646_400
    outlook = app.build_return_outlook(outlook_info(), now=now)
    saved = {"snapshots": {"2026-09-17": {"opportunities": [{"ticker": "COST", "return_outlook": outlook}]}}}
    with (
        patch.object(app.time, "time", return_value=now),
        patch.object(app, "get_recommendations", return_value=[recommendation("COST")]),
        patch.object(app, "premium_has_access", return_value=False),
        patch.object(app, "newsletter_storage_load", return_value=saved),
        patch.object(app, "newsletter_storage_update") as update,
        patch.object(app, "opportunity_return_outlook") as provider,
    ):
        page = app.app.test_client().get("/opportunities").get_data(as_text=True)
    assert '+20.0%' in page and 'Cached / delayed' in page
    assert 'USD 150.00' not in page
    provider.assert_not_called()
    update.assert_not_called()


def test_new_daily_snapshot_attempts_fresh_outlook_before_using_prior_pair():
    now = 1_789_646_400
    old = app.build_return_outlook(outlook_info(), now=now)
    fresh = app.build_return_outlook(outlook_info(regularMarketTime=now+86400, targetMeanPrice=125), now=now+86400)
    current = app.build_opportunity_snapshot([recommendation("COST")], now=datetime.fromtimestamp(now+86400, timezone.utc), market_data_provider=no_market_data)
    state = {"snapshots": {"previous": {"opportunities": [{"ticker": "COST", "return_outlook": old}]}, current["snapshot_date"]: current}}
    with patch.object(app.time, "time", return_value=now+86400), patch.object(app, "opportunity_return_outlook", return_value=fresh) as provider, patch.object(app, "newsletter_storage_update", side_effect=lambda _, fn: fn(state)):
        app.enrich_opportunity_outlooks(current, state)
    provider.assert_called_once_with("COST", refresh_date=current["snapshot_date"])
    assert current["opportunities"][0]["return_outlook"]["metric"] == "+25.0%"


@pytest.fixture
def daily_outlook_state(monkeypatch):
    import copy
    now = 1_789_646_400
    stamp = datetime.fromtimestamp(now, timezone.utc)
    old = app.build_return_outlook(outlook_info(regularMarketTime=now-86400), now=now-86400)
    snapshot = app.build_opportunity_snapshot([recommendation("COST"), recommendation("MSFT")], now=stamp, market_data_provider=no_market_data)
    for row in snapshot["opportunities"]:
        row["return_outlook"] = copy.deepcopy(old)
    stored = {"snapshots": {snapshot["snapshot_date"]: copy.deepcopy(snapshot)}}
    clock = {"now": now}
    monkeypatch.setattr(app.time, "time", lambda: clock["now"])
    monkeypatch.setattr(app, "newsletter_london_now", lambda *args: datetime.fromtimestamp(clock["now"], timezone.utc))
    monkeypatch.setattr(app, "newsletter_storage_load", lambda _: copy.deepcopy(stored))
    def update(_store, updater):
        updater(stored)
        return True
    monkeypatch.setattr(app, "newsletter_storage_update", update)
    return now, snapshot, stored, clock


def test_failed_daily_refresh_preserves_pair_and_retries_only_incomplete_ticker(daily_outlook_state):
    now, snapshot, stored, clock = daily_outlook_state
    day = snapshot["snapshot_date"]
    old = snapshot["opportunities"][0]["return_outlook"]
    good = app.build_return_outlook(outlook_info(), now=now)
    def fetch(ticker, refresh_date):
        assert refresh_date == day
        return good if ticker == "MSFT" else old
    with patch.object(app, "opportunity_return_outlook", side_effect=fetch) as provider:
        first, _ = app.get_opportunity_page_snapshot()
        rows = {row["ticker"]: row for row in first["opportunities"]}
        assert rows["COST"]["return_outlook"]["retrieved_at"] == now-86400
        assert rows["COST"]["return_outlook"]["quote_timestamp"] == now-86400
        assert rows["COST"]["outlook_refresh_date"] != day
        assert rows["COST"]["outlook_retry_after"] == now+300
        assert rows["MSFT"]["outlook_refresh_date"] == day
        assert rows["MSFT"]["outlook_retry_after"] == 0
        clock["now"] = now+299
        app.get_opportunity_page_snapshot()
        app.OPPORTUNITY_PAGE_CACHE = None
        app.get_opportunity_page_snapshot()
        assert provider.call_count == 2
        clock["now"] = now+301
        with patch.object(app, "YAHOO_COOLDOWN_UNTIL", now+400):
            app.get_opportunity_page_snapshot()
        assert provider.call_count == 2
        clock["now"] = now+401
        provider.side_effect = None
        provider.return_value = app.build_return_outlook(outlook_info(regularMarketTime=now+401, targetMeanPrice=130), now=now+401)
        recovered, _ = app.get_opportunity_page_snapshot()
        assert provider.call_count == 3
        assert provider.call_args.kwargs == {"refresh_date": day}
        assert provider.call_args.args == ("COST",)
        persisted = {row["ticker"]: row for row in stored["snapshots"][day]["opportunities"]}
        assert persisted["COST"]["return_outlook"]["metric"] == "+30.0%"
        assert persisted["COST"]["outlook_refresh_date"] == day
        for _ in range(2):
            app.get_opportunity_page_snapshot()
        assert provider.call_count == 3
        assert any(row["return_outlook"]["metric"] == "+30.0%" for row in recovered["opportunities"])


def test_scheduler_refresh_invalidates_existing_page_cache(daily_outlook_state):
    import copy
    now, snapshot, stored, clock = daily_outlook_state
    day = snapshot["snapshot_date"]
    stale = copy.deepcopy(snapshot)
    app.OPPORTUNITY_PAGE_CACHE = (day, (stale, {"snapshots": {day: stale}}))
    current = copy.deepcopy(snapshot)
    fresh = app.build_return_outlook(outlook_info(targetMeanPrice=140), now=now)
    with patch.object(app, "opportunity_return_outlook", return_value=fresh) as provider:
        app.enrich_opportunity_outlooks(current, {"snapshots": {day: current}})
        assert app.OPPORTUNITY_PAGE_CACHE is None
        visible, _ = app.get_opportunity_page_snapshot()
        assert all(row["return_outlook"]["metric"] == "+40.0%" for row in visible["opportunities"])
        assert provider.call_count == 2


def test_yesterdays_outlook_carried_during_cooldown_still_refreshes_afterwards(daily_outlook_state):
    now, snapshot, stored, clock = daily_outlook_state
    with patch.object(app, "YAHOO_COOLDOWN_UNTIL", now+300), patch.object(app, "opportunity_return_outlook") as provider:
        visible, _ = app.get_opportunity_page_snapshot()
        assert all(row["return_outlook"]["cases"] for row in visible["opportunities"])
        assert all(row["outlook_refresh_date"] != snapshot["snapshot_date"] for row in visible["opportunities"])
        provider.assert_not_called()
    clock["now"] = now+301
    with patch.object(app, "opportunity_return_outlook", return_value=app.build_return_outlook(outlook_info(), now=now+301)) as provider:
        refreshed, _ = app.get_opportunity_page_snapshot()
        assert provider.call_count == 2
        assert all(row["outlook_refresh_date"] == snapshot["snapshot_date"] for row in refreshed["opportunities"])


def test_outlook_retrieval_date_uses_london_not_utc_calendar():
    before = datetime(2026, 7, 20, 22, 59, tzinfo=timezone.utc).timestamp()
    after = datetime(2026, 7, 20, 23, 1, tzinfo=timezone.utc).timestamp()
    assert app.outlook_retrieval_date({"retrieved_at": before}) == "2026-07-20"
    assert app.outlook_retrieval_date({"retrieved_at": after}) == "2026-07-21"


def test_success_marker_does_not_bypass_seven_day_quote_expiry(daily_outlook_state):
    now, snapshot, stored, clock = daily_outlook_state
    for row in stored["snapshots"][snapshot["snapshot_date"]]["opportunities"]:
        row["return_outlook"]["quote_timestamp"] = now-7*86400-1
        row["outlook_refresh_date"] = snapshot["snapshot_date"]
    with patch.object(app, "opportunity_return_outlook", return_value=app.build_return_outlook({})) as provider:
        result, _ = app.get_opportunity_page_snapshot()
        provider.assert_not_called()
    assert all(app.eligible_return_outlook(row["return_outlook"])["metric"] == "Unavailable" for row in result["opportunities"])
