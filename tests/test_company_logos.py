from pathlib import Path
from unittest.mock import patch

import pandas as pd
from bs4 import BeautifulSoup

import app


ROOT_DIR = Path(__file__).resolve().parents[1]


def reset_logo_caches():
    app.STOCK_DISPLAY_LOOKUP_CACHE.update({"rows": None, "lookup": {}})
    app.STOCK_IDENTITY_LOOKUP_CACHE.update({"rows": None, "lookup": {}})
    app.COMPANY_LOGO_METADATA_CACHE.clear()


def test_stock_identity_renders_accessible_responsive_logo_and_company_name():
    reset_logo_caches()

    markup = str(app.stock_identity("AAPL", size="detail", lazy=False))
    identity = BeautifulSoup(markup, "html.parser").select_one(
        "[data-company-identity='AAPL']"
    )
    image = identity.select_one("img.company-logo-image")

    assert "company-identity--detail" in identity["class"]
    assert image["alt"] == "Apple Inc. logo"
    assert image["loading"] == "eager"
    assert image["fetchpriority"] == "high"
    assert image["decoding"] == "async"
    assert identity.select_one(".company-identity-name").get_text(strip=True) == (
        "Apple Inc. (AAPL)"
    )
    assert identity.select_one(".company-logo-fallback")["aria-hidden"] == "true"


def test_existing_logo_and_domain_metadata_are_reused_with_domain_fallback():
    rows = [
        {
            "ticker": "ACME",
            "name": "Acme Industries",
            "logo_url": "https://assets.example.com/acme.svg",
            "domain": "https://www.acme.example/about",
        }
    ]

    with patch.object(app, "get_stock_universe", return_value=rows):
        reset_logo_caches()
        metadata = app.company_logo_metadata("ACME")

    assert metadata["logo_url"] == "https://assets.example.com/acme.svg"
    assert metadata["fallback_logo_url"] == (
        "https://www.google.com/s2/favicons?domain=www.acme.example&sz=128"
    )
    assert metadata["initials"] == "AI"


def test_missing_or_unsafe_logo_metadata_falls_back_without_broken_ui():
    rows = [
        {
            "ticker": "ACME",
            "name": "Acme Industries",
            "logo_url": "javascript:alert(1)",
            "domain": "not a domain",
        }
    ]

    with patch.object(app, "get_stock_universe", return_value=rows):
        reset_logo_caches()
        markup = str(app.stock_identity("ACME", size="not-a-size"))

    identity = BeautifulSoup(markup, "html.parser").select_one(
        "[data-company-identity='ACME']"
    )
    image = identity.select_one("img.company-logo-image")

    assert "company-identity--card" in identity["class"]
    assert image["src"].endswith("/ACME.png")
    assert "javascript:" not in markup
    assert identity.select_one(".company-logo-fallback").get_text(strip=True) == "AI"
    assert identity.select_one(".company-identity-name").get_text(strip=True) == (
        "Acme Industries (ACME)"
    )


def test_initials_badge_renders_when_no_logo_url_is_available():
    metadata = {
        "ticker": "NONE",
        "company_name": "No Logo Limited",
        "logo_url": "",
        "fallback_logo_url": "",
        "initials": "NL",
    }

    with patch.object(app, "company_logo_metadata", return_value=metadata):
        markup = str(app.stock_identity("NONE", "No Logo Limited (NONE)"))

    identity = BeautifulSoup(markup, "html.parser")
    assert identity.select_one("img.company-logo-image") is None
    assert identity.select_one(".company-logo-fallback").get_text(strip=True) == "NL"
    assert identity.select_one(".company-identity-name").get_text(strip=True) == (
        "No Logo Limited (NONE)"
    )


def test_visa_uses_legible_provider_asset_in_server_and_dynamic_renderers():
    reset_logo_caches()

    metadata = app.company_logo_metadata("V")
    javascript = (ROOT_DIR / "static" / "company_logos.js").read_text(
        encoding="utf-8"
    )

    assert metadata["ticker"] == "V"
    assert metadata["company_name"] == "Visa Inc."
    assert metadata["logo_url"].endswith("/0QZ0.L.png")
    assert 'safeTicker === "V" ? "0QZ0.L" : safeTicker' in javascript


def test_universe_normalisation_preserves_optional_logo_metadata():
    row = app.normalise_universe_row(
        {
            "ticker": "META",
            "name": "Metadata Company",
            "company_website": "https://metadata.example/about",
            "logo": "https://assets.example/logo.svg",
        }
    )

    assert row["website"] == "https://metadata.example/about"
    assert row["domain"] == ""
    assert row["logo_url"] == "https://assets.example/logo.svg"


def test_logo_renderer_escapes_untrusted_company_metadata():
    rows = [
        {
            "ticker": "SAFE",
            "name": '<script>alert("name")</script>',
            "logo_url": 'https://assets.example.com/logo.png?x="><script>',
        }
    ]

    with patch.object(app, "get_stock_universe", return_value=rows):
        reset_logo_caches()
        markup = str(app.stock_identity("SAFE"))

    assert "<script>alert" not in markup
    assert "&lt;script&gt;" in markup
    assert BeautifulSoup(markup, "html.parser").select_one("img")["alt"].endswith(
        " logo"
    )


def test_universe_and_stock_detail_routes_use_the_shared_logo_renderer():
    reset_logo_caches()
    client = app.app.test_client()

    universe_response = client.get("/universe?q=apple")
    with patch.object(app, "safe_history", return_value=pd.DataFrame()):
        stock_response = client.get("/stock/AAPL")

    universe_page = universe_response.get_data(as_text=True)
    stock_page = stock_response.get_data(as_text=True)

    assert universe_response.status_code == 200
    assert stock_response.status_code == 200
    assert 'data-company-identity="AAPL"' in universe_page
    assert 'alt="Apple Inc. logo"' in universe_page
    assert 'company-identity--detail' in stock_page
    assert 'data-company-identity="AAPL"' in stock_page
    assert stock_page.count("/static/company_logos.css") == 1
    assert stock_page.count("/static/company_logos.js") == 1


def test_company_logo_assets_are_local_responsive_and_preserve_aspect_ratio():
    css = (ROOT_DIR / "static" / "company_logos.css").read_text(encoding="utf-8")
    javascript = (ROOT_DIR / "static" / "company_logos.js").read_text(
        encoding="utf-8"
    )

    assert "object-fit: contain" in css
    assert "@media (max-width: 760px)" in css
    assert ".company-identity--detail .company-logo-frame" in css
    assert "width: 36px" in css
    assert "width: 24px" in css
    assert 'document.addEventListener("error"' in javascript
    assert "logoFallbackSrc" in javascript
    assert "createIdentity" in javascript
    assert "showInitials" in javascript


def test_logo_metadata_is_cached_per_company():
    rows = [{"ticker": "CACHE", "name": "Cache Company"}]

    with patch.object(app, "get_stock_universe", return_value=rows) as universe:
        reset_logo_caches()
        first = app.company_logo_metadata("CACHE")
        second = app.company_logo_metadata("CACHE")

    assert first == second
    assert universe.call_count <= 3
    assert app.COMPANY_LOGO_METADATA_CACHE["CACHE"] == first


def test_templates_use_one_reusable_component_instead_of_inline_logo_markup():
    source = (ROOT_DIR / "app.py").read_text(encoding="utf-8")

    assert app.app.jinja_env.globals["stock_identity"] is app.stock_identity
    assert source.count('class="company-logo-image"') == 1
    assert source.count("stock_identity(") >= 20
    assert "window.StockRadarCompanyLogos.createIdentity" in source
