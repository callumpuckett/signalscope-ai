import json
import os
from datetime import datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pytest

import app
from newsletter_storage import PostgresNewsletterStorage


LONDON = ZoneInfo("Europe/London")


def published_issue(now, marker):
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
        "market_pulse": marker,
    })
    return {
        "draft": draft,
        "metadata": metadata,
        "summary": marker,
        "articles": [],
        "subject": app.BEEHIIV_EXPORT_SUBJECT,
        "preview_text": app.BEEHIIV_EXPORT_PREVIEW,
    }


def postgres_authoritative_storage():
    storage = Mock()
    storage.finalize_issue_once.side_effect = lambda issue: {
        "stored": True,
        "conflict": False,
        "issue": issue,
    }
    return storage


def persist(monkeypatch, issue):
    storage = postgres_authoritative_storage()
    monkeypatch.setattr(app, "NEWSLETTER_STORAGE", storage)
    result = app.persist_finalized_newsletter_issue(issue)
    storage.finalize_issue_once.assert_called_once_with(issue)
    return result


def test_successful_publication_writes_immutable_html_json_and_pointer(
    monkeypatch,
):
    issue = published_issue(
        datetime(2026, 8, 21, 9, 5, tzinfo=LONDON),
        "W34 artifact marker",
    )

    persist(monkeypatch, issue)

    paths = app.newsletter_published_artifact_paths(
        "stockradar-weekly-2026-W34"
    )
    html = open(paths["html"], encoding="utf-8").read()
    artifact = json.loads(open(paths["json"], encoding="utf-8").read())
    manifest = json.loads(open(paths["latest"], encoding="utf-8").read())
    assert "W34 artifact marker" in html
    assert artifact["issue_id"] == "stockradar-weekly-2026-W34"
    assert artifact["issue"]["metadata"]["iso_week"] == 34
    assert artifact["rss_xml"].startswith("<?xml")
    assert manifest["issue_id"] == "stockradar-weekly-2026-W34"
    assert manifest["html_file"].endswith("W34.html")
    assert manifest["json_file"].endswith("W34.json")


def test_postgresql_unavailable_after_publication_does_not_affect_readers_or_rss(
    monkeypatch,
):
    issue = published_issue(
        datetime(2026, 8, 21, 9, 5, tzinfo=LONDON),
        "Database-independent W34",
    )
    persist(monkeypatch, issue)

    def database_access_forbidden(*args, **kwargs):
        raise AssertionError("public newsletter delivery queried PostgreSQL")

    with (
        patch.object(app, "newsletter_storage_load", database_access_forbidden),
        patch.object(app, "get_finalized_newsletter_issue", database_access_forbidden),
        patch.object(app, "load_or_generate_latest_newsletter_issue", database_access_forbidden),
        patch.object(app, "newsletter_persistence_status", database_access_forbidden),
    ):
        client = app.app.test_client()
        latest = client.get("/newsletter/latest")
        rss = client.get("/newsletter/rss")

    assert latest.status_code == 200
    assert "Database-independent W34" in latest.get_data(as_text=True)
    assert "Week 34" in latest.get_data(as_text=True)
    assert rss.status_code == 200
    assert rss.content_type.startswith("application/rss+xml")
    assert "Database-independent W34" in rss.get_data(as_text=True)
    assert "stockradar-weekly-2026-W34" in rss.get_data(as_text=True)


def test_new_publication_atomically_advances_latest_without_mutating_old_artifact(
    monkeypatch,
):
    week_33 = published_issue(
        datetime(2026, 8, 14, 9, 5, tzinfo=LONDON),
        "Original W33 artifact",
    )
    week_34 = published_issue(
        datetime(2026, 8, 21, 9, 5, tzinfo=LONDON),
        "Replacement W34 artifact",
    )
    storage = postgres_authoritative_storage()
    monkeypatch.setattr(app, "NEWSLETTER_STORAGE", storage)

    app.persist_finalized_newsletter_issue(week_33)
    week_33_paths = app.newsletter_published_artifact_paths(
        "stockradar-weekly-2026-W33"
    )
    original_html = open(week_33_paths["html"], "rb").read()
    original_json = open(week_33_paths["json"], "rb").read()

    app.persist_finalized_newsletter_issue(week_34)

    assert open(week_33_paths["html"], "rb").read() == original_html
    assert open(week_33_paths["json"], "rb").read() == original_json
    published = app.load_latest_published_newsletter_artifact()
    assert published["manifest"]["issue_id"] == "stockradar-weekly-2026-W34"
    assert "Replacement W34 artifact" in published["html"]
    assert "Original W33 artifact" not in published["html"]


def test_older_republication_cannot_move_latest_pointer_backwards(monkeypatch):
    week_33 = published_issue(
        datetime(2026, 8, 14, 9, 5, tzinfo=LONDON),
        "W33",
    )
    week_34 = published_issue(
        datetime(2026, 8, 21, 9, 5, tzinfo=LONDON),
        "W34",
    )
    storage = postgres_authoritative_storage()
    monkeypatch.setattr(app, "NEWSLETTER_STORAGE", storage)

    app.persist_finalized_newsletter_issue(week_34)
    result = app.publish_newsletter_artifact(week_33)

    assert result["latest_updated"] is False
    assert (
        app.load_latest_published_newsletter_artifact()["manifest"]["issue_id"]
        == "stockradar-weekly-2026-W34"
    )


def test_failed_pointer_update_keeps_previous_published_issue_live(monkeypatch):
    week_33 = published_issue(
        datetime(2026, 8, 14, 9, 5, tzinfo=LONDON),
        "Stable W33",
    )
    week_34 = published_issue(
        datetime(2026, 8, 21, 9, 5, tzinfo=LONDON),
        "Uncommitted W34",
    )
    storage = postgres_authoritative_storage()
    monkeypatch.setattr(app, "NEWSLETTER_STORAGE", storage)
    app.persist_finalized_newsletter_issue(week_33)

    with patch.object(
        app.NEWSLETTER_PUBLISHED_ARTIFACT_STORE,
        "_write_pointer",
        side_effect=OSError("simulated pointer failure"),
    ):
        with pytest.raises(app.NewsletterPublishedArtifactError) as caught:
            app.persist_finalized_newsletter_issue(week_34)

    assert str(caught.value) == "artifact_publish_failed"
    published = app.load_latest_published_newsletter_artifact()
    assert published["manifest"]["issue_id"] == "stockradar-weekly-2026-W33"
    assert "Stable W33" in published["html"]
    assert "Uncommitted W34" not in published["html"]


def test_corrupt_artifact_returns_controlled_503_without_database_fallback(
    monkeypatch,
):
    issue = published_issue(
        datetime(2026, 8, 21, 9, 5, tzinfo=LONDON),
        "W34",
    )
    persist(monkeypatch, issue)
    paths = app.newsletter_published_artifact_paths(
        "stockradar-weekly-2026-W34"
    )
    with open(paths["html"], "w", encoding="utf-8") as handle:
        handle.write("corrupt")

    with patch.object(
        app,
        "newsletter_storage_load",
        side_effect=AssertionError("must not fall back to PostgreSQL"),
    ):
        client = app.app.test_client()
        latest = client.get("/newsletter/latest")
        rss = client.get("/newsletter/rss")

    assert latest.status_code == 503
    assert rss.status_code == 503
    assert app.NEWSLETTER_PUBLISHED_ARTIFACT_LAST_ERROR == "artifact_checksum_mismatch"


def test_validation_failure_still_prevents_database_and_artifact_publication(
    monkeypatch,
):
    issue = published_issue(
        datetime(2026, 8, 21, 9, 5, tzinfo=LONDON),
        "invalid W34",
    )
    issue["metadata"]["iso_week"] = 33
    storage = postgres_authoritative_storage()
    monkeypatch.setattr(app, "NEWSLETTER_STORAGE", storage)

    with pytest.raises(app.NewsletterIssueValidationError):
        app.persist_finalized_newsletter_issue(issue)

    storage.finalize_issue_once.assert_not_called()
    assert not os.path.exists(app.newsletter_published_artifact_paths()["latest"])


def test_production_artifact_publication_rejects_non_postgresql_authority(
    monkeypatch,
):
    issue = published_issue(
        datetime(2026, 8, 21, 9, 5, tzinfo=LONDON),
        "W34",
    )
    monkeypatch.setattr(app, "IS_PRODUCTION", True)
    monkeypatch.setattr(app, "NEWSLETTER_STORAGE", Mock())

    with pytest.raises(app.NewsletterPublishedArtifactError) as caught:
        app.publish_newsletter_artifact(issue)

    assert str(caught.value) == "artifact_authority_not_postgresql"
    assert not os.path.exists(app.newsletter_published_artifact_paths()["latest"])


def test_production_artifact_publication_requires_reachable_postgresql_record(
    monkeypatch,
):
    issue = published_issue(
        datetime(2026, 8, 21, 9, 5, tzinfo=LONDON),
        "W34",
    )
    def unavailable(*args, **kwargs):
        raise OSError("database unavailable")

    backend = PostgresNewsletterStorage(
        "postgresql://configured",
        connector=unavailable,
    )
    monkeypatch.setattr(app, "IS_PRODUCTION", True)
    monkeypatch.setattr(app, "NEWSLETTER_STORAGE", backend)

    with pytest.raises(app.NewsletterPublishedArtifactError) as caught:
        app.publish_newsletter_artifact(issue)

    assert str(caught.value) == "artifact_postgresql_authority_unavailable"
    assert not os.path.exists(app.newsletter_published_artifact_paths()["latest"])


def test_startup_catch_up_backfills_artifact_from_authoritative_latest_issue():
    issue = published_issue(
        datetime(2026, 8, 21, 9, 5, tzinfo=LONDON),
        "W34",
    )

    with (
        patch.object(app, "newsletter_auto_send_due", return_value=False),
        patch.object(app, "latest_finalized_newsletter_issue", return_value=issue),
        patch.object(app, "publish_newsletter_artifact") as publish,
    ):
        app.newsletter_startup_catch_up_once()

    publish.assert_called_once_with(issue)
