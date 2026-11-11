"""Write the API's OpenAPI schema to docs/openapi.json (a test fails when it is stale).

python scripts/export_openapi.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from campaign_forge.api import create_app  # noqa: E402
from campaign_forge.config import Settings  # noqa: E402
from campaign_forge.services import Platform  # noqa: E402


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(  # type: ignore[call-arg]
            _env_file=None,
            provider="fake",
            memory_db=Path(tmp) / "memory.db",
            runs_dir=Path(tmp) / "runs",
        )
        platform = Platform(settings)
        try:
            schema = create_app(settings, platform=platform).openapi()
        finally:
            platform.close()
    target = ROOT / "docs" / "openapi.json"
    target.write_text(json.dumps(schema, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"Wrote {target.relative_to(ROOT)} ({len(schema['paths'])} paths).")


if __name__ == "__main__":
    main()
