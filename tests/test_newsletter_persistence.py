import json
import os
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import app


LONDON = ZoneInfo("Europe/London")


def configure_persistence(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(app, "STOCKRADAR_DATA_DIR", str(data_dir))
    monkeypatch.setattr(app, "STOCKRADAR_DATA_DIR_EXPLICIT", True)
    monkeypatch.setattr(app, "PERSISTENCE_CONFIGURATION_ERROR", "")
    monkeypatch.setattr(app, "PERSISTENCE_LAST_ERROR", "")
    monkeypatch.setattr(app, "NEWSLETTER_ISSUES_PATH", str(data_dir / "newsletter_issues.json"))
    monkeypatch.setattr(
        app,
        "NEWSLETTER_STORY_HISTORY_PATH",
        str(data_dir / "newsletter_story_history.json"),
    )
    monkeypatch.setattr(
        app,
        "NEWSLETTER_MARKET_SNAPSHOTS_PATH",
        str(data_dir / "newsletter_market_snapshots.json"),
    )
    monkeypatch.setattr(
        app,
        "NEWSLETTER_DELIVERY_LOG_PATH",
        str(data_dir / "newsletter_delivery_log.json"),
    )
    monkeypatch.setattr(
        app,
        "NEWSLETTER_BEEHIIV_STATE_PATH",
        str(data_dir / "newsletter_beehiiv_state.json"),
    )
    monkeypatch.setattr(
        app,
        "NEWSLETTER_SUBSCRIBERS_PATH",
        str(data_dir / "newsletter_subscribers.json"),
    )
    monkeypatch.setattr(app, "NEWSLETTER_SEND_LOCK_DIR", str(data_dir / ".newsletter_locks"))
    app.WEEKLY_NEWSLETTER_ISSUE_CACHE.update({
        "issue_date": None,
        "issue_status": None,
        "generated_at": None,
        "issue": None,
    })
    return data_dir


def finalized_issue(issue_id="stockradar-weekly-2026-W30", marker="first"):
    return {
        "metadata": {
            "issue_id": issue_id,
            "guid": issue_id,
            "issue_key": "newsletter:2026-07-24",
            "issue_date": "2026-07-24",
            "iso_year": 2026,
            "iso_week": 30,
            "status": "final",
            "is_final": True,
            "published_at": "2026-07-24T08:01:00+00:00",
            "generated_at": "2026-07-24T08:01:00+00:00",
            "generated_at_label": "24 July 2026 at 09:01 BST",
            "window_start_utc": "2026-07-17T08:00:00+00:00",
            "window_end_utc": "2026-07-24T08:00:00+00:00",
            "title": "StockRadar Weekly",
            "marker": marker,
        },
        "draft": {
            "plain_text": marker,
            "market_mood": "Mixed",
            "market_pulse": "Test market pulse.",
            "market_week_summary": "Test weekly summary.",
            "investor_lesson": "Test investor lesson.",
            "disclaimer": "Educational only.",
            "premium_note": "Premium research preview.",
            "what_looked_strong": [],
            "what_looked_weak": [],
            "market_tracker": [],
            "risk_check": [],
            "signal_watch": {"changes": [], "current_signals": []},
            "trending_vs_forecasting": {
                "trending": [{
                    "headline": "Verified coverage unavailable.",
                    "source": "StockRadar weekly verification",
                    "url": "",
                }],
                "forecasting": [],
            },
        },
        "articles": [],
        "summary": "Test market pulse.",
        "subject": "StockRadar Weekly",
        "preview_text": "Test preview.",
    }


def test_custom_data_directory_is_normalized(monkeypatch, tmp_path):
    custom = tmp_path / "nested" / ".." / "persistent"
    monkeypatch.setenv("STOCKRADAR_DATA_DIR", str(custom))
    state = app.resolve_stockradar_data_dir(
        production=False,
        app_root=str(tmp_path / "application"),
        create=False,
    )
    assert state["path"] == os.path.realpath(custom)
    assert state["explicit"] is True
    assert state["error"] == ""


def test_development_default_is_safe_and_created(tmp_path):
    application_root = tmp_path / "application"
    state = app.resolve_stockradar_data_dir(
        configured_value="",
        production=False,
        app_root=str(application_root),
        create=True,
    )
    assert state["path"] == str(application_root / ".stockradar_data")
    assert os.path.isdir(state["path"])
    assert state["explicit"] is False


def test_production_without_external_directory_is_degraded(tmp_path):
    state = app.resolve_stockradar_data_dir(
        configured_value="",
        production=True,
        app_root=str(tmp_path / "application"),
        create=False,
    )
    assert state["explicit"] is False
    assert state["error"] == "data_directory_not_configured"
    assert not app.path_is_within_directory(
        state["path"],
        os.path.realpath(tmp_path / "application"),
    )


def test_all_mutable_runtime_files_resolve_under_canonical_directory(
    monkeypatch,
    tmp_path,
):
    data_dir = configure_persistence(monkeypatch, tmp_path)
    paths = (
        app.NEWSLETTER_ISSUES_PATH,
        app.NEWSLETTER_STORY_HISTORY_PATH,
        app.NEWSLETTER_MARKET_SNAPSHOTS_PATH,
        app.NEWSLETTER_DELIVERY_LOG_PATH,
        app.NEWSLETTER_BEEHIIV_STATE_PATH,
        app.NEWSLETTER_SUBSCRIBERS_PATH,
        app.NEWSLETTER_SEND_LOCK_DIR,
    )
    assert all(
        app.path_is_within_directory(
            os.path.realpath(path),
            os.path.realpath(data_dir),
        )
        for path in paths
    )


def test_unwritable_directory_is_reported_without_path(monkeypatch, tmp_path):
    data_dir = configure_persistence(monkeypatch, tmp_path)
    data_dir.mkdir()

    def deny_probe(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(app.tempfile, "mkstemp", deny_probe)
    assert app.persistence_directory_is_writable() is False
    assert app.PERSISTENCE_LAST_ERROR == "persistence_directory_not_writable"
    assert str(data_dir) not in app.PERSISTENCE_LAST_ERROR


def test_atomic_write_interruption_preserves_existing_json(monkeypatch, tmp_path):
    path = tmp_path / "state.json"
    assert app.save_json_storage(str(path), {"version": 1})
    original = path.read_bytes()

    def interrupted_replace(*args, **kwargs):
        raise OSError("interrupted")

    monkeypatch.setattr(app.os, "replace", interrupted_replace)
    assert app.save_json_storage(str(path), {"version": 2}) is False
    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".state.json.*.tmp"))


def test_corrupt_json_is_not_silently_overwritten(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{broken", encoding="utf-8")
    assert app.load_json_storage(str(path), {"safe": True}) == {"safe": True}
    assert app.save_json_storage(str(path), {"replacement": True}) is False
    assert path.read_text(encoding="utf-8") == "{broken"
    assert app.PERSISTENCE_LAST_ERROR == "corrupt_json_store"


def test_degraded_required_store_blocks_new_generation(monkeypatch, tmp_path):
    data_dir = configure_persistence(monkeypatch, tmp_path)
    data_dir.mkdir()
    story_path = data_dir / "newsletter_story_history.json"
    story_path.write_text("{broken", encoding="utf-8")
    generated = {"called": False}

    def unexpected_generation(now=None, force_refresh=False):
        generated["called"] = True

    monkeypatch.setattr(
        app,
        "_build_weekly_newsletter_issue_without_generation_lock",
        unexpected_generation,
    )
    now = datetime(2026, 7, 24, 10, 0, tzinfo=LONDON)
    try:
        app.build_weekly_newsletter_issue(now=now)
    except RuntimeError as error:
        assert str(error) == "newsletter_persistence_degraded"
    else:
        raise AssertionError("generation should be blocked")
    assert generated["called"] is False
    assert story_path.read_text(encoding="utf-8") == "{broken"


def test_duplicate_finalization_attempts_are_immutable(monkeypatch, tmp_path):
    configure_persistence(monkeypatch, tmp_path)
    first = finalized_issue(marker="first")
    second = finalized_issue(marker="second")
    assert app.persist_finalized_newsletter_issue(first)["metadata"]["marker"] == "first"
    assert app.persist_finalized_newsletter_issue(second)["metadata"]["marker"] == "first"
    assert app.get_finalized_newsletter_issue(
        first["metadata"]["issue_id"]
    )["metadata"]["marker"] == "first"


def test_concurrent_finalization_keeps_one_immutable_issue(monkeypatch, tmp_path):
    configure_persistence(monkeypatch, tmp_path)
    issues = [finalized_issue(marker="one"), finalized_issue(marker="two")]
    results = []

    def finalize(issue):
        results.append(app.persist_finalized_newsletter_issue(issue))

    threads = [threading.Thread(target=finalize, args=(issue,)) for issue in issues]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    markers = {item["metadata"]["marker"] for item in results}
    assert len(markers) == 1
    stored = app.load_newsletter_issues()["issues"]
    assert len(stored) == 1


def test_scheduler_and_route_generation_share_one_finalization(
    monkeypatch,
    tmp_path,
):
    configure_persistence(monkeypatch, tmp_path)
    now = datetime(2026, 7, 24, 10, 0, tzinfo=LONDON)
    issue_id = app.newsletter_weekly_window(now)["issue_id"]
    calls = {"count": 0}

    def slow_generation(now=None, force_refresh=False):
        calls["count"] += 1
        time.sleep(0.1)
        return app.persist_finalized_newsletter_issue(
            finalized_issue(issue_id=issue_id)
        )

    monkeypatch.setattr(
        app,
        "_build_weekly_newsletter_issue_without_generation_lock",
        slow_generation,
    )
    results = []

    def request_issue():
        results.append(app.build_weekly_newsletter_issue(now=now))

    scheduler = threading.Thread(target=request_issue)
    route = threading.Thread(target=request_issue)
    scheduler.start()
    route.start()
    scheduler.join()
    route.join()

    assert calls["count"] == 1
    assert len(results) == 2
    assert results[0]["metadata"]["issue_id"] == results[1]["metadata"]["issue_id"]


def test_restart_loads_same_finalized_issue(monkeypatch, tmp_path):
    configure_persistence(monkeypatch, tmp_path)
    now = datetime(2026, 7, 24, 10, 0, tzinfo=LONDON)
    issue = finalized_issue(
        issue_id=app.newsletter_weekly_window(now)["issue_id"]
    )
    app.persist_finalized_newsletter_issue(issue)
    app.WEEKLY_NEWSLETTER_ISSUE_CACHE["issue"] = None
    assert app.load_or_generate_latest_newsletter_issue(now=now) == issue


def test_legacy_migration_is_idempotent_and_never_overwrites(
    monkeypatch,
    tmp_path,
):
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    data_dir = configure_persistence(monkeypatch, tmp_path)
    source = legacy_root / "newsletter_issues.json"
    source.write_text(
        json.dumps({"issues": {"legacy": {"value": 1}}, "latest_issue_id": "legacy"}),
        encoding="utf-8",
    )

    first = app.migrate_legacy_newsletter_storage(str(legacy_root))
    destination = data_dir / "newsletter_issues.json"
    assert "newsletter_issues.json" in first["migrated"]
    original_destination = destination.read_text(encoding="utf-8")
    assert source.exists()

    source.write_text(json.dumps({"issues": {"new": {}}}), encoding="utf-8")
    second = app.migrate_legacy_newsletter_storage(str(legacy_root))
    assert "newsletter_issues.json" in second["skipped_existing"]
    assert destination.read_text(encoding="utf-8") == original_destination


def test_health_has_persistence_fields_and_exposes_no_path(
    monkeypatch,
    tmp_path,
):
    data_dir = configure_persistence(monkeypatch, tmp_path)
    monkeypatch.setattr(app, "IS_PRODUCTION", True)
    monkeypatch.setattr(app, "INTERNAL_DIAGNOSTICS_SECRET", "test-internal")
    response = app.app.test_client().get(
        "/health",
        headers={"X-StockRadar-Internal-Secret": "test-internal"},
    )
    assert response.status_code == 200
    newsletter = response.get_json()["newsletter"]
    for field in (
        "persistence_configured",
        "persistence_directory_writable",
        "persistence_backend",
        "issue_store_available",
        "story_history_store_available",
        "market_snapshot_store_available",
        "persistence_last_error",
    ):
        assert field in newsletter
    assert newsletter["persistence_status"] == "ready"
    assert str(data_dir) not in json.dumps(newsletter)
    assert "STOCKRADAR_DATA_DIR" not in json.dumps(newsletter)
