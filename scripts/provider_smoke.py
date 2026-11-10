"""Manual live smoke test for the configured LLM providers.

This script is NOT part of CI (live calls never are; see ADR-017). Run it by hand
after changing provider profiles, or when re-verifying free-tier facts.

Usage::

    python scripts/provider_smoke.py                 # dry run: show what would be checked
    python scripts/provider_smoke.py --live          # health check + one tiny completion each
    python scripts/provider_smoke.py --live --models # also list the models each key can use

Only providers that have a key configured are contacted. Keys are never printed.
Each live check spends a few tokens of that provider's free quota.
"""

from __future__ import annotations

import argparse
import sys

from campaign_forge.config import Settings
from campaign_forge.exceptions import CampaignForgeError
from campaign_forge.providers import (
    BUILTIN_PROFILES,
    CompletionRequest,
    OpenAICompatibleProvider,
    ProviderRouter,
)

_SKIP = {"fake", "custom"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke-test configured LLM providers.")
    parser.add_argument("--live", action="store_true", help="Actually call the providers.")
    parser.add_argument("--models", action="store_true", help="List available models (live).")
    args = parser.parse_args(argv)

    settings = Settings()
    router = ProviderRouter(settings)
    failures = 0
    for name, profile in sorted(BUILTIN_PROFILES.items()):
        if name in _SKIP or profile.api_key_field is None:
            continue
        if not settings.secret(profile.api_key_field):
            print(f"[skip] {name}: {profile.api_key_env} not set")
            continue
        if not profile.free_tier and settings.free_only:
            print(f"[skip] {name}: not free-tier eligible (FREE_ONLY)")
            continue
        try:
            route = router.resolve(provider_preference=name)
        except CampaignForgeError as exc:
            print(f"[skip] {name}: {exc}")
            continue
        if not args.live:
            print(f"[dry-run] {name}: would check model {route.model!r}")
            continue
        try:
            provider = router.provider(name, route)
            health = provider.health_check()
            print(f"[{'ok' if health.ok else 'FAIL'}] {name} health: {health.detail}")
            if args.models and isinstance(provider, OpenAICompatibleProvider):
                print(f"       models: {', '.join(provider.list_models()[:20])}")
            response = provider.complete(
                CompletionRequest.from_prompts(
                    "Reply with exactly one word.", "Say OK.", model=route.model, max_tokens=5
                )
            )
            print(
                f"[ok] {name}/{response.model}: {response.text!r} "
                f"({response.usage.total_tokens} tokens, {response.latency_s:.2f}s)"
            )
        except CampaignForgeError as exc:
            failures += 1
            print(f"[FAIL] {name}: {type(exc).__name__}: {exc}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
