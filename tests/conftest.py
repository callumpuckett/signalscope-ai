from pathlib import Path
import sys
import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


@pytest.fixture(autouse=True)
def isolate_application_security_state(monkeypatch, tmp_path, request):
    """Keep legacy route tests isolated; focused CSRF tests enable it explicitly."""
    import app as stockradar

    # Legacy provider tests mock the yfinance adapter; direct transport has its own tests.
    monkeypatch.setattr(stockradar, "YAHOO_HISTORY_TRANSPORT", "yfinance")
    monkeypatch.setattr(stockradar, "YAHOO_HISTORY_DIRECTORY", str(tmp_path / "market-history"))
    monkeypatch.setattr(stockradar, "YAHOO_CHART_COOLDOWN_UNTIL", 0)
    monkeypatch.setattr(stockradar, "YAHOO_METADATA_ASYNC_ENABLED", False)
    monkeypatch.setattr(stockradar, "DASHBOARD_CACHE", {"timestamp": 0, "data": None})
    monkeypatch.setattr(stockradar, "DASHBOARD_REFRESH_PENDING", False)
    monkeypatch.setattr(stockradar, "DASHBOARD_REFRESH_RETRY_AT", 0)
    # Route-render tests must not leave real network threads mutating later tests.
    # Focused concurrency tests exercise the real scheduler with controlled providers.
    if request.node.path.name != "test_site_performance_audit.py":
        monkeypatch.setattr(stockradar, "_start_dashboard_refresh", lambda *args, **kwargs: None)

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
