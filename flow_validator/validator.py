"""
Core validation logic for WSD access requests.

Checks each active request against SLA thresholds and assigns a severity:

  OK          — within warning threshold (stale_hours < warning_hours)
  DELAYED     — warning threshold exceeded (stale_hours >= warning_hours)
  STUCK       — critical threshold exceeded (stale_hours >= critical_hours)
  ESCALATION  — escalation threshold exceeded (stale_hours >= escalation_hours)

The staleness check uses `last_updated_at` rather than `submitted_at` so that
a request that was touched recently (e.g. a note added) doesn't get flagged
even if it was submitted a long time ago.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from .config import Config
from .models import (
    AccessRequest,
    RequestStatus,
    SeverityLevel,
    ValidatorReport,
    ValidationResult,
)

logger = logging.getLogger(__name__)


class FlowValidator:
    def __init__(self, config: Config):
        self._cfg = config

    def validate(self, requests: list[AccessRequest]) -> ValidatorReport:
        """
        Validate a list of AccessRequest objects and return a ValidatorReport.

        Only non-terminal (active) requests are evaluated; anything in a
        terminal status is skipped with a warning logged.
        """
        report = ValidatorReport(
            generated_at=datetime.now(timezone.utc),
            total_active=len(requests),
        )

        for req in requests:
            if req.status.value in self._cfg.terminal_statuses:
                logger.debug("Skipping terminal request %s (status=%s)", req.id, req.status)
                continue

            result = self._validate_request(req)

            if result.severity == SeverityLevel.OK:
                report.ok.append(result)
            elif result.severity == SeverityLevel.DELAYED:
                report.delayed.append(result)
            elif result.severity == SeverityLevel.STUCK:
                report.stuck.append(result)
            elif result.severity == SeverityLevel.ESCALATION:
                report.escalation.append(result)

        logger.info(
            "Validation complete: %d active | %d OK | %d delayed | %d stuck | %d escalation",
            report.total_active,
            len(report.ok),
            len(report.delayed),
            len(report.stuck),
            len(report.escalation),
        )
        return report

    def _validate_request(self, req: AccessRequest) -> ValidationResult:
        age_h = req.age_hours
        stale_h = req.stale_hours
        sla = self._cfg.sla
        flags: list[str] = []

        # --- Determine severity based on staleness ---
        if stale_h >= sla.escalation_hours:
            severity = SeverityLevel.ESCALATION
            message = (
                f"No activity for {stale_h:.1f}h — escalation needed "
                f"(threshold: {sla.escalation_hours}h)"
            )
            flags.append(f"stale>{sla.escalation_hours}h")
        elif stale_h >= sla.critical_hours:
            severity = SeverityLevel.STUCK
            message = (
                f"No activity for {stale_h:.1f}h — request appears stuck "
                f"(threshold: {sla.critical_hours}h)"
            )
            flags.append(f"stale>{sla.critical_hours}h")
        elif stale_h >= sla.warning_hours:
            severity = SeverityLevel.DELAYED
            message = (
                f"No activity for {stale_h:.1f}h — review is delayed "
                f"(threshold: {sla.warning_hours}h)"
            )
            flags.append(f"stale>{sla.warning_hours}h")
        else:
            severity = SeverityLevel.OK
            message = f"Within SLA — last activity {stale_h:.1f}h ago"

        # --- Additional contextual flags ---
        if req.status == RequestStatus.SUBMITTED and stale_h >= sla.warning_hours:
            flags.append("unreviewed")

        if req.status == RequestStatus.APPROVED and stale_h >= sla.warning_hours:
            flags.append("provisioning-delayed")

        if req.status == RequestStatus.PROVISIONING and stale_h >= (sla.warning_hours / 2):
            # Provisioning should be fast; flag at half the normal warning threshold
            flags.append("provisioning-slow")
            if severity == SeverityLevel.OK:
                severity = SeverityLevel.DELAYED
                message = (
                    f"Provisioning has been running for {stale_h:.1f}h "
                    f"— expected to complete within {sla.warning_hours / 2:.0f}h"
                )

        if req.assigned_to is None and req.status == RequestStatus.UNDER_REVIEW:
            flags.append("no-assignee")

        if not req.crm_account_number:
            flags.append("missing-account-number")

        return ValidationResult(
            request=req,
            severity=severity,
            age_hours=age_h,
            stale_hours=stale_h,
            message=message,
            flags=flags,
        )
