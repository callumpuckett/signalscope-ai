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
