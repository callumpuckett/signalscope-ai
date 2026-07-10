from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import app


LONDON = ZoneInfo("Europe/London")


def issue_for(date_string="2026-07-10"):
    return {
        "metadata": {
            "issue_date": date_string,
            "guid": f"stockradar-weekly-{date_string}",
            "title": "StockRadar Weekly — Friday 10 July 2026",
        },
        "summary": "Weekly market brief.",
        "draft": {"plain_text": "Weekly market brief."},
    }


def configure_state(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "NEWSLETTER_BEEHIIV_STATE_PATH", str(tmp_path / "beehiiv.json"))
    monkeypatch.setattr(app, "NEWSLETTER_SEND_LOCK_DIR", str(tmp_path / "locks"))


def test_friday_before_0900_is_not_due():
    assert not app.newsletter_auto_send_due(datetime(2026, 7, 10, 8, 59, tzinfo=LONDON))


def test_friday_at_0900_is_due():
    assert app.newsletter_auto_send_due(datetime(2026, 7, 10, 9, 0, tzinfo=LONDON))


def test_non_friday_is_not_due():
    assert not app.newsletter_auto_send_due(datetime(2026, 7, 9, 12, 0, tzinfo=LONDON))


def test_missing_beehiiv_configuration_fails_closed(monkeypatch, tmp_path):
    configure_state(monkeypatch, tmp_path)
    monkeypatch.setattr(app, "BEEHIIV_API_KEY", "")
    monkeypatch.setattr(app, "BEEHIIV_PUBLICATION_ID", "")
    with patch.object(app, "beehiiv_api_request") as api_call:
        result = app.run_due_newsletter_automation(now=datetime(2026, 7, 10, 9, 0, tzinfo=LONDON))
    assert result["status"] == "failed"
    assert result["reason"] == "beehiiv_not_configured"
    api_call.assert_not_called()


def test_generation_failure_never_calls_beehiiv(monkeypatch, tmp_path):
    configure_state(monkeypatch, tmp_path)
    monkeypatch.setattr(app, "BEEHIIV_API_KEY", "secret")
    monkeypatch.setattr(app, "BEEHIIV_PUBLICATION_ID", "pub_123")
    with (
        patch.object(app, "build_weekly_newsletter_issue", side_effect=RuntimeError("generation failed")),
        patch.object(app, "beehiiv_api_request") as api_call,
    ):
        result = app.run_due_newsletter_automation(now=datetime(2026, 7, 10, 9, 0, tzinfo=LONDON))
    assert result["status"] == "failed"
    assert result["content_generation_status"] == "failed"
    api_call.assert_not_called()


def test_successful_draft_records_post_id(monkeypatch, tmp_path):
    configure_state(monkeypatch, tmp_path)
    monkeypatch.setattr(app, "BEEHIIV_API_KEY", "secret")
    monkeypatch.setattr(app, "BEEHIIV_PUBLICATION_ID", "pub_123")
    monkeypatch.setattr(app, "BEEHIIV_AUTOSEND_ENABLED", False)
    responses = [{"data": []}, {"data": {"id": "post_123", "preview_url": "https://example.test/preview"}}]
    with patch.object(app, "beehiiv_api_request", side_effect=responses):
        result = app.create_beehiiv_issue(issue_for())
    assert result["status"] == "beehiiv_draft_created"
    assert result["beehiiv_post_id"] == "post_123"


def test_existing_beehiiv_issue_skips_duplicate(monkeypatch, tmp_path):
    configure_state(monkeypatch, tmp_path)
    monkeypatch.setattr(app, "BEEHIIV_API_KEY", "secret")
    monkeypatch.setattr(app, "BEEHIIV_PUBLICATION_ID", "pub_123")
    existing = {"id": "post_existing", "status": "confirmed", "title": issue_for()["metadata"]["title"]}
    with patch.object(app, "beehiiv_api_request", return_value={"data": [existing]}) as api_call:
        result = app.create_beehiiv_issue(issue_for())
    assert result["duplicate"] is True
    assert result["status"] == "beehiiv_published"
    assert api_call.call_count == 1


def test_local_state_survives_restart_and_skips_api(monkeypatch, tmp_path):
    configure_state(monkeypatch, tmp_path)
    app.record_beehiiv_issue_state(
        "newsletter:2026-07-10",
        status="beehiiv_draft_created",
        beehiiv_post_id="post_saved",
    )
    with patch.object(app, "beehiiv_api_request") as api_call:
        result = app.create_beehiiv_issue(issue_for())
    assert result["duplicate"] is True
    assert result["beehiiv_post_id"] == "post_saved"
    api_call.assert_not_called()


def test_beehiiv_error_is_sanitised(monkeypatch, tmp_path):
    configure_state(monkeypatch, tmp_path)
    monkeypatch.setattr(app, "BEEHIIV_API_KEY", "top-secret")
    monkeypatch.setattr(app, "BEEHIIV_PUBLICATION_ID", "pub_123")
    with (
        patch.object(app, "build_weekly_newsletter_issue", return_value=issue_for()),
        patch.object(app, "create_beehiiv_issue", side_effect=RuntimeError("top-secret user@example.com")),
    ):
        result = app.run_due_newsletter_automation(now=datetime(2026, 7, 10, 9, 0, tzinfo=LONDON))
    assert result["status"] == "failed"
    assert "top-secret" not in result["failure_reason"]
    assert "user@example.com" not in result["failure_reason"]


def test_transactional_smtp_function_is_unchanged(monkeypatch):
    monkeypatch.setattr(app, "NEWSLETTER_EMAIL_ENABLED", True)
    monkeypatch.setattr(app, "NEWSLETTER_SMTP_HOST", "smtp.example.test")
    monkeypatch.setattr(app, "NEWSLETTER_FROM_EMAIL", "sender@example.test")
    with (
        patch.object(app.smtplib, "SMTP") as smtp,
        patch.object(app, "build_newsletter_email_html", return_value="<p>Brief</p>"),
    ):
        result = app.send_newsletter_email("reader@example.test", issue_for())
    assert result["sent"] is True
    smtp.return_value.__enter__.return_value.send_message.assert_called_once()


def test_newsletter_public_routes_remain_available(monkeypatch):
    issue = issue_for() | {"summary": "Brief"}
    issue["metadata"].update({
        "published_at": datetime(2026, 7, 10, 9, 0, tzinfo=LONDON),
        "rss_status_label": "Final issue",
    })
    with (
        patch.object(app, "build_weekly_newsletter_issue", return_value=issue),
        patch.object(app, "render_template_string", return_value="ok"),
        patch.object(app, "render_newsletter_issue_body", return_value="<p>Brief</p>"),
    ):
        client = app.app.test_client()
        assert client.get("/newsletter/latest").status_code == 200
        assert client.get("/newsletter/rss").status_code == 200


def test_newsletter_subscribe_uses_beehiiv_not_local_storage(monkeypatch):
    with (
        patch.object(app, "create_beehiiv_subscription", return_value={"id": "sub_123"}) as subscribe,
        patch.object(app, "upsert_newsletter_subscriber") as local_subscribe,
    ):
        response = app.app.test_client().post(
            "/newsletter",
            data={"email": "reader@example.test"},
        )
    assert response.status_code == 200
    subscribe.assert_called_once_with("reader@example.test")
    local_subscribe.assert_not_called()


def test_subscription_api_error_does_not_store_locally(monkeypatch):
    with (
        patch.object(app, "create_beehiiv_subscription", side_effect=RuntimeError("beehiiv_http_403")),
        patch.object(app, "upsert_newsletter_subscriber") as local_subscribe,
    ):
        response = app.app.test_client().post(
            "/newsletter",
            data={"email": "reader@example.test"},
        )
    assert response.status_code == 200
    assert b"temporarily unavailable" in response.data
    local_subscribe.assert_not_called()
