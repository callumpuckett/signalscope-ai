from pathlib import Path
import sys
import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


@pytest.fixture(autouse=True)
def isolate_application_security_state(monkeypatch, tmp_path):
    """Keep legacy route tests isolated; focused CSRF tests enable it explicitly."""
    import app as stockradar

    previous_csrf = stockradar.app.config.get("WTF_CSRF_ENABLED", True)
    artifact_root = str(tmp_path / "published-newsletters")
    monkeypatch.setattr(
        stockradar,
        "NEWSLETTER_PUBLISHED_ARTIFACT_DIR",
        artifact_root,
    )
    monkeypatch.setattr(
        stockradar,
        "NEWSLETTER_PUBLISHED_ARTIFACT_STORE",
        stockradar.FilesystemPublishedArtifactStore(
            artifact_root,
            max_bytes=stockradar.NEWSLETTER_PUBLISHED_ARTIFACT_MAX_BYTES,
        ),
    )
    monkeypatch.setattr(stockradar, "NEWSLETTER_PUBLISHED_ARTIFACT_LAST_ERROR", "")
    monkeypatch.setattr(
        stockradar,
        "NEWSLETTER_PUBLISHED_ARTIFACT_LAST_KNOWN_GOOD",
        None,
    )
    stockradar.app.config["WTF_CSRF_ENABLED"] = False
    with stockradar.SECURITY_RATE_LIMIT_LOCK:
        stockradar.SECURITY_RATE_LIMIT_STATE["buckets"] = {}
    with stockradar.TURNSTILE_TOKEN_LOCK:
        stockradar.TURNSTILE_TOKEN_STATE["tokens"] = {}
    yield
    stockradar.app.config["WTF_CSRF_ENABLED"] = previous_csrf
