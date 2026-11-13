"""Compare compiled-template caching with Flask's original inline renderer.

Same process, same dashboard data and request/security paths; no provider calls.
These timings are local CPU measurements, not production latency.
"""
import json
from pathlib import Path
import statistics
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from flask import render_template_string as original_render


def measure(renderer):
    data = app.prepare_dashboard_data(include_market_snapshots=False, local_only=True)
    results = []
    with patch.object(app, "render_template_string", renderer), patch.object(
        app, "get_cached_dashboard_data", return_value=data
    ):
        client = app.app.test_client()
        for _ in range(3):
            assert client.get("/").status_code == 200
        for _ in range(30):
            start = time.perf_counter()
            assert client.get("/").status_code == 200
            results.append((time.perf_counter() - start) * 1000)
    return {"samples": 30, "median_ms": round(statistics.median(results), 3),
            "p95_ms": round(sorted(results)[28], 3)}


print(json.dumps({"scope": "local controlled CPU benchmark",
                  "original_inline_renderer": measure(original_render),
                  "cached_compiled_renderer": measure(app.render_template_string)}, indent=2))
