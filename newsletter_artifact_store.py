"""Durable storage backends for published newsletter artifacts."""

from contextlib import contextmanager
import json
import os
import re
import tempfile
import threading

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows/local compatibility.
    fcntl = None


class PublishedArtifactStoreError(RuntimeError):
    """A safe, credential-free published-artifact storage error."""


class PublishedArtifactStoreConfigurationError(PublishedArtifactStoreError):
    pass


_FILESYSTEM_LOCKS = {}
_FILESYSTEM_LOCKS_GUARD = threading.Lock()


def _bounded_json(payload, max_bytes, error_code):
    if not isinstance(payload, bytes) or len(payload) > max_bytes:
        raise PublishedArtifactStoreError("artifact_too_large")
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, TypeError) as error:
        raise PublishedArtifactStoreError(error_code) from error
    if not isinstance(decoded, dict):
        raise PublishedArtifactStoreError(error_code)
    return decoded


def _sort_key(pointer_payload, max_bytes):
    pointer = _bounded_json(
        pointer_payload,
        max_bytes,
        "artifact_pointer_invalid",
    )
    return str(pointer.get("sort_key") or "")


class FilesystemPublishedArtifactStore:
    """Filesystem backend for local development and tests only."""

    identifier = "filesystem"

    def __init__(self, root, max_bytes=8 * 1024 * 1024):
        self.root = os.path.realpath(str(root))
        self.max_bytes = int(max_bytes)

    @property
    def latest_location(self):
        return os.path.join(self.root, "latest.json")

    def issue_locations(self, issue_id):
        return {
            "html": os.path.join(self.root, "issues", f"{issue_id}.html"),
            "json": os.path.join(self.root, "issues", f"{issue_id}.json"),
        }

    def _read(self, path, error_code):
        try:
            with open(path, "rb") as handle:
                payload = handle.read(self.max_bytes + 1)
        except (FileNotFoundError, OSError) as error:
            raise PublishedArtifactStoreError(error_code) from error
        if len(payload) > self.max_bytes:
            raise PublishedArtifactStoreError("artifact_too_large")
        return payload

    @staticmethod
    def _fsync_directory(directory):
        try:
            descriptor = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            pass

    def _write_immutable(self, path, payload):
        if len(payload) > self.max_bytes:
            raise PublishedArtifactStoreError("artifact_too_large")
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        try:
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            existing = self._read(path, "artifact_existing_read_failed")
            if existing != payload:
                raise PublishedArtifactStoreError(
                    "artifact_immutable_conflict"
                )
            return False

        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            self._fsync_directory(os.path.dirname(path))
            return True
        except Exception:
            try:
                os.remove(path)
            except OSError:
                pass
            raise

    def _write_pointer(self, payload):
        directory = os.path.dirname(self.latest_location)
        os.makedirs(directory, mode=0o700, exist_ok=True)
        descriptor, temp_path = tempfile.mkstemp(
            prefix=".latest-newsletter.",
            suffix=".tmp",
            dir=directory,
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.latest_location)
            temp_path = ""
            self._fsync_directory(directory)
        finally:
            if temp_path:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    @contextmanager
    def _pointer_lock(self):
        lock_path = f"{self.latest_location}.lock"
        os.makedirs(os.path.dirname(lock_path), mode=0o700, exist_ok=True)
        with _FILESYSTEM_LOCKS_GUARD:
            thread_lock = _FILESYSTEM_LOCKS.setdefault(
                lock_path,
                threading.Lock(),
            )
        with thread_lock:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                if fcntl is not None:
                    fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    def publish(self, issue_id, html_payload, json_payload, pointer_payload):
        locations = self.issue_locations(issue_id)
        with self._pointer_lock():
            self._write_immutable(locations["html"], html_payload)
            self._write_immutable(locations["json"], json_payload)
            current_payload = None
            if os.path.exists(self.latest_location):
                current_payload = self._read(
                    self.latest_location,
                    "artifact_pointer_read_failed",
                )
            latest_updated = (
                current_payload is None
                or _sort_key(pointer_payload, self.max_bytes)
                >= _sort_key(current_payload, self.max_bytes)
            )
            if latest_updated:
                self._write_pointer(pointer_payload)
        return latest_updated

    def read_latest_pointer(self):
        return self._read(
            self.latest_location,
            "artifact_pointer_unavailable",
        )

    def read_issue_html(self, issue_id):
        return self._read(
            self.issue_locations(issue_id)["html"],
            "artifact_html_unavailable",
        )

    def read_issue_json(self, issue_id):
        return self._read(
            self.issue_locations(issue_id)["json"],
            "artifact_json_unavailable",
        )


def _error_response(error):
    response = getattr(error, "response", None)
    return response if isinstance(response, dict) else {}


def _error_code(error):
    response = _error_response(error)
    details = response.get("Error")
    if isinstance(details, dict):
        return str(details.get("Code") or "")
    return ""


def _error_status(error):
    response = _error_response(error)
    metadata = response.get("ResponseMetadata")
    if isinstance(metadata, dict):
        try:
            return int(metadata.get("HTTPStatusCode") or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def _is_precondition_failed(error):
    return _error_status(error) == 412 or _error_code(error) in {
        "PreconditionFailed",
        "ConditionalRequestConflict",
    }


def _is_not_found(error):
    return _error_status(error) == 404 or _error_code(error) in {
        "404",
        "NoSuchKey",
        "NotFound",
    }


class R2PublishedArtifactStore:
    """Cloudflare R2 backend using its S3-compatible API."""

    identifier = "r2"

    def __init__(
        self,
        client,
        bucket,
        prefix="stockradar/newsletters",
        max_bytes=8 * 1024 * 1024,
        pointer_attempts=5,
    ):
        self.client = client
        self.bucket = str(bucket)
        self.prefix = str(prefix or "").strip("/")
        self.max_bytes = int(max_bytes)
        self.pointer_attempts = max(1, int(pointer_attempts))
        self._register_conditional_header_support()

    def _register_conditional_header_support(self):
        events = getattr(getattr(self.client, "meta", None), "events", None)
        if events is None or getattr(
            self.client,
            "_stockradar_conditional_headers_registered",
            False,
        ):
            return

        def process_custom_arguments(params, context, **kwargs):
            custom_headers = params.pop("custom_headers", None)
            if custom_headers:
                context["stockradar_custom_headers"] = custom_headers

        def add_custom_headers(params, context, **kwargs):
            custom_headers = context.get("stockradar_custom_headers")
            if custom_headers:
                params["headers"].update(custom_headers)

        events.register(
            "before-parameter-build.s3.PutObject",
            process_custom_arguments,
        )
        events.register("before-call.s3.PutObject", add_custom_headers)
        self.client._stockradar_conditional_headers_registered = True

    def _key(self, relative_key):
        relative_key = str(relative_key or "").lstrip("/")
        return f"{self.prefix}/{relative_key}" if self.prefix else relative_key

    @property
    def latest_key(self):
        return self._key("latest.json")

    def issue_keys(self, issue_id):
        return {
            "html": self._key(f"issues/{issue_id}.html"),
            "json": self._key(f"issues/{issue_id}.json"),
        }

    def _get_optional(self, key, error_code):
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except Exception as error:
            if _is_not_found(error):
                return None
            raise PublishedArtifactStoreError(
                "artifact_store_unavailable"
            ) from error

        try:
            content_length = int(response.get("ContentLength") or 0)
        except (TypeError, ValueError):
            content_length = 0
        if content_length > self.max_bytes:
            raise PublishedArtifactStoreError("artifact_too_large")

        body = response.get("Body")
        try:
            payload = body.read(self.max_bytes + 1)
        except Exception as error:
            raise PublishedArtifactStoreError(
                "artifact_store_unavailable"
            ) from error
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()
        if not isinstance(payload, bytes):
            payload = bytes(payload)
        if len(payload) > self.max_bytes:
            raise PublishedArtifactStoreError("artifact_too_large")
        return payload, str(response.get("ETag") or "")

    def _get_required(self, key, error_code):
        result = self._get_optional(key, error_code)
        if result is None:
            raise PublishedArtifactStoreError(error_code)
        return result

    def _put_conditional(self, key, payload, content_type, headers):
        if len(payload) > self.max_bytes:
            raise PublishedArtifactStoreError("artifact_too_large")
        try:
            return self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=payload,
                ContentType=content_type,
                CacheControl="no-cache",
                custom_headers=headers,
            )
        except Exception as error:
            if _is_precondition_failed(error):
                raise PublishedArtifactStoreError(
                    "artifact_precondition_failed"
                ) from error
            raise PublishedArtifactStoreError(
                "artifact_store_unavailable"
            ) from error

    def _write_immutable(self, key, payload, content_type):
        try:
            self._put_conditional(
                key,
                payload,
                content_type,
                {"If-None-Match": "*"},
            )
            return True
        except PublishedArtifactStoreError as error:
            if str(error) != "artifact_precondition_failed":
                raise
        existing, _etag = self._get_required(
            key,
            "artifact_existing_read_failed",
        )
        if existing != payload:
            raise PublishedArtifactStoreError("artifact_immutable_conflict")
        return False

    def publish(self, issue_id, html_payload, json_payload, pointer_payload):
        issue_keys = self.issue_keys(issue_id)
        self._write_immutable(issue_keys["html"], html_payload, "text/html")
        self._write_immutable(
            issue_keys["json"],
            json_payload,
            "application/json",
        )
        requested_sort_key = _sort_key(pointer_payload, self.max_bytes)

        for _attempt in range(self.pointer_attempts):
            current = self._get_optional(
                self.latest_key,
                "artifact_pointer_read_failed",
            )
            if current is None:
                headers = {"If-None-Match": "*"}
            else:
                current_payload, current_etag = current
                if requested_sort_key < _sort_key(
                    current_payload,
                    self.max_bytes,
                ):
                    return False
                if not current_etag:
                    raise PublishedArtifactStoreError(
                        "artifact_pointer_etag_missing"
                    )
                headers = {"If-Match": current_etag}
            try:
                self._put_conditional(
                    self.latest_key,
                    pointer_payload,
                    "application/json",
                    headers,
                )
                return True
            except PublishedArtifactStoreError as error:
                if str(error) != "artifact_precondition_failed":
                    raise
        raise PublishedArtifactStoreError("artifact_pointer_update_conflict")

    def read_latest_pointer(self):
        payload, _etag = self._get_required(
            self.latest_key,
            "artifact_pointer_unavailable",
        )
        return payload

    def read_issue_html(self, issue_id):
        payload, _etag = self._get_required(
            self.issue_keys(issue_id)["html"],
            "artifact_html_unavailable",
        )
        return payload

    def read_issue_json(self, issue_id):
        payload, _etag = self._get_required(
            self.issue_keys(issue_id)["json"],
            "artifact_json_unavailable",
        )
        return payload


def _valid_r2_prefix(prefix):
    parts = str(prefix or "").strip("/").split("/")
    return all(part not in {"", ".", ".."} for part in parts)


def build_published_artifact_store(
    *,
    production,
    backend,
    filesystem_root,
    max_bytes,
    account_id="",
    access_key_id="",
    secret_access_key="",
    bucket="",
    prefix="stockradar/newsletters",
    jurisdiction="",
    client=None,
):
    """Build a backend without ever falling back from R2 in production."""

    backend = str(backend or "").strip().lower()
    if production and not backend:
        raise PublishedArtifactStoreConfigurationError(
            "artifact_backend_not_configured"
        )
    if not backend:
        backend = "filesystem"
    if backend == "filesystem":
        if production:
            raise PublishedArtifactStoreConfigurationError(
                "artifact_filesystem_forbidden_in_production"
            )
        return FilesystemPublishedArtifactStore(
            filesystem_root,
            max_bytes=max_bytes,
        )
    if backend != "r2":
        raise PublishedArtifactStoreConfigurationError(
            "artifact_backend_invalid"
        )

    account_id = str(account_id or "").strip().lower()
    access_key_id = str(access_key_id or "").strip()
    secret_access_key = str(secret_access_key or "")
    bucket = str(bucket or "").strip()
    prefix = str(prefix or "").strip("/")
    jurisdiction = str(jurisdiction or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{32}", account_id):
        raise PublishedArtifactStoreConfigurationError(
            "artifact_r2_account_id_invalid"
        )
    if not access_key_id or not secret_access_key:
        raise PublishedArtifactStoreConfigurationError(
            "artifact_r2_credentials_missing"
        )
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
        raise PublishedArtifactStoreConfigurationError(
            "artifact_r2_bucket_invalid"
        )
    if not _valid_r2_prefix(prefix):
        raise PublishedArtifactStoreConfigurationError(
            "artifact_r2_prefix_invalid"
        )
    if jurisdiction not in {"", "eu", "fedramp", "us"}:
        raise PublishedArtifactStoreConfigurationError(
            "artifact_r2_jurisdiction_invalid"
        )

    if client is None:
        try:
            import boto3
            from botocore.config import Config
        except ImportError as error:
            raise PublishedArtifactStoreConfigurationError(
                "artifact_r2_dependency_missing"
            ) from error
        jurisdiction_label = f"{jurisdiction}." if jurisdiction else ""
        endpoint_url = (
            f"https://{account_id}.{jurisdiction_label}"
            "r2.cloudflarestorage.com"
        )
        client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
            config=Config(
                signature_version="s3v4",
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=5,
                read_timeout=10,
            ),
        )
    return R2PublishedArtifactStore(
        client,
        bucket,
        prefix=prefix,
        max_bytes=max_bytes,
    )
