import copy
import logging
from datetime import datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pytest

import app


LONDON = ZoneInfo("Europe/London")


def published_issue(now):
    window = app.newsletter_weekly_window(now)
    metadata = app.newsletter_issue_metadata(now, generated_at=now)
    metadata.update({
        "finalized_at": now.isoformat(),
        "generation_status": "finalized",
    })
    draft = app.build_free_weekly_newsletter(window=window)
    draft.update({
        "issue_date_label": metadata["issue_date_label"],
        "issue_status": metadata["issue_status"],
        "issue_status_message": metadata["issue_status_message"],
        "is_final": True,
        "last_refreshed": metadata["generated_at_label"],
        "preview_refresh_note": "",
    })
    return {
        "draft": draft,
        "metadata": metadata,
        "summary": draft["market_pulse"],
        "articles": [],
        "subject": app.BEEHIIV_EXPORT_SUBJECT,
        "preview_text": app.BEEHIIV_EXPORT_PREVIEW,
    }


def issue_state(*issues):
    return {
        "issues": {
            issue["metadata"]["issue_id"]: issue
            for issue in issues
        },
        "latest_issue_id": issues[0]["metadata"]["issue_id"] if issues else "",
    }


def persist_published_issue(monkeypatch, issue):
    storage = Mock()
    storage.finalize_issue_once.return_value = {
        "stored": True,
        "conflict": False,
        "issue": issue,
    }
    monkeypatch.setattr(app, "NEWSLETTER_STORAGE", storage)
    return app.persist_finalized_newsletter_issue(issue)


def test_friday_21_august_window_is_week_34():
    window = app.newsletter_weekly_window(
        datetime(2026, 8, 21, 10, 0, tzinfo=LONDON)
    )
    assert window["issue_id"] == "stockradar-weekly-2026-W34"
    assert window["issue_key"] == "newsletter:2026-08-21"
    assert window["window_start_utc"] == "2026-08-14T08:00:00+00:00"
    assert window["window_end_utc"] == "2026-08-21T08:00:00+00:00"


def test_latest_route_does_not_generate_at_request_time_when_current_issue_missing(
    monkeypatch,
):
    previous = published_issue(
        datetime(2026, 8, 14, 9, 5, tzinfo=LONDON)
    )
    app.publish_newsletter_artifact(previous)
    monkeypatch.setitem(app.WEEKLY_NEWSLETTER_ISSUE_CACHE, "issue", None)
    with (
        patch.object(
            app,
            "newsletter_storage_load",
            side_effect=AssertionError("public route must not query persistence"),
        ),
        patch.object(
            app,
            "build_weekly_newsletter_issue",
            side_effect=RuntimeError("newsletter_persistence_degraded"),
        ) as generate,
        patch.object(
            app,
            "get_recommendations",
            side_effect=AssertionError("display must use precomputed data"),
        ),
    ):
        response = app.app.test_client().get("/newsletter/latest")

    assert response.status_code == 200
    assert "Week 33" in response.get_data(as_text=True)
    generate.assert_not_called()


def test_latest_route_skips_malformed_newest_issue_and_logs_exact_rejection(
    monkeypatch,
    caplog,
):
    current = published_issue(
        datetime(2026, 8, 21, 9, 5, tzinfo=LONDON)
    )
    previous = published_issue(
        datetime(2026, 8, 14, 9, 5, tzinfo=LONDON)
    )
    malformed = copy.deepcopy(current)
    malformed["draft"] = None
    app.publish_newsletter_artifact(previous)
    monkeypatch.setitem(app.WEEKLY_NEWSLETTER_ISSUE_CACHE, "issue", None)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(app.NewsletterIssueValidationError):
            persist_published_issue(monkeypatch, malformed)
        response = app.app.test_client().get("/newsletter/latest")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Week 33" in page
    assert "Week 34" not in page
    assert "event=newsletter_publish_validation_failed" in caplog.text
    assert "stockradar-weekly-2026-W34" in caplog.text
    assert "draft_not_object" in caplog.text


def test_latest_route_returns_observable_503_when_no_valid_issue_exists(
    monkeypatch,
    caplog,
):
    malformed = published_issue(
        datetime(2026, 8, 21, 9, 5, tzinfo=LONDON)
    )
    malformed["metadata"]["iso_week"] = 33
    monkeypatch.setitem(app.WEEKLY_NEWSLETTER_ISSUE_CACHE, "issue", None)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(app.NewsletterIssueValidationError):
            persist_published_issue(monkeypatch, malformed)
        response = app.app.test_client().get("/newsletter/latest")

    assert response.status_code == 503
    assert response.status_code != 500
    assert "temporarily unavailable" in response.get_data(as_text=True)
    assert "metadata_iso_week_mismatch" in caplog.text


def test_publish_validation_rejects_bad_week_and_link_before_storage():
    issue = published_issue(
        datetime(2026, 8, 21, 9, 5, tzinfo=LONDON)
    )
    issue["metadata"]["iso_week"] = 33
    issue["draft"]["trending_vs_forecasting"]["trending"][0]["url"] = (
        "javascript:alert(1)"
    )
    storage = Mock()

    with patch.object(app, "NEWSLETTER_STORAGE", storage):
        with pytest.raises(app.NewsletterIssueValidationError) as caught:
            app.persist_finalized_newsletter_issue(issue)

    assert "metadata_iso_week_mismatch" in caught.value.errors
    assert "draft_trending_0_url_invalid" in caught.value.errors
    storage.finalize_issue_once.assert_not_called()


def test_health_reports_latest_route_readiness(monkeypatch):
    issue = published_issue(
        datetime(2026, 8, 21, 9, 5, tzinfo=LONDON)
    )
    app.publish_newsletter_artifact(issue)
    monkeypatch.setitem(app.WEEKLY_NEWSLETTER_ISSUE_CACHE, "issue", None)
    monkeypatch.setattr(app, "INTERNAL_DIAGNOSTICS_SECRET", "internal-secret")
    with patch.object(
        app,
        "newsletter_storage_load",
        return_value=issue_state(issue),
    ):
        newsletter = app.app.test_client().get(
            "/health",
            headers={"X-StockRadar-Internal-Secret": "internal-secret"},
        ).get_json()["newsletter"]

    assert newsletter["latest_route_status"] == "ready"
    assert newsletter["latest_route_http_status"] == 200
    assert newsletter["latest_route_issue_id"] == "stockradar-weekly-2026-W34"
    assert newsletter["published_artifact_status"] == "ready"
    assert newsletter["published_artifact_issue_id"] == "stockradar-weekly-2026-W34"
    assert newsletter["published_artifact_last_error"] == ""
