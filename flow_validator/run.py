"""
CLI entry point for the WSD Flow Validator.

Usage:
  python -m flow_validator.run [options]

Options:
  --format {text,json}   Output format (default: text)
  --output FILE          Write report to FILE instead of stdout
  --request-id ID        Validate a single request by CRM ID
  --dry-run              Load config and connect, but don't write output
  --verbose              Enable DEBUG logging
  --exit-code            Exit with non-zero code if any requests are flagged
                         (useful in CI / scheduled task contexts)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Allow running as `python -m flow_validator.run` from the project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flow_validator.config import Config
from flow_validator.crm_client import CRMClient, CRMClientError
from flow_validator.reporter import render_csv, render_json, render_text
from flow_validator.validator import FlowValidator


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flow-validator",
        description="Monitor the WSD access request queue and flag stuck/delayed requests.",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json", "csv"],
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--output",
        metavar="FILE",
        help="Write report to FILE instead of stdout",
    )
    parser.add_argument(
        "--request-id",
        metavar="ID",
        help="Validate a single request by its CRM ID",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Connect and validate config but produce no output",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG-level logging",
    )
    parser.add_argument(
        "--exit-code",
        action="store_true",
        help="Exit with code 1 if any requests are flagged (for CI / alerting use)",
    )
    return parser


def _load_dotenv_if_present() -> None:
    """Load .env file if present (without requiring python-dotenv as hard dep)."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path)
        logging.debug("Loaded .env from %s", env_path)
    except ImportError:
        # dotenv not installed — parse manually (simple KEY=VALUE, no special cases)
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                os.environ.setdefault(key, value)


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    _load_dotenv_if_present()

    # --- Load & validate config ---
    config = Config.from_env()
    errors = config.validate()
    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        print(
            "\nConfigure the required environment variables (see .env.example).",
            file=sys.stderr,
        )
        return 2

    # --- Fetch requests ---
    client = CRMClient(config)
    validator = FlowValidator(config)

    try:
        if args.request_id:
            req = client.get_request_by_id(args.request_id)
            if req is None:
                print(f"ERROR: Request '{args.request_id}' not found in CRM.", file=sys.stderr)
                return 1
            active_requests = [req]
        else:
            active_requests = client.get_active_requests()
    except CRMClientError as exc:
        print(f"ERROR: CRM connection failed — {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"Dry run: fetched {len(active_requests)} active request(s). No output written.")
        return 0

    # --- Validate ---
    report = validator.validate(active_requests)

    # --- Write report ---
    _renderers = {"json": render_json, "csv": render_csv, "text": render_text}
    render = _renderers[args.format]

    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="") as out_file:
            render(report, stream=out_file)
        print(f"Report written to {args.output}")
    else:
        render(report, stream=sys.stdout)

    if args.exit_code and report.total_flagged > 0:
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
