import copy
import json
import threading
import time
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import app
from newsletter_storage import (
    DegradedNewsletterStorage,
    FilesystemNewsletterStorage,
    PostgresNewsletterStorage,
    POSTGRES_SCHEMA_STATEMENTS,
)


LONDON = ZoneInfo("Europe/London")


def unwrap_json(value):
    for attribute in ("obj", "adapted"):
        if hasattr(value, attribute):
            return copy.deepcopy(getattr(value, attribute))
    return copy.deepcopy(value)


class MemoryPostgres:
    def __init__(self):
        self.tables = {
            "issues": {},
            "stories": {},
            "snapshots": {},
            "deliveries": {},
            "runs": {},
            "beehiiv": {},
            "subscribers": {},
            "application_state": {},
            "migrations": {},
        }
        self.statements = []
        self.commits = 0
        self.rollbacks = 0
        self.fail_on = ""
        self._lock_guard = threading.Lock()
        self._locks = {}

    def connector(self, database_url, connect_timeout=5):
        assert database_url.startswith("postgresql://")
        assert connect_timeout == 5
        return MemoryConnection(self)

    def lock_for(self, key):
        with self._lock_guard:
            return self._locks.setdefault(key, threading.Lock())


class MemoryConnection:
    def __init__(self, database):
        self.database = database
        self.snapshot = copy.deepcopy(database.tables)
        self.transaction_locks = []
        self.session_locks = []
        self.closed = False

    def cursor(self):
        return MemoryCursor(self)

    def release_transaction_locks(self):
        for lock in reversed(self.transaction_locks):
            lock.release()
        self.transaction_locks = []

    def commit(self):
        self.database.commits += 1
        self.snapshot = copy.deepcopy(self.database.tables)
        self.release_transaction_locks()

    def rollback(self):
        self.database.rollbacks += 1
        self.database.tables = copy.deepcopy(self.snapshot)
        self.release_transaction_locks()

    def close(self):
        self.release_transaction_locks()
        for lock in reversed(self.session_locks):
            if lock.locked():
                lock.release()
        self.session_locks = []
        self.closed = True


class MemoryCursor:
    def __init__(self, connection):
        self.connection = connection
        self.results = []
        self.closed = False

    @property
    def database(self):
        return self.connection.database

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def close(self):
        self.closed = True

    def fetchall(self):
        return list(self.results)

    def fetchone(self):
        return self.results[0] if self.results else None

    def execute(self, sql, params=None):
        statement = " ".join(str(sql).split())
        lowered = statement.lower()
        params = tuple(params or ())
        self.database.statements.append((statement, params))
        if self.database.fail_on and self.database.fail_on in lowered:
            raise RuntimeError("simulated_database_failure")

        if lowered.startswith("create table") or lowered.startswith("create index"):
            self.results = []
        elif "pg_advisory_xact_lock" in lowered:
            lock = self.database.lock_for(params[0])
            lock.acquire()
            self.connection.transaction_locks.append(lock)
            self.results = [(True,)]
        elif "pg_try_advisory_lock" in lowered:
            lock = self.database.lock_for(params[0])
            acquired = lock.acquire(blocking=False)
            if acquired:
                self.connection.session_locks.append(lock)
            self.results = [(acquired,)]
        elif "pg_advisory_unlock" in lowered:
            lock = self.database.lock_for(params[0])
            if lock in self.connection.session_locks and lock.locked():
                lock.release()
                self.connection.session_locks.remove(lock)
            self.results = [(True,)]
        elif lowered.startswith("select issue_id, payload"):
            rows = list(self.database.tables["issues"].items())
            self.results = [(key, copy.deepcopy(value)) for key, value in reversed(rows)]
        elif lowered.startswith("insert into newsletter_issues"):
            issue_id = str(params[0])
            self.database.tables["issues"].setdefault(
                issue_id,
                unwrap_json(params[6]),
            )
            self.results = []
        elif lowered.startswith("select story_fingerprint, payload"):
            self.results = [
                (key, copy.deepcopy(value))
                for key, value in self.database.tables["stories"].items()
            ]
        elif lowered.startswith("insert into newsletter_story_history"):
            fingerprint = str(params[0])
            payload = unwrap_json(params[3])
            if "do update" in lowered or fingerprint not in self.database.tables["stories"]:
                self.database.tables["stories"][fingerprint] = payload
            self.results = []
        elif lowered.startswith("select snapshot_key, payload"):
            self.results = [
                (key, copy.deepcopy(value))
                for key, value in self.database.tables["snapshots"].items()
            ]
        elif lowered.startswith("insert into newsletter_market_snapshots"):
            self.database.tables["snapshots"].setdefault(
                str(params[0]),
                unwrap_json(params[3]),
            )
            self.results = []
        elif "select payload from newsletter_delivery_records" in lowered:
            self.results = [
                (copy.deepcopy(value),)
                for value in self.database.tables["deliveries"].values()
            ]
        elif lowered.startswith("insert into newsletter_delivery_records"):
            key = (str(params[0]), str(params[1]))
            self.database.tables["deliveries"].setdefault(
                key,
                unwrap_json(params[3]),
            )
            self.results = []
        elif "select payload from newsletter_scheduler_runs" in lowered:
            self.results = [
                (copy.deepcopy(value),)
                for value in self.database.tables["runs"].values()
            ]
        elif lowered.startswith("insert into newsletter_scheduler_runs"):
            self.database.tables["runs"].setdefault(
                str(params[0]),
                unwrap_json(params[2]),
            )
            self.results = []
        elif lowered.startswith("select issue_key, payload"):
            self.results = [
                (key, copy.deepcopy(value))
                for key, value in self.database.tables["beehiiv"].items()
            ]
        elif lowered.startswith("insert into newsletter_beehiiv_state"):
            key = str(params[0])
            payload = unwrap_json(params[2])
            if "do update" in lowered or key not in self.database.tables["beehiiv"]:
                self.database.tables["beehiiv"][key] = payload
            self.results = []
        elif "select payload from newsletter_subscribers" in lowered:
            self.results = [
                (copy.deepcopy(value),)
                for value in self.database.tables["subscribers"].values()
            ]
        elif lowered.startswith("insert into newsletter_subscribers"):
            email = str(params[0])
            payload = unwrap_json(params[2])
            if "do update" in lowered or email not in self.database.tables["subscribers"]:
                self.database.tables["subscribers"][email] = payload
            self.results = []
        elif "from stockradar_application_state" in lowered:
            payload = self.database.tables["application_state"].get(
                str(params[0])
            )
            self.results = [(copy.deepcopy(payload),)] if payload else []
        elif lowered.startswith("insert into stockradar_application_state"):
            self.database.tables["application_state"][str(params[0])] = (
                unwrap_json(params[1])
            )
            self.results = []
        elif "from newsletter_storage_migrations" in lowered:
            migration = self.database.tables["migrations"].get(str(params[0]))
            self.results = [migration] if migration else []
        elif lowered.startswith("insert into newsletter_storage_migrations"):
            key = str(params[0])
            self.database.tables["migrations"].setdefault(
                key,
                (str(params[1]), unwrap_json(params[2])),
            )
            self.results = []
        else:
            raise AssertionError(f"Unhandled SQL: {statement}")


def postgres_backend(database=None):
    database = database or MemoryPostgres()
    backend = PostgresNewsletterStorage(
        "postgresql://user:password@db.example.test/stockradar",
        connector=database.connector,
    )
    assert backend.initialize_schema()
    return backend, database


def issue_for(marker="first"):
    issue_id = "stockradar-weekly-2026-W30"
    return {
        "metadata": {
            "issue_id": issue_id,
            "guid": issue_id,
            "issue_key": "newsletter:2026-07-24",
            "issue_date": "2026-07-24",
            "iso_year": 2026,
            "iso_week": 30,
            "generated_at": "2026-07-24T08:01:00+00:00",
            "finalized_at": "2026-07-24T08:01:00+00:00",
            "window_end_utc": "2026-07-24T08:00:00+00:00",
            "status": "final",
            "is_final": True,
            "title": "StockRadar Weekly",
            "marker": marker,
        },
        "draft": {"plain_text": marker},
        "articles": [],
    }


def test_postgresql_backend_selection():
    backend = app.build_newsletter_storage_backend(
        database_url="postgresql://configured",
        production=True,
        postgres_connector=lambda *args, **kwargs: None,
    )
    assert isinstance(backend, PostgresNewsletterStorage)
    assert backend.identifier == "postgresql"


def test_local_filesystem_fallback():
    backend = app.build_newsletter_storage_backend(
        database_url="",
        production=False,
    )
    assert isinstance(backend, FilesystemNewsletterStorage)
    assert backend.identifier == "filesystem_json"


def test_production_ephemeral_filesystem_is_rejected(monkeypatch):
    monkeypatch.setattr(app, "STOCKRADAR_DATA_DIR_EXPLICIT", False)
    monkeypatch.setattr(
        app,
        "PERSISTENCE_CONFIGURATION_ERROR",
        "data_directory_not_configured",
    )
    backend = app.build_newsletter_storage_backend(
        database_url="",
        production=True,
    )
    assert isinstance(backend, DegradedNewsletterStorage)
    assert backend.identifier == "degraded_ephemeral"
    assert backend.update_state("issues", lambda data: data.update({"x": 1})) is False


def test_production_explicit_durable_filesystem_is_allowed(monkeypatch):
    monkeypatch.setattr(app, "STOCKRADAR_DATA_DIR_EXPLICIT", True)
    monkeypatch.setattr(app, "PERSISTENCE_CONFIGURATION_ERROR", "")
    backend = app.build_newsletter_storage_backend(
        database_url="",
        production=True,
    )
    assert isinstance(backend, FilesystemNewsletterStorage)


def test_schema_initialization_is_idempotent():
    backend, database = postgres_backend()
    assert backend.initialize_schema()
    creates = [
        statement for statement, _ in database.statements
        if statement.lower().startswith(("create table", "create index"))
    ]
    assert len(creates) == len(POSTGRES_SCHEMA_STATEMENTS) * 2
    assert all("IF NOT EXISTS".lower() in statement.lower() for statement in creates)
    assert database.rollbacks == 0


def test_postgres_issue_finalization_is_immutable(monkeypatch):
    backend, database = postgres_backend()
    monkeypatch.setattr(app, "NEWSLETTER_STORAGE", backend)
    first = app.persist_finalized_newsletter_issue(issue_for("first"))
    repeated = app.persist_finalized_newsletter_issue(issue_for("second"))
    assert first["metadata"]["marker"] == "first"
    assert repeated["metadata"]["marker"] == "first"
    assert database.tables["issues"][
        "stockradar-weekly-2026-W30"
    ]["metadata"]["marker"] == "first"


def test_postgres_restart_loads_finalized_issue(monkeypatch):
    backend, database = postgres_backend()
    monkeypatch.setattr(app, "NEWSLETTER_STORAGE", backend)
    app.persist_finalized_newsletter_issue(issue_for())
    restarted = PostgresNewsletterStorage(
        "postgresql://user:password@db.example.test/stockradar",
        connector=database.connector,
    )
    assert restarted.initialize_schema()
    assert restarted.load_issue_by_id(
        "stockradar-weekly-2026-W30"
    )["metadata"]["marker"] == "first"


def test_postgres_restart_preserves_premium_entitlements(monkeypatch):
    backend, database = postgres_backend()
    monkeypatch.setattr(app, "NEWSLETTER_STORAGE", backend)
    stored_record = app.update_premium_entitlement(
        customer_id="cus_active",
        subscription_id="sub_active",
        email="reader@example.test",
        subscription_status="active",
        premium_active=True,
        event_type="checkout-success",
    )
    assert stored_record["premium_active"] is True
    assert stored_record["entitlement_version"] == 1

    restarted = PostgresNewsletterStorage(
        "postgresql://user:password@db.example.test/stockradar",
        connector=database.connector,
    )
    assert restarted.initialize_schema()

    records = restarted.load_state("premium_entitlements")["records"]
    assert len(records) == 1
    assert records[0]["stripe_subscription_id"] == "sub_active"
    assert records[0]["premium_active"] is True


def test_concurrent_postgres_finalization_keeps_one_issue(monkeypatch):
    backend, database = postgres_backend()
    monkeypatch.setattr(app, "NEWSLETTER_STORAGE", backend)
    results = []

    def finalize(marker):
        results.append(app.persist_finalized_newsletter_issue(issue_for(marker)))

    threads = [
        threading.Thread(target=finalize, args=("first",)),
        threading.Thread(target=finalize, args=("second",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(database.tables["issues"]) == 1
    assert len({result["metadata"]["marker"] for result in results}) == 1


def test_postgres_story_history_deduplicates_issue_ids(monkeypatch):
    backend, database = postgres_backend()
    monkeypatch.setattr(app, "NEWSLETTER_STORAGE", backend)
    issue = issue_for()
    issue["articles"] = [{
        "story_fingerprint": "story-1",
        "provider": "newsapi",
        "provider_article_id": "article-1",
        "title": "A new market development",
        "published_at": "2026-07-22T12:00:00+00:00",
        "fetched_at": "2026-07-22T12:01:00+00:00",
    }]
    assert app.record_newsletter_story_usage(issue)
    assert app.record_newsletter_story_usage(issue)
    story = database.tables["stories"]["story-1"]
    assert story["issue_ids_used_in"] == ["stockradar-weekly-2026-W30"]


def test_postgres_snapshot_storage_is_idempotent(monkeypatch):
    backend, database = postgres_backend()
    monkeypatch.setattr(app, "NEWSLETTER_STORAGE", backend)
    first = {
        "snapshot_id": "snapshot-first",
        "cutoff_utc": "2026-07-24T08:00:00+00:00",
    }
    second = {
        "snapshot_id": "snapshot-second",
        "cutoff_utc": "2026-07-24T08:00:00+00:00",
    }

    def store(snapshot):
        def update(data):
            snapshots = data.setdefault("snapshots", {})
            if first["cutoff_utc"] in snapshots:
                return False
            snapshots[first["cutoff_utc"]] = snapshot
        return update

    assert app.newsletter_storage_update("market_snapshots", store(first))
    assert app.newsletter_storage_update("market_snapshots", store(second))
    assert database.tables["snapshots"][
        first["cutoff_utc"]
    ]["snapshot_id"] == "snapshot-first"


def test_postgres_delivery_state_is_idempotent(monkeypatch):
    backend, database = postgres_backend()
    monkeypatch.setattr(app, "NEWSLETTER_STORAGE", backend)
    issue = issue_for()
    assert app.record_newsletter_delivery("reader@example.test", issue, "weekly")
    assert app.record_newsletter_delivery("reader@example.test", issue, "weekly")
    assert len(database.tables["deliveries"]) == 1


def test_postgres_transaction_failure_rolls_back(monkeypatch):
    backend, database = postgres_backend()
    monkeypatch.setattr(app, "NEWSLETTER_STORAGE", backend)
    database.fail_on = "insert into newsletter_issues"
    try:
        app.persist_finalized_newsletter_issue(issue_for())
    except RuntimeError as error:
        assert str(error) == "newsletter_issue_persistence_failed"
    else:
        raise AssertionError("persistence failure should be reported")
    assert database.tables["issues"] == {}
    assert database.rollbacks >= 1


def test_issue_and_story_finalization_roll_back_together(monkeypatch):
    backend, database = postgres_backend()
    monkeypatch.setattr(app, "NEWSLETTER_STORAGE", backend)
    issue = issue_for()
    issue["articles"] = [{
        "story_fingerprint": "story-rollback",
        "title": "Story must roll back with issue",
        "fetched_at": "2026-07-24T08:01:00+00:00",
        "published_at": "2026-07-24T07:30:00+00:00",
    }]
    database.fail_on = "insert into newsletter_story_history"
    try:
        app.persist_finalized_newsletter_issue(issue)
    except RuntimeError as error:
        assert str(error) == "newsletter_issue_persistence_failed"
    else:
        raise AssertionError("bundled persistence failure should be reported")
    assert database.tables["issues"] == {}
    assert database.tables["stories"] == {}


def test_database_unavailability_never_falls_back_to_filesystem():
    def unavailable(*args, **kwargs):
        raise OSError("connection refused at secret-host")

    backend = PostgresNewsletterStorage(
        "postgresql://secret-user:secret-pass@secret-host/database",
        connector=unavailable,
    )
    assert backend.initialize_schema() is False
    assert backend.load_state("issues") == {"issues": {}, "latest_issue_id": ""}
    health = backend.health()
    assert health["persistence_backend"] == "postgresql"
    assert health["database_configured"] is True
    assert health["database_reachable"] is False
    assert health["database_schema_ready"] is False
    assert health["persistence_status"] == "degraded"
    assert "secret" not in json.dumps(health)


def test_database_unavailability_serves_cached_finalized_issue(monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError("unavailable")

    backend = PostgresNewsletterStorage(
        "postgresql://configured",
        connector=unavailable,
    )
    monkeypatch.setattr(app, "NEWSLETTER_STORAGE", backend)
    monkeypatch.setitem(app.WEEKLY_NEWSLETTER_ISSUE_CACHE, "issue", issue_for())
    result = app.load_or_generate_latest_newsletter_issue(
        now=datetime(2026, 7, 24, 10, 0, tzinfo=LONDON)
    )
    assert result["metadata"]["issue_id"] == "stockradar-weekly-2026-W30"
    assert backend.health()["persistence_status"] == "degraded"


def test_degraded_ephemeral_backend_blocks_new_generation(monkeypatch):
    backend = DegradedNewsletterStorage()
    monkeypatch.setattr(app, "NEWSLETTER_STORAGE", backend)
    monkeypatch.setitem(app.WEEKLY_NEWSLETTER_ISSUE_CACHE, "issue", None)
    try:
        app.build_weekly_newsletter_issue(
            now=datetime(2026, 7, 24, 10, 0, tzinfo=LONDON)
        )
    except RuntimeError as error:
        assert str(error) == "newsletter_persistence_degraded"
    else:
        raise AssertionError("ephemeral production generation must be blocked")


def test_scheduler_reports_persistence_degraded_without_generation(monkeypatch):
    monkeypatch.setattr(app, "NEWSLETTER_STORAGE", DegradedNewsletterStorage())
    with patch.object(app, "build_weekly_newsletter_issue") as generate:
        result = app.run_due_newsletter_automation(
            now=datetime(2026, 7, 24, 10, 0, tzinfo=LONDON)
        )
    assert result["status"] == "persistence_degraded"
    assert result["generation_status"] == "blocked"
    generate.assert_not_called()


def test_startup_catch_up_requests_latest_issue(monkeypatch):
    monkeypatch.setattr(app, "newsletter_auto_send_due", lambda now=None: True)
    with patch.object(app, "load_or_generate_latest_newsletter_issue") as generate:
        app.newsletter_startup_catch_up_once()
    generate.assert_called_once_with()


def test_latest_route_simplifies_persisted_issue_without_mutation(monkeypatch):
    backend, database = postgres_backend()
    monkeypatch.setattr(app, "NEWSLETTER_STORAGE", backend)
    issue = issue_for()
    issue["metadata"].update({
        "generated_at_label": "24 July 2026 at 09:01 BST",
        "issue_date_label": "Friday 24 July 2026",
        "issue_status": "Final issue",
        "issue_status_message": "Finalized Friday-to-Friday issue",
        "generation_status": "finalized",
    })
    issue["draft"].update({
        "issue_date_label": "Friday 24 July 2026",
        "issue_status": "Final",
        "issue_status_message": "Finalized Friday-to-Friday issue",
        "last_refreshed": "24 July 2026",
        "preview_refresh_note": "Internal preview refresh note.",
        "opening_line": "Your Friday-to-Friday market brief is ready.",
        "opening_note": (
            "This issue covers verified developments from "
            "2026-07-17T09:00:00+01:00 up to, but not including, "
            "2026-07-24T09:00:00+01:00."
        ),
        "market_mood": "Mixed",
        "market_pulse": (
            "8 tracked instruments rose and 9 fell between the two Friday "
            "cutoffs; 17 had comparable verified prices."
        ),
        "market_week_summary": (
            "Verified developments inside the reporting window included: "
            "Example weekly headline."
        ),
        "what_looked_strong": [{
            "name": "Example plc",
            "weekly_change_label": "+5.0%",
            "sector": "Technology",
            "reason": "Friday-to-Friday change +5.0% from 100.00 to 105.00.",
        }],
        "what_looked_weak": [],
        "market_tracker": [],
        "signal_watch": {"changes": []},
        "trending_vs_forecasting": {"trending": [], "forecasting": []},
        "investor_lesson": "Lesson",
        "risk_check": [],
        "disclaimer": "Educational only.",
        "premium_note": "",
    })
    app.persist_finalized_newsletter_issue(issue)
    persisted_issue = copy.deepcopy(
        database.tables["issues"]["stockradar-weekly-2026-W30"]
    )
    with (
        patch.object(
            app,
            "load_or_generate_latest_newsletter_issue",
            side_effect=lambda: backend.load_issue_by_id(
                "stockradar-weekly-2026-W30"
            ),
        ) as generate,
        patch.object(
            app,
            "get_recommendations",
            return_value=[
                {
                    "ticker": "BUY2",
                    "signal": "BUY",
                    "confidence": "91%",
                    "reason": "Highest current BUY context.",
                },
                {
                    "ticker": "HOLD1",
                    "signal": "HOLD",
                    "confidence": "82%",
                    "reason": "Highest current HOLD context.",
                },
                {
                    "ticker": "SELL2",
                    "signal": "SELL",
                    "confidence": "79%",
                    "reason": "Highest current SELL context.",
                },
            ],
        ),
    ):
        response = app.app.test_client().get("/newsletter/latest")
    assert response.status_code == 200
    rendered = response.get_data(as_text=True)
    title_position = rendered.index("<h1>StockRadar Weekly</h1>")
    opening_position = rendered.index("Your Friday market brief is ready")
    updated_position = rendered.index(
        "Last updated: 24 July 2026 at 09:01 BST"
    )
    mood_position = rendered.index("Market mood:</strong> Mixed.")
    pulse_position = rendered.index("8 tracked instruments rose and 9 fell")
    summary_position = rendered.index(
        "Verified developments this week included: "
        "Example weekly headline."
    )
    assert (
        title_position
        < opening_position
        < updated_position
        < mood_position
        < pulse_position
        < summary_position
    )
    assert "Weekly change: +5.0%" in rendered
    assert "weekly change +5.0% from 100.00 to 105.00." in rendered
    assert (
        "No tracked signals changed this week. Current signals to watch:"
        in rendered
    )
    assert "BUY2" in rendered and "BUY — 91%" in rendered
    assert "HOLD1" in rendered and "HOLD — 82%" in rendered
    assert "SELL2" in rendered and "SELL — 79%" in rendered
    assert "No verified signal changes were available" not in rendered
    for unwanted in (
        "Friday-to-Friday",
        "Issue date:",
        "Issue status",
        "Final issue",
        "Generation status",
        "Internal preview refresh note",
        "This issue covers verified developments",
        "Reporting window",
        "reporting window",
        "2026-07-17T09:00:00+01:00 up to, but not including, "
        "2026-07-24T09:00:00+01:00",
    ):
        assert unwanted not in rendered
    assert (
        database.tables["issues"]["stockradar-weekly-2026-W30"]
        == persisted_issue
    )
    generate.assert_called_once_with()


def test_database_generation_lock_serializes_scheduler_and_route(
    monkeypatch,
):
    backend, database = postgres_backend()
    monkeypatch.setattr(app, "NEWSLETTER_STORAGE", backend)
    monkeypatch.setattr(app, "PERSISTENCE_BACKEND", "postgresql")
    app.WEEKLY_NEWSLETTER_ISSUE_CACHE["issue"] = None
    now = datetime(2026, 7, 24, 10, 0, tzinfo=LONDON)
    calls = {"count": 0}

    def slow_generation(now=None, force_refresh=False):
        calls["count"] += 1
        time.sleep(0.1)
        return app.persist_finalized_newsletter_issue(issue_for())

    monkeypatch.setattr(
        app,
        "_build_weekly_newsletter_issue_without_generation_lock",
        slow_generation,
    )
    results = []

    def generate():
        results.append(app.build_weekly_newsletter_issue(now=now))

    scheduler = threading.Thread(target=generate)
    route = threading.Thread(target=generate)
    scheduler.start()
    route.start()
    scheduler.join()
    route.join()
    assert calls["count"] == 1
    assert len(results) == 2
    assert len(database.tables["issues"]) == 1


def test_legacy_json_migration_is_one_time_and_non_destructive(
    monkeypatch,
    tmp_path,
):
    backend, database = postgres_backend()
    legacy_root = tmp_path / "legacy"
    data_root = tmp_path / "data"
    legacy_root.mkdir()
    data_root.mkdir()
    source = legacy_root / "newsletter_issues.json"
    database.tables["issues"]["stockradar-weekly-2026-W29"] = issue_for(
        "database-newer"
    )
    source.write_text(
        json.dumps({
            "issues": {
                "stockradar-weekly-2026-W29": issue_for("legacy"),
            },
            "latest_issue_id": "stockradar-weekly-2026-W29",
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(app, "APP_ROOT", str(legacy_root))
    monkeypatch.setattr(app, "STOCKRADAR_DATA_DIR", str(data_root))
    monkeypatch.setattr(app, "NEWSLETTER_STORAGE", backend)

    first = app.migrate_selected_newsletter_storage()
    second = app.migrate_selected_newsletter_storage()
    assert first["status"] == "completed"
    assert second["status"] == "already_completed"
    assert source.exists()
    assert len(database.tables["issues"]) == 1
    assert database.tables["issues"][
        "stockradar-weekly-2026-W29"
    ]["metadata"]["marker"] == "database-newer"
    assert backend.migration_key in database.tables["migrations"]


def test_health_reports_database_and_catch_up_without_secrets(
    monkeypatch,
):
    backend, _ = postgres_backend()
    monkeypatch.setattr(app, "NEWSLETTER_STORAGE", backend)
    monkeypatch.setattr(app, "PERSISTENCE_BACKEND", "postgresql")
    monkeypatch.setattr(
        app,
        "DATABASE_URL",
        "postgresql://secret-user:secret-pass@secret-host/database",
    )
    monkeypatch.setattr(
        app,
        "newsletter_london_now",
        lambda now=None: datetime(2026, 7, 24, 10, 0, tzinfo=LONDON),
    )
    newsletter = app.app.test_client().get("/health").get_json()["newsletter"]
    assert newsletter["persistence_backend"] == "postgresql"
    assert newsletter["database_configured"] is True
    assert newsletter["database_reachable"] is True
    assert newsletter["database_schema_ready"] is True
    assert newsletter["catch_up_required"] is True
    assert newsletter["latest_completed_issue_id"] == "stockradar-weekly-2026-W30"
    serialized = json.dumps(newsletter)
    assert "secret-user" not in serialized
    assert "secret-pass" not in serialized
    assert "secret-host" not in serialized
    assert "/Users/" not in serialized
