#!/usr/bin/env python3
"""
Mock CRM server for local testing of the WSD Flow Validator.

No extra dependencies — uses Python stdlib only.

Usage:
    python mock_crm/server.py            # listens on http://127.0.0.1:8080
    python mock_crm/server.py --port 9000

Then set in .env:
    CRM_BASE_URL=http://localhost:8080/api/v1
    CRM_AUTH_MODE=api_key
    CRM_API_KEY=dummy

Sample data covers every SLA severity band:
    REQ-001 / REQ-002  →  OK          (stale < 24 h)
    REQ-003 / REQ-004  →  DELAYED     (stale 25-47 h)
    REQ-005 / REQ-006  →  STUCK       (stale 49-71 h)
    REQ-007 / REQ-008  →  ESCALATION  (stale > 72 h)
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
logger = logging.getLogger("mock_crm")


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

def _dt(hours_ago: float) -> str:
    """ISO 8601 UTC timestamp for `hours_ago` hours in the past."""
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _make_requests() -> list[dict]:
    """
    Return sample AccessRequest objects.

    stale_hours = time since lastUpdatedAt (this is what the validator checks):
      < 24 h  → OK
      ≥ 24 h  → DELAYED
      ≥ 48 h  → STUCK
      ≥ 72 h  → ESCALATION
    """
    return [
        # ── OK ──────────────────────────────────────────────────────────────
        {
            "id": "REQ-001",
            "accountNumber": "ACC-1001",
            "contactName": "Alice Hansen",
            "contactEmail": "alice.hansen@example.com",
            "requestedSystems": ["WSD"],
            "status": "SUBMITTED",
            "submittedAt": _dt(4),
            "lastUpdatedAt": _dt(2),       # stale 2 h → OK
            "assignedTo": None,
            "notes": None,
        },
        {
            "id": "REQ-002",
            "accountNumber": "ACC-1002",
            "contactName": "Bob Müller",
            "contactEmail": "bob.mueller@example.com",
            "requestedSystems": ["WSD", "AdminUI"],
            "status": "UNDER_REVIEW",
            "submittedAt": _dt(20),
            "lastUpdatedAt": _dt(10),      # stale 10 h → OK
            "assignedTo": "reviewer@example.com",
            "notes": "Pending background check",
        },
        # ── DELAYED (stale 25–47 h) ──────────────────────────────────────────
        {
            "id": "REQ-003",
            "accountNumber": "ACC-1003",
            "contactName": "Carla Rossi",
            "contactEmail": "carla.rossi@example.com",
            "requestedSystems": ["WSD"],
            "status": "SUBMITTED",
            "submittedAt": _dt(30),
            "lastUpdatedAt": _dt(26),      # stale 26 h → DELAYED
            "assignedTo": None,
            "notes": None,
        },
        {
            "id": "REQ-004",
            "accountNumber": "ACC-1004",
            "contactName": "David Patel",
            "contactEmail": "david.patel@example.com",
            "requestedSystems": ["AdminUI"],
            "status": "APPROVED",
            "submittedAt": _dt(50),
            "lastUpdatedAt": _dt(36),      # stale 36 h → DELAYED
            "assignedTo": "approver@example.com",
            "notes": None,
        },
        # ── STUCK (stale 49–71 h) ────────────────────────────────────────────
        {
            "id": "REQ-005",
            "accountNumber": "ACC-1005",
            "contactName": "Eva Björk",
            "contactEmail": "eva.bjork@example.com",
            "requestedSystems": ["WSD", "FleetMgr"],
            "status": "UNDER_REVIEW",
            "submittedAt": _dt(72),
            "lastUpdatedAt": _dt(55),      # stale 55 h → STUCK
            "assignedTo": "reviewer@example.com",
            "notes": "Waiting for manager approval",
        },
        {
            "id": "REQ-006",
            "accountNumber": "ACC-1006",
            "contactName": "Frank Schmidt",
            "contactEmail": "frank.schmidt@example.com",
            "requestedSystems": ["WSD"],
            "status": "PROVISIONING",
            "submittedAt": _dt(80),
            "lastUpdatedAt": _dt(62),      # stale 62 h → STUCK
            "assignedTo": "provisioning@example.com",
            "notes": None,
        },
        # ── ESCALATION (stale > 72 h) ────────────────────────────────────────
        {
            "id": "REQ-007",
            "accountNumber": "ACC-1007",
            "contactName": "Greta Lindqvist",
            "contactEmail": "greta.lindqvist@example.com",
            "requestedSystems": ["WSD", "AdminUI", "FleetMgr"],
            "status": "SUBMITTED",
            "submittedAt": _dt(120),
            "lastUpdatedAt": _dt(96),      # stale 96 h → ESCALATION
            "assignedTo": None,
            "notes": "No response from contact",
        },
        {
            "id": "REQ-008",
            "accountNumber": "ACC-1008",
            "contactName": "Hans Weber",
            "contactEmail": "hans.weber@example.com",
            "requestedSystems": ["WSD"],
            "status": "UNDER_REVIEW",
            "submittedAt": _dt(150),
            "lastUpdatedAt": _dt(110),     # stale 110 h → ESCALATION
            "assignedTo": "reviewer@example.com",
            "notes": "Escalated — awaiting director sign-off",
        },
    ]


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class MockCRMHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        logger.info("%-7s %s", args[0] if args else "", self.path)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        qs = parse_qs(parsed.query)

        if path == "/api/v1/access-requests":
            self._handle_list(qs)
        elif path.startswith("/api/v1/access-requests/"):
            request_id = path[len("/api/v1/access-requests/"):]
            self._handle_single(request_id)
        else:
            self._send_json({"error": "Not found"}, status=404)

    # ------------------------------------------------------------------

    def _handle_list(self, qs: dict):
        all_requests = _make_requests()

        # Filter by status (comma-separated, e.g. status=SUBMITTED,UNDER_REVIEW)
        status_param = qs.get("status", [""])[0]
        if status_param:
            allowed = {s.strip().upper() for s in status_param.split(",")}
            all_requests = [r for r in all_requests if r["status"] in allowed]

        # Pagination
        page_size = int(qs.get("pageSize", ["500"])[0])
        page = int(qs.get("page", ["1"])[0])
        start = (page - 1) * page_size
        end = start + page_size
        page_items = all_requests[start:end]
        has_more = end < len(all_requests)

        self._send_json({"items": page_items, "hasMore": has_more})

    def _handle_single(self, request_id: str):
        for req in _make_requests():
            if req["id"] == request_id:
                self._send_json(req)
                return
        self._send_json({"error": f"Request '{request_id}' not found"}, status=404)

    def _send_json(self, payload, status: int = 200):
        body = json.dumps(payload, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Mock CRM server for WSD Flow Validator")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    server = HTTPServer((args.host, args.port), MockCRMHandler)
    logger.info("Mock CRM listening on http://%s:%d/api/v1", args.host, args.port)
    logger.info(".env settings:")
    logger.info("  CRM_BASE_URL=http://%s:%d/api/v1", args.host, args.port)
    logger.info("  CRM_AUTH_MODE=api_key")
    logger.info("  CRM_API_KEY=dummy")
    logger.info("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopped.")


if __name__ == "__main__":
    main()
