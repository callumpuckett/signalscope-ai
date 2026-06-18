import pytest
from flask import Flask, session

import app


def test_production_cookie_has_secure_httponly_and_samesite_lax():
    test_app = Flask(__name__)
    app.configure_session_security(test_app, "s" * 32, production=True)

    @test_app.route("/set-session")
    def set_session():
        session["owner_logged_in"] = True
        return "ok"

    response = test_app.test_client().get("/set-session")
    cookie = response.headers["Set-Cookie"]

    assert "Secure" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


def test_local_cookie_does_not_require_https():
    test_app = Flask(__name__)
    app.configure_session_security(test_app, "", production=False)

    assert test_app.config["SESSION_COOKIE_SECURE"] is False
    assert test_app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert test_app.config["SESSION_COOKIE_SAMESITE"] == "Lax"


def test_production_requires_strong_secret():
    test_app = Flask(__name__)

    with pytest.raises(RuntimeError):
        app.configure_session_security(test_app, "too-short", production=True)

