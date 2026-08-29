from datetime import datetime
import hashlib
import io
import json
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pytest

import app
from newsletter_artifact_store import (
    PublishedArtifactStoreConfigurationError,
    R2PublishedArtifactStore,
    build_published_artifact_store,
)


LONDON = ZoneInfo("Europe/London")


class FakeR2Error(Exception):
    def __init__(self, code, status):
        super().__init__(code)
        self.response = {
            "Error": {"Code": code},
            "ResponseMetadata": {"HTTPStatusCode": status},
        }


class FakeR2Client:
    def __init__(self):
        self.objects = {}
        self.etags = {}
        self.available = True
        self.fail_put_keys = set()
        self.replace_before_conditional_put = {}

    @staticmethod
    def etag(payload):
        return f'"{hashlib.sha256(payload).hexdigest()}"'

    def set_object(self, key, payload):
        self.objects[key] = payload
        self.etags[key] = self.etag(payload)

    def get_object(self, *, Bucket, Key):
        if not self.available:
            raise FakeR2Error("ServiceUnavailable", 503)
        if Key not in self.objects:
            raise FakeR2Error("NoSuchKey", 404)
        payload = self.objects[Key]
        return {
            "Body": io.BytesIO(payload),
            "ContentLength": len(payload),
            "ETag": self.etags[Key],
        }

    def put_object(
        self,
        *,
        Bucket,
        Key,
        Body,
        ContentType,
        CacheControl,
        custom_headers,
    ):
        if not self.available or Key in self.fail_put_keys:
            raise FakeR2Error("ServiceUnavailable", 503)
        if custom_headers.get("If-None-Match") == "*" and Key in self.objects:
            raise FakeR2Error("PreconditionFailed", 412)
        expected = custom_headers.get("If-Match")
        replacement = self.replace_before_conditional_put.pop(Key, None)
        if expected is not None and replacement is not None:
            self.set_object(Key, replacement)
        if expected is not None and self.etags.get(Key) != expected:
            raise FakeR2Error("PreconditionFailed", 412)
        payload = bytes(Body)
        self.set_object(Key, payload)
        return {"ETag": self.etags[Key]}


def r2_store(client):
    return R2PublishedArtifactStore(
        client,
        "stockradar-newsletters",
        prefix="published/newsletters",
        max_bytes=app.NEWSLETTER_PUBLISHED_ARTIFACT_MAX_BYTES,
    )


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


def use_r2_and_postgres_authority(monkeypatch, client):
    store = r2_store(client)
    storage = Mock()
    storage.finalize_issue_once.side_effect = lambda issue: {
        "stored": True,
        "conflict": False,
        "issue": issue,
    }
    monkeypatch.setattr(app, "NEWSLETTER_PUBLISHED_ARTIFACT_STORE", store)
    monkeypatch.setattr(app, "NEWSLETTER_STORAGE", storage)
    return store


def test_production_fails_closed_without_explicit_valid_r2_configuration():
    common = {
        "production": True,
        "filesystem_root": "/tmp/should-not-be-used",
        "max_bytes": 1024,
    }
    with pytest.raises(PublishedArtifactStoreConfigurationError) as missing:
        build_published_artifact_store(backend="", **common)
    assert str(missing.value) == "artifact_backend_not_configured"

    with pytest.raises(PublishedArtifactStoreConfigurationError) as local:
        build_published_artifact_store(backend="filesystem", **common)
    assert str(local.value) == "artifact_filesystem_forbidden_in_production"

    with pytest.raises(PublishedArtifactStoreConfigurationError) as credentials:
        build_published_artifact_store(
            backend="r2",
            account_id="a" * 32,
            bucket="stockradar-newsletters",
            **common,
        )
    assert str(credentials.value) == "artifact_r2_credentials_missing"


def test_r2_unavailable_serves_warm_last_known_good_but_cold_start_fails_safe(
    monkeypatch,
):
    client = FakeR2Client()
    use_r2_and_postgres_authority(monkeypatch, client)
    issue = published_issue(
        datetime(2026, 8, 21, 9, 5, tzinfo=LONDON),
        "Warm R2 W34",
    )
    app.persist_finalized_newsletter_issue(issue)

    web = app.app.test_client()
    assert web.get("/newsletter/latest").status_code == 200
    client.available = False

    latest = web.get("/newsletter/latest")
    rss = web.get("/newsletter/rss")
    assert latest.status_code == 200
    assert "Warm R2 W34" in latest.get_data(as_text=True)
    assert rss.status_code == 200
    assert "Warm R2 W34" in rss.get_data(as_text=True)
    assert app.NEWSLETTER_PUBLISHED_ARTIFACT_LAST_ERROR == (
        "artifact_store_unavailable"
    )

    monkeypatch.setattr(
        app,
        "NEWSLETTER_PUBLISHED_ARTIFACT_LAST_KNOWN_GOOD",
        None,
    )
    assert web.get("/newsletter/latest").status_code == 503
    assert web.get("/newsletter/rss").status_code == 503


def test_r2_corrupt_and_malformed_objects_are_rejected_without_postgres_fallback(
    monkeypatch,
):
    client = FakeR2Client()
    store = use_r2_and_postgres_authority(monkeypatch, client)
    issue = published_issue(
        datetime(2026, 8, 21, 9, 5, tzinfo=LONDON),
        "Corruption W34",
    )
    app.persist_finalized_newsletter_issue(issue)
    issue_id = issue["metadata"]["issue_id"]
    html_key = store.issue_keys(issue_id)["html"]
    json_key = store.issue_keys(issue_id)["json"]
    original_html = client.objects[html_key]
    client.set_object(html_key, b"corrupt")

    with patch.object(
        app,
        "newsletter_storage_load",
        side_effect=AssertionError("public route queried PostgreSQL"),
    ):
        web = app.app.test_client()
        assert web.get("/newsletter/latest").status_code == 503
        assert web.get("/newsletter/rss").status_code == 503
    assert app.NEWSLETTER_PUBLISHED_ARTIFACT_LAST_ERROR == (
        "artifact_checksum_mismatch"
    )

    client.set_object(html_key, original_html)
    malformed_json = b"{malformed"
    client.set_object(json_key, malformed_json)
    pointer = json.loads(client.objects[store.latest_key].decode("utf-8"))
    pointer["json_sha256"] = hashlib.sha256(malformed_json).hexdigest()
    client.set_object(
        store.latest_key,
        json.dumps(pointer, sort_keys=True).encode("utf-8"),
    )
    assert web.get("/newsletter/latest").status_code == 503
    assert app.NEWSLETTER_PUBLISHED_ARTIFACT_LAST_ERROR == (
        "artifact_json_invalid"
    )

    client.set_object(store.latest_key, b"{malformed")
    assert web.get("/newsletter/latest").status_code == 503
    assert app.NEWSLETTER_PUBLISHED_ARTIFACT_LAST_ERROR == (
        "artifact_pointer_invalid"
    )


def test_r2_pointer_update_failure_preserves_previous_last_known_good(
    monkeypatch,
):
    client = FakeR2Client()
    store = use_r2_and_postgres_authority(monkeypatch, client)
    week_33 = published_issue(
        datetime(2026, 8, 14, 9, 5, tzinfo=LONDON),
        "Stable R2 W33",
    )
    week_34 = published_issue(
        datetime(2026, 8, 21, 9, 5, tzinfo=LONDON),
        "Uncommitted R2 W34",
    )
    app.persist_finalized_newsletter_issue(week_33)
    store_key = store.latest_key
    previous_pointer = client.objects[store_key]
    client.fail_put_keys.add(store_key)

    with pytest.raises(app.NewsletterPublishedArtifactError) as caught:
        app.persist_finalized_newsletter_issue(week_34)
    assert str(caught.value) == "artifact_store_unavailable"
    assert client.objects[store_key] == previous_pointer

    client.fail_put_keys.clear()
    published = app.load_latest_published_newsletter_artifact()
    assert published["manifest"]["issue_id"].endswith("W33")
    assert "Stable R2 W33" in published["html"]
    assert "Uncommitted R2 W34" not in published["html"]


def test_r2_public_routes_survive_postgres_failure_and_application_restart(
    monkeypatch,
):
    client = FakeR2Client()
    original_store = use_r2_and_postgres_authority(monkeypatch, client)
    issue = published_issue(
        datetime(2026, 8, 21, 9, 5, tzinfo=LONDON),
        "Restart-safe R2 W34",
    )
    app.persist_finalized_newsletter_issue(issue)

    restarted_store = r2_store(client)
    assert restarted_store is not original_store
    monkeypatch.setattr(
        app,
        "NEWSLETTER_PUBLISHED_ARTIFACT_STORE",
        restarted_store,
    )
    monkeypatch.setattr(
        app,
        "NEWSLETTER_PUBLISHED_ARTIFACT_LAST_KNOWN_GOOD",
        None,
    )

    def database_access_forbidden(*args, **kwargs):
        raise AssertionError("public newsletter delivery queried PostgreSQL")

    with (
        patch.object(app, "newsletter_storage_load", database_access_forbidden),
        patch.object(app, "get_finalized_newsletter_issue", database_access_forbidden),
        patch.object(app, "load_or_generate_latest_newsletter_issue", database_access_forbidden),
        patch.object(app, "newsletter_persistence_status", database_access_forbidden),
        patch.object(app, "build_newsletter_rss_xml", database_access_forbidden),
        patch.object(app, "render_newsletter_issue_body", database_access_forbidden),
    ):
        web = app.app.test_client()
        latest = web.get("/newsletter/latest")
        rss = web.get("/newsletter/rss")

    assert latest.status_code == 200
    assert "Restart-safe R2 W34" in latest.get_data(as_text=True)
    assert rss.status_code == 200
    assert "Restart-safe R2 W34" in rss.get_data(as_text=True)


def test_r2_latest_pointer_advances_and_cannot_roll_back(monkeypatch):
    client = FakeR2Client()
    use_r2_and_postgres_authority(monkeypatch, client)
    week_33 = published_issue(
        datetime(2026, 8, 14, 9, 5, tzinfo=LONDON),
        "R2 W33",
    )
    week_34 = published_issue(
        datetime(2026, 8, 21, 9, 5, tzinfo=LONDON),
        "R2 W34",
    )
    app.persist_finalized_newsletter_issue(week_33)
    app.persist_finalized_newsletter_issue(week_34)
    rollback = app.publish_newsletter_artifact(week_33)

    assert rollback["latest_updated"] is False
    published = app.load_latest_published_newsletter_artifact()
    assert published["manifest"]["issue_id"].endswith("W34")
    assert "R2 W34" in published["html"]


def test_r2_issue_objects_are_immutable(monkeypatch):
    client = FakeR2Client()
    use_r2_and_postgres_authority(monkeypatch, client)
    publication_time = datetime(2026, 8, 21, 9, 5, tzinfo=LONDON)
    original = published_issue(publication_time, "Original immutable W34")
    conflicting = published_issue(publication_time, "Conflicting W34")
    app.persist_finalized_newsletter_issue(original)

    with pytest.raises(app.NewsletterPublishedArtifactError) as caught:
        app.publish_newsletter_artifact(conflicting)

    assert str(caught.value) == "artifact_immutable_conflict"
    published = app.load_latest_published_newsletter_artifact()
    assert "Original immutable W34" in published["html"]
    assert "Conflicting W34" not in published["html"]


def test_r2_compare_and_swap_prevents_concurrent_pointer_rollback(monkeypatch):
    client = FakeR2Client()
    store = use_r2_and_postgres_authority(monkeypatch, client)
    week_33 = published_issue(
        datetime(2026, 8, 14, 9, 5, tzinfo=LONDON),
        "R2 W33",
    )
    week_34 = published_issue(
        datetime(2026, 8, 21, 9, 5, tzinfo=LONDON),
        "R2 W34",
    )
    app.persist_finalized_newsletter_issue(week_33)

    concurrent_pointer = json.loads(
        client.objects[store.latest_key].decode("utf-8")
    )
    concurrent_pointer.update({
        "issue_id": "stockradar-weekly-2026-W35",
        "sort_key": "2026-08-28T08:00:00+00:00",
        "html_file": "issues/stockradar-weekly-2026-W35.html",
        "json_file": "issues/stockradar-weekly-2026-W35.json",
    })
    concurrent_payload = json.dumps(
        concurrent_pointer,
        sort_keys=True,
    ).encode("utf-8")
    client.replace_before_conditional_put[store.latest_key] = concurrent_payload

    result = app.publish_newsletter_artifact(week_34)

    assert result["latest_updated"] is False
    stored_pointer = json.loads(
        client.objects[store.latest_key].decode("utf-8")
    )
    assert stored_pointer["issue_id"] == "stockradar-weekly-2026-W35"
