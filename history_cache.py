"""Bounded, atomic JSON price-history persistence. Never deserialize executable objects."""
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile

import pandas as pd

MAX_BYTES = 8 * 1024 * 1024


def cache_path(directory, key):
    return Path(directory) / (hashlib.sha256(repr(key).encode()).hexdigest() + ".json")


def load_history(directory, key, now):
    path = cache_path(directory, key)
    try:
        if path.stat().st_size > MAX_BYTES:
            return None
        value = json.loads(path.read_text())
        stamp = float(value["timestamp"])
        if not 0 <= now - stamp <= 30 * 86400:
            return None
        history = pd.read_json(io.StringIO(value["frame"]), orient="split")
        history.index = pd.to_datetime(history.index, utc=True)
        history.attrs = value.get("attrs") or {}
        return {"timestamp": stamp, "history": history, "ttl": value.get("ttl", 300)}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_history(directory, key, entry, limit=256):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    history = entry["history"]
    payload = json.dumps({"timestamp": entry["timestamp"], "ttl": entry.get("ttl", 300),
                          "attrs": history.attrs,
                          "frame": history.to_json(orient="split", date_format="iso")}, allow_nan=False)
    if len(payload.encode()) > MAX_BYTES:
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=directory, suffix=".tmp", delete=False) as handle:
            temporary = handle.name
            handle.write(payload)
        os.replace(temporary, cache_path(directory, key))
        temporary = None
        files = sorted(directory.glob("*.json"), key=lambda file: file.stat().st_mtime, reverse=True)
        for path in files[limit:]:
            path.unlink(missing_ok=True)
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)
