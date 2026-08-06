import json
from urllib.parse import parse_qs
from unittest.mock import patch

import app


class JsonResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self, size=-1):
        return json.dumps(self.payload).encode("utf-8")


def configure_turnstile(monkeypatch):
    monkeypatch.setattr(app, "TURNSTILE_SITE_KEY", "test-site-key")
    monkeypatch.setattr(app, "TURNSTILE_SECRET_KEY", "test-secret-key")


def test_newsletter_form_renders_one_turnstile_script_and_widget(monkeypatch):
    configure_turnstile(monkeypatch)

    page = app.app.test_client().get("/newsletter").get_data(as_text=True)

    assert page.count("https://challenges.cloudflare.com/turnstile/v0/api.js") == 1
    assert page.count('class="cf-turnstile"') == 1
    assert 'data-sitekey="test-site-key"' in page
    assert "test-secret-key" not in page
    assert page.count('name="cf-turnstile-response"') == 0


def test_successful_turnstile_verification_allows_beehiiv_subscription(monkeypatch):
    configure_turnstile(monkeypatch)
    captured = {}

    def verify_request(request_object, timeout):
        captured["url"] = request_object.full_url
        captured["method"] = request_object.get_method()
        captured["payload"] = parse_qs(request_object.data.decode("utf-8"))
        captured["timeout"] = timeout
        return JsonResponse({"success": True})

    with (
        patch.object(app.urllib.request, "urlopen", side_effect=verify_request),
        patch.object(
            app,
            "create_beehiiv_subscription",
            return_value={"id": "sub_123"},
        ) as beehiiv,
    ):
        response = app.app.test_client().post(
            "/newsletter",
            data={
                "email": "reader@example.test",
                "cf-turnstile-response": "valid-turnstile-token",
            },
            headers={"CF-Connecting-IP": "203.0.113.25"},
            environ_base={"REMOTE_ADDR": "10.0.0.8"},
        )

    assert response.status_code == 200
    assert b"Subscribed successfully through Beehiiv" in response.data
    assert captured == {
        "url": app.TURNSTILE_VERIFY_URL,
        "method": "POST",
        "payload": {
            "secret": ["test-secret-key"],
            "response": ["valid-turnstile-token"],
            "remoteip": ["203.0.113.25"],
        },
        "timeout": app.TURNSTILE_REQUEST_TIMEOUT_SECONDS,
    }
    beehiiv.assert_called_once_with("reader@example.test")


def test_missing_turnstile_token_never_calls_cloudflare_or_beehiiv(monkeypatch):
    configure_turnstile(monkeypatch)

    with (
        patch.object(app.urllib.request, "urlopen") as cloudflare,
        patch.object(app, "create_beehiiv_subscription") as beehiiv,
    ):
        response = app.app.test_client().post(
            "/newsletter",
            data={"email": "reader@example.test"},
        )

    assert b"Please complete the security check" in response.data
    cloudflare.assert_not_called()
    beehiiv.assert_not_called()


def test_failed_cloudflare_verification_never_calls_beehiiv(monkeypatch):
    configure_turnstile(monkeypatch)

    with (
        patch.object(
            app.urllib.request,
            "urlopen",
            return_value=JsonResponse(
                {"success": False, "error-codes": ["timeout-or-duplicate"]}
            ),
        ) as cloudflare,
        patch.object(app, "create_beehiiv_subscription") as beehiiv,
    ):
        response = app.app.test_client().post(
            "/newsletter",
            data={
                "email": "reader@example.test",
                "cf-turnstile-response": "expired-token",
            },
        )

    assert b"The security check could not be confirmed" in response.data
    cloudflare.assert_called_once()
    beehiiv.assert_not_called()


def test_cloudflare_timeout_never_calls_beehiiv(monkeypatch):
    configure_turnstile(monkeypatch)

    with (
        patch.object(app.urllib.request, "urlopen", side_effect=TimeoutError),
        patch.object(app, "create_beehiiv_subscription") as beehiiv,
    ):
        response = app.app.test_client().post(
            "/newsletter",
            data={
                "email": "reader@example.test",
                "cf-turnstile-response": "unverified-token",
            },
        )

    assert b"The security check is temporarily unavailable" in response.data
    beehiiv.assert_not_called()


def test_missing_turnstile_configuration_fails_closed(monkeypatch):
    monkeypatch.setattr(app, "TURNSTILE_SITE_KEY", "")
    monkeypatch.setattr(app, "TURNSTILE_SECRET_KEY", "configured-secret-key")

    with (
        patch.object(app.urllib.request, "urlopen") as cloudflare,
        patch.object(app, "create_beehiiv_subscription") as beehiiv,
    ):
        response = app.app.test_client().post(
            "/newsletter",
            data={
                "email": "reader@example.test",
                "cf-turnstile-response": "unverified-token",
            },
        )

    assert b"Newsletter signup protection is temporarily unavailable" in response.data
    cloudflare.assert_not_called()
    beehiiv.assert_not_called()
