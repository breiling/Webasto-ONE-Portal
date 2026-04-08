#!/usr/bin/env python3
"""
Power BI data-feed server for the WSD Flow Validator.

Runs the validator on every request and serves the results as a flat JSON
array that Power BI can refresh on demand via the Web connector.

Usage:
    # 1. Start the mock CRM (or point .env at a real CRM):
    python mock_crm/server.py

    # 2. Start this server:
    python powerbi/serve.py

    # 3. In Power BI Desktop:
    #    Home → Get Data → Web → http://localhost:8081/data
    #    (or paste the query from powerbi/WSD_Flow_Validator.pq)

Endpoints:
    GET /data       — JSON array of flat request rows (for Power BI Web connector)
    GET /data.csv   — same data as a CSV download (for Excel / manual import)
    GET /health     — {"status": "ok"} liveness check
    GET /           — plain-text instructions
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import StringIO
from pathlib import Path

# Allow running from the project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flow_validator.config import Config
from flow_validator.crm_client import CRMClient, CRMClientError
from flow_validator.reporter import render_csv, to_flat_rows
from flow_validator.validator import FlowValidator

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
logger = logging.getLogger("powerbi_serve")


def _load_dotenv() -> None:
    import os
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path)
    except ImportError:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _run_validator() -> list[dict]:
    """Fetch from CRM, validate, return flat rows."""
    config = Config.from_env()
    errors = config.validate()
    if errors:
        raise RuntimeError("Invalid config: " + "; ".join(errors))
    client = CRMClient(config)
    validator = FlowValidator(config)
    requests = client.get_active_requests()
    report = validator.validate(requests)
    logger.info(
        "Validated %d requests — %d flagged (%d escalation, %d stuck, %d delayed)",
        report.total_active, report.total_flagged,
        len(report.escalation), len(report.stuck), len(report.delayed),
    )
    return to_flat_rows(report)


class PowerBIHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        logger.info("%-7s %s", args[0] if args else "", self.path)

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/")

        if path == "/data":
            self._serve_json()
        elif path == "/data.csv":
            self._serve_csv()
        elif path == "/health":
            self._send(b'{"status":"ok"}', "application/json")
        else:
            self._send(self._instructions().encode(), "text/plain; charset=utf-8")

    # ------------------------------------------------------------------

    def _serve_json(self):
        try:
            rows = _run_validator()
        except Exception as exc:
            logger.error("Validator error: %s", exc)
            self._send(
                json.dumps({"error": str(exc)}).encode(),
                "application/json", status=500,
            )
            return
        body = json.dumps(rows, indent=2, default=str).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _serve_csv(self):
        try:
            rows = _run_validator()
        except Exception as exc:
            logger.error("Validator error: %s", exc)
            self._send(str(exc).encode(), "text/plain", status=500)
            return

        # Rebuild a ValidatorReport to reuse render_csv — easier to just write CSV directly
        from flow_validator.reporter import _CSV_FIELDS
        import csv as _csv
        buf = StringIO()
        writer = _csv.DictWriter(buf, fieldnames=_CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        body = buf.getvalue().encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", 'attachment; filename="wsd_flow_validator.csv"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send(self, body: bytes, content_type: str, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _instructions() -> str:
        return (
            "WSD Flow Validator — Power BI data feed\n"
            "========================================\n\n"
            "Endpoints:\n"
            "  GET /data       JSON array (Power BI Web connector)\n"
            "  GET /data.csv   CSV download (Excel / manual import)\n"
            "  GET /health     Liveness check\n\n"
            "Power BI setup:\n"
            "  Home → Get Data → Web → http://localhost:8081/data\n"
            "  — or paste the query from powerbi/WSD_Flow_Validator.pq\n"
        )


def main():
    _load_dotenv()

    parser = argparse.ArgumentParser(description="Power BI data-feed server for WSD Flow Validator")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8081)
    args = parser.parse_args()

    server = HTTPServer((args.host, args.port), PowerBIHandler)
    logger.info("Power BI feed listening on http://%s:%d", args.host, args.port)
    logger.info("  /data      → JSON (Power BI Web connector)")
    logger.info("  /data.csv  → CSV download")
    logger.info("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopped.")


if __name__ == "__main__":
    main()
