from datetime import datetime, timezone
from unittest.mock import patch

import app
import newsletter_storage


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


def test_free_preview_is_locked_and_does_not_touch_opportunity_storage():
    with (
        patch.object(app, "premium_has_access", return_value=False),
        patch.object(app, "get_recommendations", return_value=[recommendation("MSFT")]),
        patch.object(app, "newsletter_storage_load") as storage_load,
    ):
        response = app.app.test_client().get("/opportunities")

    assert response.status_code == 200
    assert b"Premium preview" in response.data
    assert b"Get StockRadar Weekly free" in response.data
    storage_load.assert_not_called()


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
