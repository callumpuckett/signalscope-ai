from datetime import datetime
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import app


LONDON = ZoneInfo("Europe/London")


def configure_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "NEWSLETTER_ISSUES_PATH", str(tmp_path / "issues.json"))
    monkeypatch.setattr(app, "NEWSLETTER_STORY_HISTORY_PATH", str(tmp_path / "stories.json"))
    monkeypatch.setattr(app, "NEWSLETTER_MARKET_SNAPSHOTS_PATH", str(tmp_path / "snapshots.json"))
    monkeypatch.setattr(app, "NEWSLETTER_BEEHIIV_STATE_PATH", str(tmp_path / "beehiiv.json"))
    monkeypatch.setattr(app, "NEWSLETTER_SEND_LOCK_DIR", str(tmp_path / "locks"))
    app.WEEKLY_NEWSLETTER_ISSUE_CACHE.update({
        "issue_date": None,
        "issue_status": None,
        "generated_at": None,
        "issue": None,
    })


def article_at(timestamp, **updates):
    article = {
        "title": "Stock market rises after Bank of England rate decision",
        "url": "https://example.test/markets/story?utm_source=test",
        "source": "Example News",
        "publishedAt": timestamp,
        "id": "article-1",
    }
    article.update(updates)
    return article


def snapshot(cutoff, price, signal=None, signal_source="unavailable", available=True):
    return {
        "snapshot_id": f"snapshot-{cutoff}-{price}",
        "cutoff_utc": cutoff,
        "available_count": 1 if available else 0,
        "instruments": [{
            "ticker": "SPY",
            "name": "SPDR S&P 500 ETF",
            "sector": "ETF",
            "price": price if available else None,
            "availability": "live" if available else "unavailable",
            "source": "verified-test-provider",
            "signal": signal,
            "confidence": 70 if signal else None,
            "signal_source": signal_source,
        }],
    }


def recommendation(ticker, signal, confidence, reason=None):
    return {
        "ticker": ticker,
        "signal": signal,
        "confidence": confidence,
        "reason": reason or f"Current {signal} context for {ticker}.",
        "sector": "Technology",
    }


def test_weekly_window_normal_bst_week():
    window = app.newsletter_weekly_window(
        datetime(2026, 7, 24, 10, 0, tzinfo=LONDON)
    )
    assert window["window_start_local"] == "2026-07-17T09:00:00+01:00"
    assert window["window_end_local"] == "2026-07-24T09:00:00+01:00"
    assert window["window_start_utc"] == "2026-07-17T08:00:00+00:00"
    assert window["window_end_utc"] == "2026-07-24T08:00:00+00:00"
    assert window["iso_week"] == 30
    assert window["iso_year"] == 2026


def test_weekly_window_normal_gmt_week():
    window = app.newsletter_weekly_window(
        datetime(2026, 1, 9, 10, 0, tzinfo=LONDON)
    )
    assert window["window_start_local"].endswith("+00:00")
    assert window["window_end_local"].endswith("+00:00")
    assert window["window_start_utc"] == "2026-01-02T09:00:00+00:00"
    assert window["window_end_utc"] == "2026-01-09T09:00:00+00:00"


def test_weekly_window_handles_dst_transition():
    window = app.newsletter_weekly_window(
        datetime(2026, 4, 3, 10, 0, tzinfo=LONDON)
    )
    assert window["window_start_local"] == "2026-03-27T09:00:00+00:00"
    assert window["window_end_local"] == "2026-04-03T09:00:00+01:00"
    assert window["window_start_utc"] == "2026-03-27T09:00:00+00:00"
    assert window["window_end_utc"] == "2026-04-03T08:00:00+00:00"


def test_weekly_window_handles_year_and_iso_week_boundary():
    week_53 = app.newsletter_weekly_window(
        datetime(2021, 1, 1, 10, 0, tzinfo=LONDON)
    )
    week_1 = app.newsletter_weekly_window(
        datetime(2021, 1, 8, 10, 0, tzinfo=LONDON)
    )
    assert (week_53["iso_year"], week_53["iso_week"]) == (2020, 53)
    assert (week_1["iso_year"], week_1["iso_week"]) == (2021, 1)


def test_friday_before_cutoff_uses_previous_completed_week():
    window = app.newsletter_weekly_window(
        datetime(2026, 7, 24, 8, 59, tzinfo=LONDON)
    )
    assert window["issue_date"] == "2026-07-17"


def test_friday_after_cutoff_and_saturday_use_same_issue():
    friday = app.newsletter_weekly_window(
        datetime(2026, 7, 24, 9, 0, tzinfo=LONDON)
    )
    saturday = app.newsletter_weekly_window(
        datetime(2026, 7, 25, 12, 0, tzinfo=LONDON)
    )
    assert friday["issue_id"] == saturday["issue_id"]
    assert saturday["window_end_local"] == "2026-07-24T09:00:00+01:00"


def test_news_filter_includes_start_and_excludes_end():
    window = app.newsletter_weekly_window(
        datetime(2026, 7, 24, 10, 0, tzinfo=LONDON)
    )
    raw = [
        article_at(window["window_start_utc"], id="start"),
        article_at(window["window_end_utc"], id="end", url="https://example.test/end"),
    ]
    included, excluded = app.filter_weekly_news_articles(raw, "newsapi", window)
    assert [item["provider_article_id"] for item in included] == ["start"]
    assert excluded == 1


def test_news_filter_rejects_old_missing_timestamp_and_invalid_url():
    window = app.newsletter_weekly_window(
        datetime(2026, 7, 24, 10, 0, tzinfo=LONDON)
    )
    raw = [
        article_at("2026-07-10T08:00:00Z", id="old"),
        article_at("", id="missing", url="https://example.test/missing"),
        article_at(
            "2026-07-20T12:00:00Z",
            id="invalid-url",
            url="not-a-url",
        ),
    ]
    included, excluded = app.filter_weekly_news_articles(raw, "newsapi", window)
    assert included == []
    assert excluded == 3


def test_provider_date_filter_is_enforced_locally_when_provider_ignores_it():
    window = app.newsletter_weekly_window(
        datetime(2026, 7, 24, 10, 0, tzinfo=LONDON)
    )
    with (
        patch.object(app, "NEWSAPI_KEY", "configured"),
        patch.object(
            app,
            "fetch_newsapi_weekly_articles",
            return_value=[article_at("2026-06-01T12:00:00Z")],
        ),
        patch.object(app, "fetch_gdelt_weekly_articles", return_value=[]),
        patch.object(app, "load_newsletter_story_history", return_value={"stories": {}}),
    ):
        articles, status = app.fetch_weekly_news_articles(window)
    assert articles == []
    assert status["stale_stories_excluded"] == 1


def test_newsapi_and_gdelt_receive_exact_window_boundaries():
    window = app.newsletter_weekly_window(
        datetime(2026, 7, 24, 10, 0, tzinfo=LONDON)
    )
    requested_urls = []

    def fake_fetch(url, timeout=8):
        requested_urls.append(url)
        return {"status": "ok", "articles": []}

    with (
        patch.object(app, "NEWSAPI_KEY", "configured"),
        patch.object(app, "fetch_url_json", side_effect=fake_fetch),
    ):
        app.fetch_newsapi_weekly_articles(window)
        app.fetch_gdelt_weekly_articles(window)

    newsapi_query = parse_qs(urlsplit(requested_urls[0]).query)
    gdelt_query = parse_qs(urlsplit(requested_urls[1]).query)
    assert newsapi_query["from"] == [window["window_start_utc"]]
    assert newsapi_query["to"] == [window["window_end_utc"]]
    assert gdelt_query["startdatetime"] == ["20260717080000"]
    assert gdelt_query["enddatetime"] == ["20260724080000"]


def test_canonical_url_and_strong_story_identifiers_deduplicate():
    first = app.normalize_weekly_news_article(
        article_at("2026-07-20T12:00:00Z"),
        "newsapi",
    )
    second = app.normalize_weekly_news_article(
        article_at(
            "2026-07-20T13:00:00Z",
            id="article-2",
            url="https://example.test/markets/story?utm_campaign=copy",
        ),
        "gdelt",
    )
    assert first["canonical_url"] == "https://example.test/markets/story"
    assert app.newsletter_story_identifiers_match(first, second)


def test_provider_id_normalized_title_and_syndicated_title_deduplicate():
    base = app.normalize_weekly_news_article(
        article_at("2026-07-20T12:00:00Z"),
        "newsapi",
    )
    same_provider_id = dict(base, canonical_url="https://other.test/a")
    same_title = dict(
        base,
        provider_article_id="different",
        canonical_url="https://other.test/b",
    )
    syndicated = dict(
        same_title,
        normalized_title="stock market rises after bank of england rate decision today",
        normalized_title_hash="different",
    )
    assert app.newsletter_story_identifiers_match(base, same_provider_id)
    assert app.newsletter_story_identifiers_match(base, same_title)
    assert app.newsletter_story_identifiers_match(base, syndicated)


def test_meaningful_new_development_is_allowed():
    previous = app.normalize_weekly_news_article(
        article_at(
            "2026-07-18T08:00:00Z",
            title="Regulator considers bank merger decision",
            url="https://example.test/merger/considered",
        ),
        "newsapi",
    )
    current = app.normalize_weekly_news_article(
        article_at(
            "2026-07-19T12:00:00Z",
            title="Regulator approves bank merger after final decision",
            url="https://example.test/merger/approved",
            id="article-2",
        ),
        "newsapi",
    )
    assert app.newsletter_story_is_meaningful_update(current, previous)


def test_prior_edition_duplicate_is_excluded_but_meaningful_update_is_allowed():
    window = app.newsletter_weekly_window(
        datetime(2026, 7, 24, 10, 0, tzinfo=LONDON)
    )
    previous = app.normalize_weekly_news_article(
        article_at(
            "2026-07-18T08:00:00Z",
            title="Regulator considers bank merger decision",
            url="https://example.test/merger/considered",
        ),
        "newsapi",
    )
    duplicate_raw = article_at(
        "2026-07-19T08:00:00Z",
        title="Regulator considers bank merger decision",
        url="https://syndicated.test/merger/considered",
        id="syndicated",
    )
    update_raw = article_at(
        "2026-07-20T12:00:00Z",
        title="Regulator approves bank merger after final decision",
        url="https://example.test/merger/approved",
    )
    with (
        patch.object(app, "NEWSAPI_KEY", "configured"),
        patch.object(
            app,
            "fetch_newsapi_weekly_articles",
            return_value=[duplicate_raw, update_raw],
        ),
        patch.object(app, "fetch_gdelt_weekly_articles", return_value=[]),
        patch.object(
            app,
            "load_newsletter_story_history",
            return_value={"stories": {previous["story_fingerprint"]: previous}},
        ),
    ):
        articles, status = app.fetch_weekly_news_articles(window)
    assert [item["title"] for item in articles] == [
        "Regulator approves bank merger after final decision"
    ]
    assert status["duplicate_stories_excluded"] == 1


def test_market_comparison_calculates_change_and_signal_transition():
    previous = snapshot(
        "2026-07-17T08:00:00+00:00",
        100,
        signal="HOLD",
        signal_source="verified",
    )
    current = snapshot(
        "2026-07-24T08:00:00+00:00",
        110,
        signal="BUY",
        signal_source="verified",
    )
    result = app.compare_newsletter_market_snapshots(previous, current)
    assert result["strongest"][0]["weekly_change_percent"] == 10.0
    assert result["signal_changes"][0]["previous_signal"] == "HOLD"
    assert result["signal_changes"][0]["current_signal"] == "BUY"


def test_unavailable_and_deterministic_market_values_are_not_presented_as_changes():
    unavailable = app.compare_newsletter_market_snapshots(
        snapshot("2026-07-17T08:00:00+00:00", 100),
        snapshot("2026-07-24T08:00:00+00:00", None, available=False),
    )
    deterministic = app.compare_newsletter_market_snapshots(
        snapshot(
            "2026-07-17T08:00:00+00:00",
            100,
            signal="HOLD",
            signal_source="deterministic",
        ),
        snapshot(
            "2026-07-24T08:00:00+00:00",
            105,
            signal="BUY",
            signal_source="deterministic",
        ),
    )
    assert unavailable["comparisons"] == []
    assert deterministic["signal_changes"] == []


def test_current_signal_watchlist_ranks_buy_hold_and_sell_by_confidence():
    watchlist = app.build_newsletter_current_signal_watchlist([
        recommendation("BUY1", "BUY", "70%"),
        recommendation("BUY2", "BUY", "91%"),
        recommendation("HOLD1", "HOLD", "82%"),
        recommendation("HOLD2", "HOLD", "55%"),
        recommendation("SELL1", "SELL", "64%"),
        recommendation("SELL2", "SELL", "79%"),
        recommendation("BUY2", "BUY", "40%", reason="Lower duplicate."),
        recommendation("UNKNOWN", "MAYBE", "99%"),
    ])
    assert [item["ticker"] for item in watchlist] == [
        "BUY2",
        "HOLD1",
        "SELL2",
    ]
    assert [item["signal"] for item in watchlist] == [
        "BUY",
        "HOLD",
        "SELL",
    ]
    assert [item["confidence"] for item in watchlist] == [
        "91%",
        "82%",
        "79%",
    ]
    assert len({item["ticker"] for item in watchlist}) == len(watchlist)
    assert watchlist[0]["reason"] == "Current BUY context for BUY2."


def test_current_signal_watchlist_uses_weakest_hold_when_sell_is_absent():
    watchlist = app.build_newsletter_current_signal_watchlist([
        recommendation("BUY1", "BUY", "88%"),
        recommendation("HOLD1", "HOLD", "76%"),
        recommendation("HOLD2", "HOLD", "43%"),
        recommendation("HOLD3", "HOLD", "61%"),
    ])
    assert [item["ticker"] for item in watchlist] == [
        "BUY1",
        "HOLD1",
        "HOLD2",
    ]
    assert watchlist[-1]["signal"] == "HOLD"
    assert watchlist[-1]["watch_role"] == "caution"
    assert len({item["ticker"] for item in watchlist}) == 3


def test_current_signal_watchlist_uses_next_valid_signal_when_hold_is_absent():
    watchlist = app.build_newsletter_current_signal_watchlist([
        recommendation("BUY1", "BUY", "92%"),
        recommendation("BUY2", "BUY", "81%"),
        recommendation("SELL1", "SELL", "74%"),
        recommendation("SELL2", "SELL", "63%"),
    ])
    assert [item["ticker"] for item in watchlist] == [
        "BUY1",
        "SELL1",
        "BUY2",
    ]
    assert len({item["ticker"] for item in watchlist}) == 3


def test_signal_watchlist_has_reader_facing_message_when_current_data_is_absent():
    draft = app.build_free_weekly_newsletter(
        window=app.newsletter_weekly_window(
            datetime(2026, 7, 24, 10, 0, tzinfo=LONDON)
        ),
        current_recommendations=[],
    )
    with app.app.app_context():
        rendered = app.render_newsletter_issue_body(draft)
    assert (
        "Current StockRadar signals were unavailable when this issue was generated."
        in rendered
    )
    assert "No verified signal changes were available" not in rendered


def test_verified_signal_changes_remain_the_only_signal_watchlist_output():
    comparison = app.compare_newsletter_market_snapshots(
        snapshot(
            "2026-07-17T08:00:00+00:00",
            100,
            signal="HOLD",
            signal_source="verified",
        ),
        snapshot(
            "2026-07-24T08:00:00+00:00",
            105,
            signal="BUY",
            signal_source="verified",
        ),
    )
    draft = app.build_free_weekly_newsletter(
        window=app.newsletter_weekly_window(
            datetime(2026, 7, 24, 10, 0, tzinfo=LONDON)
        ),
        comparison=comparison,
        current_recommendations=[
            recommendation("BUY1", "BUY", "90%"),
            recommendation("HOLD1", "HOLD", "80%"),
            recommendation("SELL1", "SELL", "70%"),
        ],
    )
    with app.app.app_context():
        rendered = app.render_newsletter_issue_body(draft)
    assert draft["signal_watch"]["changes"]
    assert draft["signal_watch"]["current_signals"] == []
    assert "HOLD → BUY" in rendered
    assert "No tracked signals changed this week" not in rendered
    assert "BUY1" not in rendered


def test_finalized_issue_is_persisted_immutable_and_survives_restart(
    monkeypatch,
    tmp_path,
):
    configure_storage(monkeypatch, tmp_path)
    previous = snapshot("2026-07-17T08:00:00+00:00", 100)
    current = snapshot("2026-07-24T08:00:00+00:00", 105)
    normalized_article = app.normalize_weekly_news_article(
        article_at("2026-07-20T12:00:00Z"),
        "newsapi",
    )
    now = datetime(2026, 7, 24, 10, 0, tzinfo=LONDON)
    with (
        patch.object(
            app,
            "collect_newsletter_market_snapshot",
            side_effect=[previous, current],
        ),
        patch.object(
            app,
            "fetch_weekly_news_articles",
            return_value=([normalized_article], {
                "coverage_status": "verified",
                "provider_errors": [],
                "stale_stories_excluded": 0,
                "duplicate_stories_excluded": 0,
            }),
        ),
        patch.object(
            app,
            "get_recommendations",
            return_value=[
                recommendation("BUY1", "BUY", "90%"),
                recommendation("HOLD1", "HOLD", "80%"),
                recommendation("SELL1", "SELL", "70%"),
            ],
        ),
    ):
        first = app.build_weekly_newsletter_issue(now=now)

    assert first["metadata"]["issue_id"] == "stockradar-weekly-2026-W30"
    assert first["metadata"]["iso_week"] == 30
    assert first["metadata"]["status"] == "final"
    assert first["metadata"]["window_start_local"] == "2026-07-17T09:00:00+01:00"
    assert first["metadata"]["window_end_local"] == "2026-07-24T09:00:00+01:00"
    assert first["metadata"]["content_fingerprint"] == first["content_fingerprint"]
    assert first["metadata"]["title"] == (
        "StockRadar Weekly – Week 30: This Week’s Market Signals"
    )
    assert first["draft"]["opening_line"] == "Your Friday market brief is ready."
    assert first["draft"]["issue_status_message"] == "Latest issue"
    assert first["draft"]["market_pulse"] == (
        "1 tracked instruments rose and 0 fell between the two Friday cutoffs; "
        "1 had comparable verified prices."
    )
    assert normalized_article["title"] in first["draft"]["market_week_summary"]
    assert [
        item["ticker"]
        for item in first["draft"]["signal_watch"]["current_signals"]
    ] == ["BUY1", "HOLD1", "SELL1"]
    assert (
        "No tracked signals changed this week. Current signals to watch:"
        in first["draft"]["plain_text"]
    )

    app.WEEKLY_NEWSLETTER_ISSUE_CACHE["issue"] = None
    with (
        patch.object(
            app,
            "collect_newsletter_market_snapshot",
            side_effect=AssertionError("must not rebuild finalized issue"),
        ),
        patch.object(
            app,
            "fetch_weekly_news_articles",
            side_effect=AssertionError("must not refetch finalized issue"),
        ),
    ):
        restarted = app.build_weekly_newsletter_issue(now=now, force_refresh=True)
    assert restarted == first
    assert len(app.load_newsletter_issues()["issues"]) == 1
    assert app.latest_finalized_newsletter_issue() == first


def test_story_usage_is_persisted_only_for_finalized_issue(monkeypatch, tmp_path):
    configure_storage(monkeypatch, tmp_path)
    article = app.normalize_weekly_news_article(
        article_at("2026-07-20T12:00:00Z"),
        "newsapi",
    )
    issue = {
        "metadata": {
            "issue_id": "stockradar-weekly-2026-W30",
            "is_final": True,
            "status": "final",
        },
        "articles": [article],
    }
    assert app.record_newsletter_story_usage(issue)
    record = app.load_newsletter_story_history()["stories"][
        article["story_fingerprint"]
    ]
    assert record["issue_ids_used_in"] == ["stockradar-weekly-2026-W30"]


def test_generation_succeeds_when_beehiiv_is_unconfigured(monkeypatch, tmp_path):
    configure_storage(monkeypatch, tmp_path)
    monkeypatch.setattr(app, "BEEHIIV_API_KEY", "")
    monkeypatch.setattr(app, "BEEHIIV_PUBLICATION_ID", "")
    with patch.object(
        app,
        "build_weekly_newsletter_issue",
        return_value={
            "metadata": {
                "issue_id": "stockradar-weekly-2026-W30",
                "issue_key": "newsletter:2026-07-24",
                "issue_date": "2026-07-24",
                "is_final": True,
                "status": "final",
            },
            "draft": {},
        },
    ) as generate:
        result = app.run_due_newsletter_automation(
            now=datetime(2026, 7, 24, 10, 0, tzinfo=LONDON)
        )
    assert result["content_generation_status"] == "generated"
    assert result["generation_status"] == "finalized"
    assert result["status"] == "delivery_unavailable"
    generate.assert_called_once()


def test_health_reports_generation_and_delivery_separately(monkeypatch, tmp_path):
    configure_storage(monkeypatch, tmp_path)
    issue = {
        "metadata": {
            "issue_id": "stockradar-weekly-2026-W30",
            "issue_key": "newsletter:2026-07-24",
            "guid": "stockradar-weekly-2026-W30",
            "issue_date": "2026-07-24",
            "iso_week": 30,
            "iso_year": 2026,
            "status": "final",
            "is_final": True,
            "issue_status_key": "final",
            "window_start_utc": "2026-07-17T08:00:00+00:00",
            "window_end_utc": "2026-07-24T08:00:00+00:00",
            "generated_at": "2026-07-24T08:01:00+00:00",
            "story_count": 2,
            "market_snapshot_count": 8,
            "duplicate_stories_excluded": 3,
            "stale_stories_excluded": 4,
        }
    }
    with (
        patch.object(app, "get_finalized_newsletter_issue", return_value=issue),
        patch.object(app, "latest_finalized_newsletter_issue", return_value=issue),
        patch.object(
            app,
            "newsletter_london_now",
            return_value=datetime(2026, 7, 24, 10, 0, tzinfo=LONDON),
        ),
    ):
        newsletter = app.app.test_client().get("/health").get_json()["newsletter"]
    assert newsletter["newsletter_generation_status"] == "finalized"
    assert newsletter["current_issue_id"] == "stockradar-weekly-2026-W30"
    assert newsletter["current_issue_iso_week"] == 30
    assert newsletter["current_issue_story_count"] == 2
    assert newsletter["current_issue_market_snapshot_count"] == 8
    assert newsletter["duplicate_stories_excluded"] == 3
    assert newsletter["stale_stories_excluded"] == 4
    assert "beehiiv_delivery_status" in newsletter
    assert "next_expected_friday_cutoff" in newsletter


def test_blocked_beehiiv_keeps_manual_sender_and_never_calls_smtp(
    monkeypatch,
    tmp_path,
):
    configure_storage(monkeypatch, tmp_path)
    monkeypatch.setattr(app, "BEEHIIV_API_KEY", "secret")
    monkeypatch.setattr(app, "BEEHIIV_PUBLICATION_ID", "publication")
    monkeypatch.setattr(app, "BEEHIIV_CREATE_POST_BLOCKED", True)
    issue = {
        "metadata": {
            "issue_id": "stockradar-weekly-2026-W30",
            "issue_key": "newsletter:2026-07-24",
            "issue_date": "2026-07-24",
            "is_final": True,
            "status": "final",
        },
        "draft": {},
    }
    with (
        patch.object(app, "build_weekly_newsletter_issue", return_value=issue),
        patch.object(app, "send_newsletter_email") as smtp,
    ):
        result = app.run_due_newsletter_automation(
            now=datetime(2026, 7, 24, 10, 0, tzinfo=LONDON)
        )
    assert result["status"] == "beehiiv_api_post_blocked"
    assert result["content_generation_status"] == "generated"
    assert app.BEEHIIV_WEEKLY_BULK_SENDER == "beehiiv_manual"
    smtp.assert_not_called()
