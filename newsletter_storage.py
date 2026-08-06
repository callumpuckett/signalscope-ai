from contextlib import contextmanager
from datetime import datetime, timezone
import copy
import hashlib
import json

try:
    import psycopg
    from psycopg.types.json import Jsonb
except ImportError:
    psycopg = None

    def Jsonb(value):
        return value


STORE_DEFAULTS = {
    "issues": {"issues": {}, "latest_issue_id": ""},
    "story_history": {"stories": {}},
    "market_snapshots": {"snapshots": {}},
    "delivery": {"deliveries": [], "runs": []},
    "beehiiv": {"issues": {}},
    "subscribers": {"subscribers": []},
    "premium_entitlements": {"records": []},
    "rate_limits": {"buckets": {}},
    "turnstile_tokens": {"tokens": {}},
}


APPLICATION_STATE_STORES = {
    "premium_entitlements",
    "rate_limits",
    "turnstile_tokens",
}


POSTGRES_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS newsletter_issues (
        issue_id TEXT PRIMARY KEY,
        issue_date DATE,
        iso_year INTEGER,
        iso_week INTEGER,
        generated_at TIMESTAMPTZ,
        finalized_at TIMESTAMPTZ,
        payload JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS newsletter_issues_finalized_at_idx
    ON newsletter_issues (finalized_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS newsletter_story_history (
        story_fingerprint TEXT PRIMARY KEY,
        first_seen_at TIMESTAMPTZ,
        last_seen_at TIMESTAMPTZ,
        payload JSONB NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS newsletter_story_history_last_seen_idx
    ON newsletter_story_history (last_seen_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS newsletter_market_snapshots (
        snapshot_key TEXT PRIMARY KEY,
        snapshot_id TEXT UNIQUE,
        cutoff_utc TIMESTAMPTZ,
        payload JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS newsletter_market_snapshots_cutoff_idx
    ON newsletter_market_snapshots (cutoff_utc DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS newsletter_delivery_records (
        issue_guid TEXT NOT NULL,
        email TEXT NOT NULL,
        sent_at TIMESTAMPTZ,
        payload JSONB NOT NULL,
        PRIMARY KEY (issue_guid, email)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS newsletter_delivery_records_sent_at_idx
    ON newsletter_delivery_records (sent_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS newsletter_scheduler_runs (
        run_id TEXT PRIMARY KEY,
        completed_at TIMESTAMPTZ,
        payload JSONB NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS newsletter_scheduler_runs_completed_at_idx
    ON newsletter_scheduler_runs (completed_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS newsletter_beehiiv_state (
        issue_key TEXT PRIMARY KEY,
        updated_at TIMESTAMPTZ,
        payload JSONB NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS newsletter_subscribers (
        email TEXT PRIMARY KEY,
        updated_at TIMESTAMPTZ,
        payload JSONB NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS newsletter_storage_migrations (
        migration_key TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        details JSONB NOT NULL,
        completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stockradar_application_state (
        state_key TEXT PRIMARY KEY,
        payload JSONB NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
)


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def state_default(store_name):
    return copy.deepcopy(STORE_DEFAULTS[store_name])


def json_payload(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    return {}


def nullable_timestamp(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return text


def nullable_date(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        datetime.fromisoformat(text).date()
    except ValueError:
        return None
    return text


def nullable_integer(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def stable_payload_id(prefix, payload):
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"{prefix}-{digest}"


def select_backend_identifier(
    database_url,
    production,
    durable_filesystem_configured,
):
    if str(database_url or "").strip():
        return "postgresql"
    if not production or durable_filesystem_configured:
        return "filesystem_json"
    return "degraded_ephemeral"


class NewsletterStorageBackend:
    identifier = "unconfigured"
    durable = False

    def initialize_schema(self):
        return False

    def load_state(self, store_name):
        raise NotImplementedError

    def save_state(self, store_name, data):
        raise NotImplementedError

    def update_state(self, store_name, updater):
        raise NotImplementedError

    def load_issue_by_id(self, issue_id):
        issue = self.load_state("issues").get("issues", {}).get(str(issue_id or ""))
        return issue if isinstance(issue, dict) else None

    def load_latest_issue(self):
        data = self.load_state("issues")
        issues = data.get("issues", {})
        latest = issues.get(data.get("latest_issue_id", ""))
        if (
            isinstance(latest, dict)
            and latest.get("metadata", {}).get("is_final") is True
            and latest.get("metadata", {}).get("status") == "final"
        ):
            return latest
        return max(
            (
                issue for issue in issues.values()
                if isinstance(issue, dict)
                and issue.get("metadata", {}).get("is_final") is True
                and issue.get("metadata", {}).get("status") == "final"
            ),
            key=lambda issue: str(
                issue.get("metadata", {}).get("window_end_utc") or ""
            ),
            default=None,
        )

    def finalize_issue_once(self, issue):
        metadata = issue.get("metadata", {})
        issue_id = str(metadata.get("issue_id") or "")
        result = {"issue": issue, "conflict": False}

        def finalize(data):
            issues = data.setdefault("issues", {})
            existing = issues.get(issue_id)
            if existing:
                if existing.get("metadata", {}).get("status") == "final":
                    result["issue"] = existing
                    return False
                result["conflict"] = True
                return False
            issues[issue_id] = issue
            current_latest = issues.get(data.get("latest_issue_id", ""), {})
            if (
                not current_latest
                or str(metadata.get("window_end_utc") or "")
                >= str(current_latest.get("metadata", {}).get("window_end_utc") or "")
            ):
                data["latest_issue_id"] = issue_id
            return True

        return {
            "stored": self.update_state("issues", finalize),
            "issue": result["issue"],
            "conflict": result["conflict"],
        }

    def load_story_history(self):
        return self.load_state("story_history")

    def update_story_history(self, updater):
        return self.update_state("story_history", updater)

    def load_market_snapshots(self):
        return self.load_state("market_snapshots")

    def update_market_snapshots(self, updater):
        return self.update_state("market_snapshots", updater)

    def load_delivery_state(self):
        return self.load_state("delivery")

    def update_delivery_state(self, updater):
        return self.update_state("delivery", updater)

    def load_beehiiv_state(self):
        return self.load_state("beehiiv")

    def update_beehiiv_state(self, updater):
        return self.update_state("beehiiv", updater)

    def load_subscriber_state(self):
        return self.load_state("subscribers")

    def update_subscriber_state(self, updater):
        return self.update_state("subscribers", updater)

    def acquire_lock(self, lock_key):
        return None

    def release_lock(self, lock_token):
        return None

    def health(self):
        raise NotImplementedError

    def migrate_legacy_documents(self, documents):
        return {
            "status": "unsupported",
            "imported_stores": [],
            "skipped_stores": sorted(documents),
        }


class FilesystemNewsletterStorage(NewsletterStorageBackend):
    identifier = "filesystem_json"
    durable = True

    def __init__(
        self,
        paths,
        loader,
        saver,
        updater,
        lock_acquirer,
        lock_releaser,
        health_provider,
    ):
        self.paths = paths
        self.loader = loader
        self.saver = saver
        self.updater = updater
        self.lock_acquirer = lock_acquirer
        self.lock_releaser = lock_releaser
        self.health_provider = health_provider

    def initialize_schema(self):
        return True

    def _path(self, store_name):
        paths = self.paths() if callable(self.paths) else self.paths
        return paths[store_name]

    def load_state(self, store_name):
        return self.loader(
            self._path(store_name),
            state_default(store_name),
        )

    def save_state(self, store_name, data):
        return self.saver(self._path(store_name), data)

    def update_state(self, store_name, updater):
        return self.updater(
            self._path(store_name),
            state_default(store_name),
            updater,
        )

    def acquire_lock(self, lock_key):
        return self.lock_acquirer(lock_key)

    def release_lock(self, lock_token):
        self.lock_releaser(lock_token)

    def health(self):
        return self.health_provider()


class DegradedNewsletterStorage(NewsletterStorageBackend):
    identifier = "degraded_ephemeral"
    durable = False

    def __init__(self, error_code="durable_persistence_not_configured"):
        self.error_code = error_code

    def load_state(self, store_name):
        return state_default(store_name)

    def save_state(self, store_name, data):
        return False

    def update_state(self, store_name, updater):
        return False

    def health(self):
        return {
            "persistence_backend": self.identifier,
            "database_configured": False,
            "database_reachable": False,
            "database_schema_ready": False,
            "persistence_configured": False,
            "persistence_directory_writable": False,
            "issue_store_available": False,
            "story_history_store_available": False,
            "market_snapshot_store_available": False,
            "persistence_status": "degraded",
            "persistence_last_error": self.error_code,
        }


class PostgresNewsletterStorage(NewsletterStorageBackend):
    identifier = "postgresql"
    durable = True
    migration_key = "phase21b-newsletter-json-v1"

    def __init__(self, database_url, connector=None):
        self.database_url = str(database_url or "").strip()
        self.connector = connector or (psycopg.connect if psycopg is not None else None)
        self.database_reachable = False
        self.database_schema_ready = False
        self.last_error = ""

    def _connect(self):
        if not self.connector:
            self.last_error = "postgres_driver_unavailable"
            raise RuntimeError(self.last_error)
        try:
            connection = self.connector(self.database_url, connect_timeout=5)
            self.database_reachable = True
            self.last_error = ""
            return connection
        except Exception as error:
            self.database_reachable = False
            self.database_schema_ready = False
            self.last_error = "database_unavailable"
            raise RuntimeError(self.last_error) from error

    def _rollback(self, connection):
        try:
            connection.rollback()
        except Exception:
            pass

    def _close(self, connection):
        try:
            connection.close()
        except Exception:
            pass

    def initialize_schema(self):
        connection = None
        try:
            connection = self._connect()
            with connection.cursor() as cursor:
                for statement in POSTGRES_SCHEMA_STATEMENTS:
                    cursor.execute(statement)
            connection.commit()
            self.database_schema_ready = True
            self.last_error = ""
            return True
        except Exception:
            if connection is not None:
                self._rollback(connection)
            if self.last_error != "database_unavailable":
                self.last_error = "database_schema_initialization_failed"
            self.database_schema_ready = False
            return False
        finally:
            if connection is not None:
                self._close(connection)

    def _load_state_with_cursor(self, cursor, store_name):
        data = state_default(store_name)
        if store_name == "issues":
            cursor.execute(
                """
                SELECT issue_id, payload
                FROM newsletter_issues
                ORDER BY finalized_at DESC NULLS LAST, created_at DESC
                """
            )
            for issue_id, payload in cursor.fetchall():
                data["issues"][str(issue_id)] = json_payload(payload)
            data["latest_issue_id"] = next(iter(data["issues"]), "")
        elif store_name == "story_history":
            cursor.execute(
                "SELECT story_fingerprint, payload FROM newsletter_story_history"
            )
            for fingerprint, payload in cursor.fetchall():
                data["stories"][str(fingerprint)] = json_payload(payload)
        elif store_name == "market_snapshots":
            cursor.execute(
                "SELECT snapshot_key, payload FROM newsletter_market_snapshots"
            )
            for snapshot_key, payload in cursor.fetchall():
                data["snapshots"][str(snapshot_key)] = json_payload(payload)
        elif store_name == "delivery":
            cursor.execute(
                """
                SELECT payload FROM newsletter_delivery_records
                ORDER BY sent_at ASC NULLS LAST
                """
            )
            data["deliveries"] = [
                json_payload(row[0]) for row in cursor.fetchall()
            ]
            cursor.execute(
                """
                SELECT payload FROM newsletter_scheduler_runs
                ORDER BY completed_at ASC NULLS LAST
                """
            )
            data["runs"] = [json_payload(row[0]) for row in cursor.fetchall()]
        elif store_name == "beehiiv":
            cursor.execute(
                "SELECT issue_key, payload FROM newsletter_beehiiv_state"
            )
            for issue_key, payload in cursor.fetchall():
                data["issues"][str(issue_key)] = json_payload(payload)
        elif store_name == "subscribers":
            cursor.execute(
                "SELECT payload FROM newsletter_subscribers ORDER BY email"
            )
            data["subscribers"] = [
                json_payload(row[0]) for row in cursor.fetchall()
            ]
        elif store_name in APPLICATION_STATE_STORES:
            cursor.execute(
                """
                SELECT payload
                FROM stockradar_application_state
                WHERE state_key = %s
                """,
                (store_name,),
            )
            row = cursor.fetchone()
            if row:
                payload = json_payload(row[0])
                if isinstance(payload, dict):
                    data = payload
        else:
            raise ValueError("unknown_newsletter_store")
        return data

    def load_state(self, store_name):
        connection = None
        try:
            connection = self._connect()
            with connection.cursor() as cursor:
                data = self._load_state_with_cursor(cursor, store_name)
            connection.commit()
            self.database_schema_ready = True
            self.last_error = ""
            return data
        except Exception:
            if connection is not None:
                self._rollback(connection)
            if self.last_error != "database_unavailable":
                self.last_error = "database_read_failed"
            return state_default(store_name)
        finally:
            if connection is not None:
                self._close(connection)

    def _sync_issues(self, cursor, data):
        for issue_id, issue in data.get("issues", {}).items():
            metadata = issue.get("metadata", {})
            cursor.execute(
                """
                INSERT INTO newsletter_issues (
                    issue_id, issue_date, iso_year, iso_week,
                    generated_at, finalized_at, payload
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (issue_id) DO NOTHING
                """,
                (
                    issue_id,
                    nullable_date(metadata.get("issue_date")),
                    nullable_integer(metadata.get("iso_year")),
                    nullable_integer(metadata.get("iso_week")),
                    nullable_timestamp(metadata.get("generated_at")),
                    nullable_timestamp(metadata.get("finalized_at")),
                    Jsonb(issue),
                ),
            )

    def _sync_story_history(self, cursor, data, overwrite=True):
        conflict_action = (
            """
            DO UPDATE SET
                first_seen_at = COALESCE(
                    newsletter_story_history.first_seen_at,
                    EXCLUDED.first_seen_at
                ),
                last_seen_at = GREATEST(
                    newsletter_story_history.last_seen_at,
                    EXCLUDED.last_seen_at
                ),
                payload = EXCLUDED.payload,
                updated_at = NOW()
            """
            if overwrite
            else "DO NOTHING"
        )
        for fingerprint, story in data.get("stories", {}).items():
            cursor.execute(
                f"""
                INSERT INTO newsletter_story_history (
                    story_fingerprint, first_seen_at, last_seen_at, payload
                )
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (story_fingerprint) {conflict_action}
                """,
                (
                    fingerprint,
                    nullable_timestamp(story.get("first_seen_at")),
                    nullable_timestamp(story.get("last_seen_at")),
                    Jsonb(story),
                ),
            )

    def _sync_market_snapshots(self, cursor, data):
        for snapshot_key, snapshot in data.get("snapshots", {}).items():
            cursor.execute(
                """
                INSERT INTO newsletter_market_snapshots (
                    snapshot_key, snapshot_id, cutoff_utc, payload
                )
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (snapshot_key) DO NOTHING
                """,
                (
                    snapshot_key,
                    snapshot.get("snapshot_id") or None,
                    nullable_timestamp(snapshot.get("cutoff_utc")),
                    Jsonb(snapshot),
                ),
            )

    def _sync_delivery(self, cursor, data):
        for delivery in data.get("deliveries", []):
            issue_guid = str(delivery.get("issue_guid") or "").strip()
            email = str(delivery.get("email") or "").strip().lower()
            if not issue_guid or not email:
                continue
            cursor.execute(
                """
                INSERT INTO newsletter_delivery_records (
                    issue_guid, email, sent_at, payload
                )
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (issue_guid, email) DO NOTHING
                """,
                (
                    issue_guid,
                    email,
                    nullable_timestamp(delivery.get("sent_at")),
                    Jsonb(delivery),
                ),
            )
        for run in data.get("runs", []):
            run_id = str(run.get("run_id") or "").strip() or stable_payload_id(
                "run",
                run,
            )
            cursor.execute(
                """
                INSERT INTO newsletter_scheduler_runs (
                    run_id, completed_at, payload
                )
                VALUES (%s, %s, %s)
                ON CONFLICT (run_id) DO NOTHING
                """,
                (
                    run_id,
                    nullable_timestamp(run.get("completed_at")),
                    Jsonb(dict(run, run_id=run_id)),
                ),
            )

    def _sync_beehiiv(self, cursor, data, overwrite=True):
        conflict_action = (
            """
            DO UPDATE SET
                updated_at = EXCLUDED.updated_at,
                payload = EXCLUDED.payload
            """
            if overwrite
            else "DO NOTHING"
        )
        for issue_key, state in data.get("issues", {}).items():
            cursor.execute(
                f"""
                INSERT INTO newsletter_beehiiv_state (
                    issue_key, updated_at, payload
                )
                VALUES (%s, %s, %s)
                ON CONFLICT (issue_key) {conflict_action}
                """,
                (
                    issue_key,
                    nullable_timestamp(state.get("updated_at")),
                    Jsonb(state),
                ),
            )

    def _sync_subscribers(self, cursor, data, overwrite=True):
        conflict_action = (
            """
            DO UPDATE SET
                updated_at = EXCLUDED.updated_at,
                payload = EXCLUDED.payload
            """
            if overwrite
            else "DO NOTHING"
        )
        for subscriber in data.get("subscribers", []):
            email = str(subscriber.get("email") or "").strip().lower()
            if not email:
                continue
            cursor.execute(
                f"""
                INSERT INTO newsletter_subscribers (
                    email, updated_at, payload
                )
                VALUES (%s, %s, %s)
                ON CONFLICT (email) {conflict_action}
                """,
                (
                    email,
                    nullable_timestamp(subscriber.get("updated_at")),
                    Jsonb(subscriber),
                ),
            )

    def _sync_application_state(self, cursor, store_name, data):
        cursor.execute(
            """
            INSERT INTO stockradar_application_state (
                state_key, payload, updated_at
            )
            VALUES (%s, %s, NOW())
            ON CONFLICT (state_key) DO UPDATE SET
                payload = EXCLUDED.payload,
                updated_at = NOW()
            """,
            (
                store_name,
                Jsonb(data),
            ),
        )

    def _sync_state_with_cursor(self, cursor, store_name, data, overwrite=True):
        if store_name == "issues":
            self._sync_issues(cursor, data)
        elif store_name == "story_history":
            self._sync_story_history(cursor, data, overwrite=overwrite)
        elif store_name == "market_snapshots":
            self._sync_market_snapshots(cursor, data)
        elif store_name == "delivery":
            self._sync_delivery(cursor, data)
        elif store_name == "beehiiv":
            self._sync_beehiiv(cursor, data, overwrite=overwrite)
        elif store_name == "subscribers":
            self._sync_subscribers(cursor, data, overwrite=overwrite)
        elif store_name in APPLICATION_STATE_STORES:
            self._sync_application_state(cursor, store_name, data)
        else:
            raise ValueError("unknown_newsletter_store")

    def save_state(self, store_name, data):
        def replace(current):
            current.clear()
            current.update(copy.deepcopy(data))

        return self.update_state(store_name, replace)

    def update_state(self, store_name, updater):
        connection = None
        try:
            connection = self._connect()
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"newsletter-store:{store_name}",),
                )
                data = self._load_state_with_cursor(cursor, store_name)
                should_write = updater(data)
                if should_write is not False:
                    self._sync_state_with_cursor(cursor, store_name, data)
            connection.commit()
            self.database_schema_ready = True
            self.last_error = ""
            return True
        except Exception:
            if connection is not None:
                self._rollback(connection)
            if self.last_error != "database_unavailable":
                self.last_error = "database_transaction_failed"
            return False
        finally:
            if connection is not None:
                self._close(connection)

    def finalize_issue_once(self, issue):
        metadata = issue.get("metadata", {})
        issue_id = str(metadata.get("issue_id") or "")
        connection = None
        try:
            connection = self._connect()
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"newsletter-finalize:{issue_id}",),
                )
                issue_state = self._load_state_with_cursor(cursor, "issues")
                existing = issue_state.get("issues", {}).get(issue_id)
                if existing:
                    if existing.get("metadata", {}).get("status") != "final":
                        connection.commit()
                        return {
                            "stored": True,
                            "issue": existing,
                            "conflict": True,
                        }
                    finalized_issue = existing
                else:
                    finalized_issue = issue
                    self._sync_issues(
                        cursor,
                        {
                            "issues": {issue_id: issue},
                            "latest_issue_id": issue_id,
                        },
                    )

                story_state = self._load_state_with_cursor(
                    cursor,
                    "story_history",
                )
                now = utc_now_iso()
                stories = story_state.setdefault("stories", {})
                for article in finalized_issue.get("articles", []):
                    fingerprint = str(
                        article.get("story_fingerprint") or ""
                    ).strip()
                    if not fingerprint:
                        continue
                    current = stories.get(fingerprint, {})
                    issue_ids = list(current.get("issue_ids_used_in", []))
                    if issue_id not in issue_ids:
                        issue_ids.append(issue_id)
                    stories[fingerprint] = {
                        "provider": article.get("provider", ""),
                        "provider_article_id": article.get(
                            "provider_article_id",
                            "",
                        ),
                        "title": article.get("title", ""),
                        "normalized_title": article.get(
                            "normalized_title",
                            "",
                        ),
                        "canonical_url": article.get("canonical_url", ""),
                        "normalized_title_hash": article.get(
                            "normalized_title_hash",
                            "",
                        ),
                        "story_fingerprint": fingerprint,
                        "first_seen_at": (
                            current.get("first_seen_at")
                            or article.get("fetched_at")
                            or now
                        ),
                        "last_seen_at": article.get("fetched_at") or now,
                        "issue_ids_used_in": issue_ids,
                        "published_at": article.get("published_at", ""),
                    }
                self._sync_story_history(cursor, story_state)
            connection.commit()
            self.database_schema_ready = True
            self.last_error = ""
            return {
                "stored": True,
                "issue": finalized_issue,
                "conflict": False,
            }
        except Exception:
            if connection is not None:
                self._rollback(connection)
            if self.last_error != "database_unavailable":
                self.last_error = "database_transaction_failed"
            return {
                "stored": False,
                "issue": issue,
                "conflict": False,
            }
        finally:
            if connection is not None:
                self._close(connection)

    def acquire_lock(self, lock_key):
        connection = None
        cursor = None
        try:
            connection = self._connect()
            cursor = connection.cursor()
            cursor.execute(
                "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))",
                (f"newsletter-lock:{lock_key}",),
            )
            row = cursor.fetchone()
            if not row or row[0] is not True:
                cursor.close()
                self._close(connection)
                return None
            return {
                "backend": self.identifier,
                "connection": connection,
                "cursor": cursor,
                "lock_key": lock_key,
            }
        except Exception:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass
            if connection is not None:
                self._close(connection)
            if self.last_error != "database_unavailable":
                self.last_error = "database_lock_failed"
            return None

    def release_lock(self, lock_token):
        if not isinstance(lock_token, dict):
            return
        connection = lock_token.get("connection")
        cursor = lock_token.get("cursor")
        try:
            cursor.execute(
                "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
                (f"newsletter-lock:{lock_token.get('lock_key', '')}",),
            )
            connection.commit()
        except Exception:
            self.last_error = "database_lock_release_failed"
            self._rollback(connection)
        finally:
            try:
                cursor.close()
            except Exception:
                pass
            self._close(connection)

    def migrate_legacy_documents(self, documents):
        connection = None
        imported = []
        try:
            connection = self._connect()
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"newsletter-migration:{self.migration_key}",),
                )
                cursor.execute(
                    """
                    SELECT status, details
                    FROM newsletter_storage_migrations
                    WHERE migration_key = %s
                    """,
                    (self.migration_key,),
                )
                existing = cursor.fetchone()
                if existing:
                    connection.commit()
                    return {
                        "status": "already_completed",
                        "imported_stores": [],
                        "skipped_stores": sorted(documents),
                    }

                for store_name, data in documents.items():
                    if store_name not in STORE_DEFAULTS or not isinstance(data, dict):
                        continue
                    self._sync_state_with_cursor(
                        cursor,
                        store_name,
                        data,
                        overwrite=False,
                    )
                    imported.append(store_name)

                status = "completed" if imported else "no_legacy_data"
                details = {
                    "imported_stores": sorted(imported),
                    "store_count": len(imported),
                }
                cursor.execute(
                    """
                    INSERT INTO newsletter_storage_migrations (
                        migration_key, status, details
                    )
                    VALUES (%s, %s, %s)
                    ON CONFLICT (migration_key) DO NOTHING
                    """,
                    (self.migration_key, status, Jsonb(details)),
                )
            connection.commit()
            self.database_schema_ready = True
            self.last_error = ""
            return {
                "status": status,
                "imported_stores": sorted(imported),
                "skipped_stores": sorted(set(documents) - set(imported)),
            }
        except Exception:
            if connection is not None:
                self._rollback(connection)
            if self.last_error != "database_unavailable":
                self.last_error = "database_migration_failed"
            return {
                "status": "failed",
                "imported_stores": [],
                "skipped_stores": sorted(documents),
            }
        finally:
            if connection is not None:
                self._close(connection)

    def health(self):
        ready = bool(
            self.database_reachable
            and self.database_schema_ready
            and not self.last_error
        )
        return {
            "persistence_backend": self.identifier,
            "database_configured": True,
            "database_reachable": self.database_reachable,
            "database_schema_ready": self.database_schema_ready,
            "persistence_configured": True,
            "persistence_directory_writable": False,
            "issue_store_available": ready,
            "story_history_store_available": ready,
            "market_snapshot_store_available": ready,
            "persistence_status": "ready" if ready else "degraded",
            "persistence_last_error": self.last_error,
        }
