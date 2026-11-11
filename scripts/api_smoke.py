"""Smoke-test a running Campaign Forge API (stdlib only; used by CI against the Docker image).

    python scripts/api_smoke.py http://127.0.0.1:8000 <api-key>

Checks: /health, a 401 without a key, system status, then a simulated ad-pack
campaign from start to completion. Run it against a server started with
CF_PROVIDER=fake, so no provider quota is used.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any

BRIEF = {
    "product_name": "EcoSip Reusable Bottle",
    "product_description": "An insulated bottle that keeps drinks cold for 24 hours.",
    "target_audience": "eco-conscious young professionals who hike at weekends",
    "goal": "drive summer sales",
    "tone": "fresh, practical",
    "channels": ["instagram", "email"],
}


def call(base: str, path: str, key: str | None = None, body: Any = None) -> tuple[int, Any]:
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-API-Key"] = key
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(  # noqa: S310 - the URL is the caller's own server
        base + path, data=data, headers=headers, method="POST" if data else "GET"
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


def main(base: str, key: str) -> int:
    for _ in range(30):
        try:
            if call(base, "/health")[0] == 200:
                break
        except OSError:
            pass
        time.sleep(2)
    checks: list[tuple[str, bool]] = []
    status, health = call(base, "/health")
    checks.append(("health", status == 200 and health["status"] == "ok"))
    checks.append(("401 without a key", call(base, "/api/v1/agents")[0] == 401))
    status, system = call(base, "/api/v1/system/status", key)
    checks.append(("system status", status == 200 and system["auth"] == "api_key"))
    status, started = call(base, "/api/v1/campaigns", key, {"brief": BRIEF, "workflow": "ad-pack"})
    checks.append(("campaign accepted", status == 202))
    final = None
    if status == 202:
        for _ in range(60):
            final = call(base, f"/api/v1/campaigns/{started['id']}", key)[1]
            if final["status"] not in ("starting", "pending", "planning", "running"):
                break
            time.sleep(1)
    checks.append(("campaign completed", bool(final) and final["status"] == "completed"))
    for name, ok in checks:
        print(f"{'ok  ' if ok else 'FAIL'} {name}")
    if final:
        usage = final.get("usage") or {}
        print(
            f"campaign {final['id']}: {final['status']}, simulated={final['simulated']}, "
            f"calls={usage.get('llm_calls')}"
        )
    return 0 if all(ok for _, ok in checks) else 1


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1].rstrip("/"), sys.argv[2]))
