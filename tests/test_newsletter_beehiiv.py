from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import app


LONDON = ZoneInfo("Europe/London")


def issue_for(date_string="2026-07-10"):
    return {
        "metadata": {
            "issue_date": date_string,
            "issue_id": "stockradar-weekly-2026-W28",
            "issue_key": f"newsletter:{date_string}",
            "guid": "stockradar-weekly-2026-W28",
            "title": "StockRadar Weekly – Week 28: This Week’s Market Signals",
            "issue_status_key": "final",
            "status": "final",
            "is_final": True,
            "iso_week": 28,
            "iso_year": 2026,
            "window_start_utc": "2026-07-03T08:00:00+00:00",
            "window_end_utc": "2026-07-10T08:00:00+00:00",
            "generated_at": "2026-07-10T08:00:00+00:00",
        },
        "summary": "Weekly market brief.",
        "draft": {
            "plain_text": "Weekly market brief.",
            "market_week_summary": "Markets were mixed as investors reviewed the latest signals.",
            "market_pulse": "Constructive and caution signals remain balanced.",
            "what_looked_strong": [{"name": "Example strength", "reason": "Momentum improved."}],
            "what_looked_weak": [{"name": "Example risk", "reason": "Momentum weakened."}],
        },
    }


def configure_state(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "NEWSLETTER_BEEHIIV_STATE_PATH", str(tmp_path / "beehiiv.json"))
    monkeypatch.setattr(app, "NEWSLETTER_ISSUES_PATH", str(tmp_path / "issues.json"))
    monkeypatch.setattr(app, "NEWSLETTER_STORY_HISTORY_PATH", str(tmp_path / "stories.json"))
    monkeypatch.setattr(app, "NEWSLETTER_MARKET_SNAPSHOTS_PATH", str(tmp_path / "snapshots.json"))
    monkeypatch.setattr(app, "NEWSLETTER_SEND_LOCK_DIR", str(tmp_path / "locks"))


def test_friday_before_0900_is_not_due_when_previous_issue_exists(monkeypatch):
    with patch.object(app, "get_finalized_newsletter_issue", return_value=issue_for()):
        assert not app.newsletter_auto_send_due(datetime(2026, 7, 10, 8, 59, tzinfo=LONDON))


def test_friday_at_0900_is_due(monkeypatch):
    with patch.object(app, "get_finalized_newsletter_issue", return_value=None):
        assert app.newsletter_auto_send_due(datetime(2026, 7, 10, 9, 0, tzinfo=LONDON))


def test_non_friday_catches_up_when_latest_issue_is_missing(monkeypatch):
    with patch.object(app, "get_finalized_newsletter_issue", return_value=None):
        assert app.newsletter_auto_send_due(datetime(2026, 7, 9, 12, 0, tzinfo=LONDON))


def test_missing_beehiiv_configuration_does_not_block_generation(monkeypatch, tmp_path):
    configure_state(monkeypatch, tmp_path)
    monkeypatch.setattr(app, "BEEHIIV_API_KEY", "")
    monkeypatch.setattr(app, "BEEHIIV_PUBLICATION_ID", "")
    with (
        patch.object(app, "build_weekly_newsletter_issue", return_value=issue_for()) as generate,
        patch.object(app, "beehiiv_api_request") as api_call,
    ):
        result = app.run_due_newsletter_automation(now=datetime(2026, 7, 10, 9, 0, tzinfo=LONDON))
    assert result["status"] == "delivery_unavailable"
    assert result["content_generation_status"] == "generated"
    assert result["failure_reason"] == "beehiiv_not_configured"
    generate.assert_called_once()
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
    monkeypatch.setattr(app, "BEEHIIV_CREATE_POST_BLOCKED", False)
    responses = [{"data": []}, {"data": {"id": "post_123", "preview_url": "https://example.test/preview"}}]
    with patch.object(app, "beehiiv_api_request", side_effect=responses):
        result = app.create_beehiiv_issue(issue_for())
    assert result["status"] == "beehiiv_draft_created"
    assert result["beehiiv_post_id"] == "post_123"


def test_existing_beehiiv_issue_skips_duplicate(monkeypatch, tmp_path):
    configure_state(monkeypatch, tmp_path)
    monkeypatch.setattr(app, "BEEHIIV_API_KEY", "secret")
    monkeypatch.setattr(app, "BEEHIIV_PUBLICATION_ID", "pub_123")
    monkeypatch.setattr(app, "BEEHIIV_CREATE_POST_BLOCKED", False)
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
    monkeypatch.setattr(app, "BEEHIIV_CREATE_POST_BLOCKED", False)
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
    with (
        patch.object(
            app,
            "load_latest_published_newsletter_artifact",
            return_value={
                "html": "<h1>Published newsletter</h1>",
                "rss_xml": "<?xml version=\"1.0\"?><rss></rss>",
            },
        ),
        patch.object(app, "load_or_generate_latest_newsletter_issue") as generate,
    ):
        client = app.app.test_client()
        assert client.get("/newsletter/latest").status_code == 200
        assert client.get("/newsletter/rss").status_code == 200
    generate.assert_not_called()


def test_newsletter_subscribe_uses_beehiiv_not_local_storage(monkeypatch):
    with (
        patch.object(
            app,
            "verify_turnstile_token",
            return_value={"success": True, "reason": ""},
        ),
        patch.object(app, "create_beehiiv_subscription", return_value={"id": "sub_123"}) as subscribe,
        patch.object(app, "upsert_newsletter_subscriber") as local_subscribe,
    ):
        response = app.app.test_client().post(
            "/newsletter",
            data={
                "email": "reader@example.test",
                "cf-turnstile-response": "verified-token",
            },
        )
    assert response.status_code == 200
    subscribe.assert_called_once_with("reader@example.test")
    local_subscribe.assert_not_called()


def test_subscription_api_error_does_not_store_locally(monkeypatch):
    with (
        patch.object(
            app,
            "verify_turnstile_token",
            return_value={"success": True, "reason": ""},
        ),
        patch.object(app, "create_beehiiv_subscription", side_effect=RuntimeError("beehiiv_http_403")),
        patch.object(app, "upsert_newsletter_subscriber") as local_subscribe,
    ):
        response = app.app.test_client().post(
            "/newsletter",
            data={
                "email": "reader@example.test",
                "cf-turnstile-response": "verified-token",
            },
        )
    assert response.status_code == 200
    assert b"temporarily unavailable" in response.data
    local_subscribe.assert_not_called()


def test_beehiiv_post_403_records_manual_block_without_smtp(monkeypatch, tmp_path):
    configure_state(monkeypatch, tmp_path)
    monkeypatch.setattr(app, "BEEHIIV_API_KEY", "secret")
    monkeypatch.setattr(app, "BEEHIIV_PUBLICATION_ID", "pub_123")
    monkeypatch.setattr(app, "BEEHIIV_CREATE_POST_BLOCKED", False)
    responses = [{"data": []}, RuntimeError("beehiiv_http_403")]
    with (
        patch.object(app, "beehiiv_api_request", side_effect=responses),
        patch.object(app, "send_newsletter_email") as smtp_send,
    ):
        result = app.create_beehiiv_issue(issue_for())
    assert result["status"] == "beehiiv_api_post_blocked"
    assert result["content_generation_status"] == "generated"
    assert result["failure_reason"] == "beehiiv_http_403"
    smtp_send.assert_not_called()


def test_manual_scheduler_does_not_call_post_api_or_smtp(monkeypatch, tmp_path):
    configure_state(monkeypatch, tmp_path)
    monkeypatch.setattr(app, "BEEHIIV_API_KEY", "secret")
    monkeypatch.setattr(app, "BEEHIIV_PUBLICATION_ID", "pub_123")
    monkeypatch.setattr(app, "BEEHIIV_CREATE_POST_BLOCKED", True)
    with (
        patch.object(app, "build_weekly_newsletter_issue", return_value=issue_for()),
        patch.object(app, "beehiiv_api_request") as api_call,
        patch.object(app, "send_newsletter_email") as smtp_send,
    ):
        result = app.run_due_newsletter_automation(
            now=datetime(2026, 7, 10, 9, 0, tzinfo=LONDON)
        )
    assert result["status"] == "beehiiv_api_post_blocked"
    assert result["content_generation_status"] == "generated"
    api_call.assert_not_called()
    smtp_send.assert_not_called()


def test_health_uses_manual_beehiiv_status_without_sent(monkeypatch, tmp_path):
    configure_state(monkeypatch, tmp_path)
    monkeypatch.setattr(app, "BEEHIIV_API_KEY", "secret")
    monkeypatch.setattr(app, "BEEHIIV_PUBLICATION_ID", "pub_123")
    monkeypatch.setattr(app, "BEEHIIV_CREATE_POST_BLOCKED", True)
    monkeypatch.setattr(app, "INTERNAL_DIAGNOSTICS_SECRET", "test-internal")
    app.record_beehiiv_issue_state(
        "newsletter:2026-07-10",
        status="beehiiv_api_post_blocked",
        content_generation_status="generated",
        failure_reason="beehiiv_http_403",
    )
    with (
        patch.object(
            app,
            "newsletter_london_now",
            return_value=datetime(2026, 7, 10, 9, 0, tzinfo=LONDON),
        ),
        patch.object(app, "get_finalized_newsletter_issue", return_value=issue_for()),
        patch.object(app, "latest_finalized_newsletter_issue", return_value=issue_for()),
    ):
        newsletter = app.app.test_client().get(
            "/health",
            headers={"X-StockRadar-Internal-Secret": "test-internal"},
        ).get_json()["newsletter"]
    assert newsletter["weekly_bulk_sender"] == "beehiiv_manual"
    assert newsletter["beehiiv_configured"] is True
    assert newsletter["beehiiv_create_post_blocked"] is True
    assert newsletter["beehiiv_campaign_status"] == "beehiiv_api_post_blocked"
    assert newsletter["content_generation_status"] == "finalized"
    assert "sent" not in str(newsletter).lower()


def test_beehiiv_copy_route_requires_owner_login():
    response = app.app.test_client().get("/admin/newsletter/beehiiv-copy")
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login")


def test_beehiiv_copy_route_contains_export_fields(monkeypatch, tmp_path):
    configure_state(monkeypatch, tmp_path)
    with patch.object(app, "load_or_generate_latest_newsletter_issue", return_value=issue_for()):
        client = app.app.test_client()
        with client.session_transaction() as current_session:
            current_session["owner_logged_in"] = True
        response = client.get("/admin/newsletter/beehiiv-copy")
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "StockRadar Weekly: This week’s market signals" in page
    assert "Your 5-minute plain-English market brief is ready." in page
    assert "Copy this into Beehiiv and send from Beehiiv." in page
    assert "https://www.stockradarhq.com/newsletter/latest" in page
    assert "educational market information" in page
    assert "StockRadar Team" in page
