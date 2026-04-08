"""
Report generation for the WSD Flow Validator.

Supports two formats:
  text  — human-readable console output with severity grouping
  json  — machine-readable JSON (suitable for downstream automation / alerting)
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import IO

from .models import SeverityLevel, ValidatorReport, ValidationResult

# ANSI colour codes (disabled automatically when writing to a file)
_COLOURS = {
    SeverityLevel.ESCALATION: "\033[91m",   # bright red
    SeverityLevel.STUCK:      "\033[93m",   # yellow
    SeverityLevel.DELAYED:    "\033[94m",   # blue
    SeverityLevel.OK:         "\033[92m",   # green
    "reset":                  "\033[0m",
    "bold":                   "\033[1m",
}


def _colour(text: str, level: SeverityLevel, stream: IO) -> str:
    if not stream.isatty():
        return text
    c = _COLOURS.get(level, "")
    reset = _COLOURS["reset"]
    return f"{c}{text}{reset}"


def _bold(text: str, stream: IO) -> str:
    if not stream.isatty():
        return text
    return f"{_COLOURS['bold']}{text}{_COLOURS['reset']}"


# ---------------------------------------------------------------------------
# Text reporter
# ---------------------------------------------------------------------------

def render_text(report: ValidatorReport, stream: IO = sys.stdout) -> None:
    """Write a human-readable report to *stream*."""
    ts = report.generated_at.strftime("%Y-%m-%d %H:%M UTC")
    stream.write(f"\n{_bold('WSD Access Request — Flow Validator Report', stream)}\n")
    stream.write(f"Generated: {ts}\n")
    stream.write("─" * 60 + "\n\n")

    # Summary line
    stream.write(_bold("Summary", stream) + "\n")
    stream.write(f"  Total active requests : {report.total_active}\n")
    stream.write(
        f"  {_colour('ESCALATION', SeverityLevel.ESCALATION, stream)} : "
        f"{len(report.escalation)}\n"
    )
    stream.write(
        f"  {_colour('STUCK     ', SeverityLevel.STUCK, stream)} : "
        f"{len(report.stuck)}\n"
    )
    stream.write(
        f"  {_colour('DELAYED   ', SeverityLevel.DELAYED, stream)} : "
        f"{len(report.delayed)}\n"
    )
    stream.write(
        f"  {_colour('OK        ', SeverityLevel.OK, stream)} : "
        f"{len(report.ok)}\n"
    )
    stream.write("\n")

    if report.total_flagged == 0:
        stream.write(_colour("All requests are within SLA. No action required.\n", SeverityLevel.OK, stream))
        stream.write("\n")
        return

    # Detail section — only flagged items, grouped by severity
    for severity, items in report.by_severity().items():
        if severity == SeverityLevel.OK or not items:
            continue

        header = _colour(f"[{severity.value}]", severity, stream)
        stream.write(_bold(f"{header} {len(items)} request(s)\n", stream))
        stream.write("─" * 60 + "\n")

        for r in items:
            _render_result_text(r, stream)

        stream.write("\n")


def _render_result_text(result: ValidationResult, stream: IO) -> None:
    req = result.request
    stream.write(
        f"  ID      : {req.id}\n"
        f"  Account : {req.crm_account_number or '(not set)'}\n"
        f"  Contact : {req.contact_name} <{req.contact_email}>\n"
        f"  Systems : {', '.join(req.requested_systems) or '(none)'}\n"
        f"  Status  : {req.status.value}\n"
        f"  Age     : {result.age_hours:.1f}h  |  Idle: {result.stale_hours:.1f}h\n"
        f"  Assigned: {req.assigned_to or '(unassigned)'}\n"
        f"  Message : {result.message}\n"
    )
    if result.flags:
        stream.write(f"  Flags   : {', '.join(result.flags)}\n")
    stream.write("\n")


# ---------------------------------------------------------------------------
# JSON reporter
# ---------------------------------------------------------------------------

def render_json(report: ValidatorReport, stream: IO = sys.stdout) -> None:
    """Write a machine-readable JSON report to *stream*."""
    payload = _report_to_dict(report)
    json.dump(payload, stream, indent=2, default=str)
    stream.write("\n")


def _report_to_dict(report: ValidatorReport) -> dict:
    return {
        "generatedAt": report.generated_at.isoformat(),
        "summary": {
            "totalActive": report.total_active,
            "totalFlagged": report.total_flagged,
            "escalation": len(report.escalation),
            "stuck": len(report.stuck),
            "delayed": len(report.delayed),
            "ok": len(report.ok),
        },
        "flagged": [_result_to_dict(r) for r in report.all_flagged],
        "ok": [_result_to_dict(r) for r in report.ok],
    }


def _result_to_dict(result: ValidationResult) -> dict:
    req = result.request
    return {
        "id": req.id,
        "severity": result.severity.value,
        "message": result.message,
        "flags": result.flags,
        "ageHours": round(result.age_hours, 2),
        "staleHours": round(result.stale_hours, 2),
        "request": {
            "crmAccountNumber": req.crm_account_number,
            "contactName": req.contact_name,
            "contactEmail": req.contact_email,
            "requestedSystems": req.requested_systems,
            "status": req.status.value,
            "submittedAt": req.submitted_at.isoformat(),
            "lastUpdatedAt": req.last_updated_at.isoformat(),
            "assignedTo": req.assigned_to,
            "notes": req.notes,
        },
    }
