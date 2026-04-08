"""
Data models for WSD access requests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class RequestStatus(str, Enum):
    SUBMITTED = "SUBMITTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    APPROVED = "APPROVED"
    PROVISIONING = "PROVISIONING"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def from_str(cls, value: str) -> "RequestStatus":
        try:
            return cls(value.upper())
        except ValueError:
            return cls.UNKNOWN


class SeverityLevel(str, Enum):
    OK = "OK"
    DELAYED = "DELAYED"        # warning threshold exceeded
    STUCK = "STUCK"            # critical threshold exceeded
    ESCALATION = "ESCALATION"  # escalation threshold exceeded


@dataclass
class AccessRequest:
    """A single WSD access request as returned by the CRM API."""
    id: str
    crm_account_number: str
    contact_name: str
    contact_email: str
    requested_systems: list[str]
    status: RequestStatus
    submitted_at: datetime
    last_updated_at: datetime
    assigned_to: Optional[str] = None
    notes: Optional[str] = None

    @property
    def age_hours(self) -> float:
        """Total age of the request in hours since submission."""
        now = datetime.now(timezone.utc)
        submitted = self.submitted_at.replace(tzinfo=timezone.utc) if self.submitted_at.tzinfo is None else self.submitted_at
        return (now - submitted).total_seconds() / 3600

    @property
    def stale_hours(self) -> float:
        """Hours since the request was last updated (i.e., how long it has been idle)."""
        now = datetime.now(timezone.utc)
        updated = self.last_updated_at.replace(tzinfo=timezone.utc) if self.last_updated_at.tzinfo is None else self.last_updated_at
        return (now - updated).total_seconds() / 3600

    @classmethod
    def from_api_response(cls, data: dict) -> "AccessRequest":
        """Parse a CRM API response dict into an AccessRequest."""
        return cls(
            id=str(data.get("id") or data.get("requestId") or ""),
            crm_account_number=str(data.get("accountNumber") or data.get("crmAccountNumber") or ""),
            contact_name=str(data.get("contactName") or ""),
            contact_email=str(data.get("contactEmail") or ""),
            requested_systems=data.get("requestedSystems") or data.get("systems") or [],
            status=RequestStatus.from_str(str(data.get("status") or "UNKNOWN")),
            submitted_at=_parse_dt(data.get("submittedAt") or data.get("createdAt") or ""),
            last_updated_at=_parse_dt(data.get("lastUpdatedAt") or data.get("updatedAt") or data.get("submittedAt") or ""),
            assigned_to=data.get("assignedTo"),
            notes=data.get("notes"),
        )


@dataclass
class ValidationResult:
    """The outcome of validating a single AccessRequest against SLA thresholds."""
    request: AccessRequest
    severity: SeverityLevel
    age_hours: float
    stale_hours: float
    message: str
    flags: list[str] = field(default_factory=list)

    @property
    def is_flagged(self) -> bool:
        return self.severity != SeverityLevel.OK


@dataclass
class ValidatorReport:
    """Aggregated results from a full validation run."""
    generated_at: datetime
    total_active: int
    ok: list[ValidationResult] = field(default_factory=list)
    delayed: list[ValidationResult] = field(default_factory=list)
    stuck: list[ValidationResult] = field(default_factory=list)
    escalation: list[ValidationResult] = field(default_factory=list)

    @property
    def total_flagged(self) -> int:
        return len(self.delayed) + len(self.stuck) + len(self.escalation)

    @property
    def all_flagged(self) -> list[ValidationResult]:
        return self.escalation + self.stuck + self.delayed

    def by_severity(self) -> dict[SeverityLevel, list[ValidationResult]]:
        return {
            SeverityLevel.ESCALATION: self.escalation,
            SeverityLevel.STUCK: self.stuck,
            SeverityLevel.DELAYED: self.delayed,
            SeverityLevel.OK: self.ok,
        }


def _parse_dt(value: str) -> datetime:
    """Best-effort ISO 8601 datetime parser."""
    if not value:
        return datetime.now(timezone.utc)
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return datetime.now(timezone.utc)
