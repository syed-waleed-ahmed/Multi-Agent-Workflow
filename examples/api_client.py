"""Start a campaign over the HTTP API and print its report when it finishes.

    CF_PROVIDER=fake CF_API_KEYS=dev-key-0123456789 python -m campaign_forge.api
    python examples/api_client.py http://127.0.0.1:8000 dev-key-0123456789

With CF_PROVIDER=fake the run is simulated and needs no provider key. Against a
real free-tier provider a full campaign takes several minutes, because the run
waits out rate limits; this client simply keeps polling.
"""

from __future__ import annotations

import sys
import time

import httpx

BRIEF = {
    "product_name": "EcoSip Reusable Bottle",
    "product_description": "An insulated bottle that keeps drinks cold for 24 hours.",
    "target_audience": "eco-conscious young professionals who hike at weekends",
    "goal": "drive summer sales",
    "tone": "fresh, practical",
    "channels": ["instagram", "email", "google ads"],
}
FINISHED = {"completed", "failed", "cancelled", "paused", "waiting_for_approval"}


def main(base_url: str, api_key: str) -> int:
    with httpx.Client(base_url=base_url, headers={"X-API-Key": api_key}, timeout=30) as api:
        started = api.post("/api/v1/campaigns", json={"brief": BRIEF, "workflow": "full-campaign"})
        started.raise_for_status()
        campaign_id = started.json()["id"]
        print(f"Started campaign {campaign_id}")
        while True:
            campaign = api.get(f"/api/v1/campaigns/{campaign_id}").json()
            done = sum(1 for s in campaign["steps"] if s["status"] not in ("pending", "running"))
            print(f"  {campaign['status']}: {done}/{len(campaign['steps'])} steps")
            if campaign["status"] in FINISHED:
                break
            time.sleep(3)
    if campaign["status"] != "completed":
        print(f"Stopped: {campaign['status']} ({campaign.get('status_reason')})")
        return 1
    if campaign["simulated"]:
        print("SIMULATED: offline demo responder output, not a real model.")
    print(campaign["report_markdown"])
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1], sys.argv[2]))
