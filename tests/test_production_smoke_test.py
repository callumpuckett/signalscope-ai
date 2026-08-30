from pathlib import Path
import importlib.util
import sys

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "production_smoke_test.py"
SPEC = importlib.util.spec_from_file_location("production_smoke_test", SCRIPT_PATH)
smoke = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = smoke
SPEC.loader.exec_module(smoke)


@pytest.mark.parametrize(
    "value, expected",
    [
        ("https://www.stockradarhq.com/", "https://www.stockradarhq.com"),
        ("http://127.0.0.1:8080", "http://127.0.0.1:8080"),
    ],
)
def test_normalise_base_url_accepts_safe_http_urls(value, expected):
    assert smoke._normalise_base_url(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "postgresql://example.com/database",
        "https://username@example.com",
        "https://example.com/?token=secret",
        "example.com",
    ],
)
def test_normalise_base_url_rejects_credentials_and_unsafe_shapes(value):
    with pytest.raises(ValueError):
        smoke._normalise_base_url(value)


def test_safe_path_drops_query_values():
    assert smoke._safe_path("https://example.com/private?token=secret") == "/private"


def logo_details(**overrides):
    details = {
        "alt": "Microsoft Corporation logo",
        "current_src": "https://images.example.test/MSFT.png",
        "declared_src": "https://images.example.test/MSFT.png",
        "fallback_src": "https://fallback.example.test/microsoft.com",
        "complete": False,
        "natural_width": 0,
        "fallback_text": "MI",
        "fallback_active": False,
        "frame_width": 48,
        "frame_height": 48,
    }
    details.update(overrides)
    return details


def test_company_logo_accepts_loaded_image():
    details = logo_details(complete=True, natural_width=96)
    assert smoke._company_logo_render_mode(details, "https://www.stockradarhq.com") == "image"


@pytest.mark.parametrize(
    "identity_text",
    ["Microsoft", "Microsoft Corporation", "  Microsoft Corporation (MSFT)  "],
)
def test_msft_company_identity_accepts_production_name_variants(identity_text):
    assert smoke._is_msft_company_identity(identity_text, "MSFT")


@pytest.mark.parametrize(
    "identity_text, ticker",
    [
        ("Apple Inc. (AAPL)", "AAPL"),
        ("Amazon.com, Inc. (AMZN)", "AMZN"),
        ("Microsoft Corporation (MSFT)", "AAPL"),
        ("", "MSFT"),
    ],
)
def test_msft_company_identity_rejects_wrong_or_empty_identity(identity_text, ticker):
    assert not smoke._is_msft_company_identity(identity_text, ticker)


def test_company_logo_accepts_external_image_failure_with_available_fallback():
    assert (
        smoke._company_logo_render_mode(logo_details(), "https://www.stockradarhq.com")
        == "external-fallback"
    )


def test_company_logo_accepts_active_initials_fallback():
    details = logo_details(current_src="", declared_src="", fallback_active=True)
    assert smoke._company_logo_render_mode(details, "https://www.stockradarhq.com") == "fallback"


def test_company_logo_rejects_failed_app_owned_image():
    details = logo_details(
        current_src="https://www.stockradarhq.com/static/logos/MSFT.png",
        declared_src="/static/logos/MSFT.png",
        fallback_active=True,
    )
    with pytest.raises(smoke.SmokeFailure, match="app-owned"):
        smoke._company_logo_render_mode(details, "https://www.stockradarhq.com")


def test_company_logo_rejects_relative_app_owned_image():
    details = logo_details(current_src="", declared_src="/static/logos/MSFT.png", fallback_src="")
    with pytest.raises(smoke.SmokeFailure, match="app-owned"):
        smoke._company_logo_render_mode(details, "https://www.stockradarhq.com")


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"fallback_text": ""}, "fallback is missing"),
        ({"frame_width": 0}, "frame is collapsed"),
        ({"alt": ""}, "fallback/alt assertion failed"),
    ],
)
def test_company_logo_rejects_broken_identity_fallback(overrides, message):
    with pytest.raises(smoke.SmokeFailure, match=message):
        smoke._company_logo_render_mode(logo_details(**overrides), "https://www.stockradarhq.com")
