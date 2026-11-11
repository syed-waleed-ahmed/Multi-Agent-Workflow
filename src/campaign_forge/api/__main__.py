"""``python -m campaign_forge.api [--host HOST] [--port PORT]``: serve the API with uvicorn."""

from __future__ import annotations

import argparse

import uvicorn


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m campaign_forge.api")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: localhost)")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    uvicorn.run("campaign_forge.api.app:create_app", factory=True, host=args.host, port=args.port)


if __name__ == "__main__":  # pragma: no cover
    main()
