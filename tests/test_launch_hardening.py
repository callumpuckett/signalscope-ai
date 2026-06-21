import app


def test_unknown_route_uses_custom_404_page():
    response = app.app.test_client().get("/this-page-does-not-exist")

    assert response.status_code == 404
    assert b"page could not be found" in response.data.lower()
    assert b'href="/universe"' in response.data
    assert b'href="/feedback"' in response.data
    assert b'href="/contact"' in response.data


def test_robots_txt_allows_crawling_and_contains_sitemap():
    response = app.app.test_client().get("/robots.txt")

    assert response.status_code == 200
    assert response.mimetype == "text/plain"
    assert b"User-agent: *" in response.data
    assert b"Allow: /" in response.data
    assert (
        b"Sitemap: https://stockradarhq.com/sitemap.xml"
        in response.data
    )


def test_sitemap_contains_key_public_routes():
    response = app.app.test_client().get("/sitemap.xml")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert response.mimetype == "application/xml"
    for path in (
        "/universe",
        "/upgrade",
        "/privacy",
        "/terms",
        "/refund-policy",
        "/risk-disclaimer",
        "/contact",
        "/manage-subscription",
        "/feedback",
    ):
        assert f"https://stockradarhq.com{path}" in page


def test_health_and_upgrade_remain_available():
    client = app.app.test_client()

    assert client.get("/health").status_code == 200
    assert client.get("/upgrade").status_code == 200


def test_security_headers_are_present_on_public_pages():
    response = app.app.test_client().get("/privacy")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert (
        response.headers["Permissions-Policy"]
        == "geolocation=(), microphone=(), camera=()"
    )
    assert "Content-Security-Policy" not in response.headers


def test_custom_500_page_uses_support_email(monkeypatch):
    monkeypatch.setattr(app, "SUPPORT_EMAIL", "support@example.test")

    with app.app.test_request_context("/"):
        response, status = app.internal_server_error(RuntimeError("test"))

    assert status == 500
    assert "Something went wrong" in response
    assert "support@example.test" in response
