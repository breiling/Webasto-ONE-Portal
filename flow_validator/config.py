"""
Configuration for the WSD Flow Validator.
All values are loaded from environment variables.
"""

import os
from dataclasses import dataclass, field


@dataclass
class SLAThresholds:
    """Hours before a request is considered delayed / stuck / escalation-needed."""
    warning_hours: int = 24       # flag as DELAYED
    critical_hours: int = 48      # flag as STUCK
    escalation_hours: int = 72    # flag as ESCALATION NEEDED


@dataclass
class Config:
    # CRM API connection
    crm_base_url: str = ""
    crm_api_key: str = ""
    crm_tenant_id: str = ""
    crm_client_id: str = ""
    crm_client_secret: str = ""

    # Which CRM auth mode to use: "api_key" | "oauth2"
    crm_auth_mode: str = "api_key"

    # Request timeout and retry settings
    request_timeout_seconds: int = 30
    max_retries: int = 4
    retry_base_delay_seconds: float = 2.0

    # SLA thresholds
    sla: SLAThresholds = field(default_factory=SLAThresholds)

    # Statuses that represent terminal (no further action needed) states
    terminal_statuses: tuple = ("COMPLETED", "REJECTED", "CANCELLED")

    # Statuses that are active/non-terminal and should be monitored
    active_statuses: tuple = ("SUBMITTED", "UNDER_REVIEW", "APPROVED", "PROVISIONING")

    @classmethod
    def from_env(cls) -> "Config":
        cfg = cls(
            crm_base_url=os.environ.get("CRM_BASE_URL", ""),
            crm_api_key=os.environ.get("CRM_API_KEY", ""),
            crm_tenant_id=os.environ.get("CRM_TENANT_ID", ""),
            crm_client_id=os.environ.get("CRM_CLIENT_ID", ""),
            crm_client_secret=os.environ.get("CRM_CLIENT_SECRET", ""),
            crm_auth_mode=os.environ.get("CRM_AUTH_MODE", "api_key"),
            request_timeout_seconds=int(os.environ.get("REQUEST_TIMEOUT_SECONDS", "30")),
            max_retries=int(os.environ.get("MAX_RETRIES", "4")),
            retry_base_delay_seconds=float(os.environ.get("RETRY_BASE_DELAY_SECONDS", "2.0")),
            sla=SLAThresholds(
                warning_hours=int(os.environ.get("SLA_WARNING_HOURS", "24")),
                critical_hours=int(os.environ.get("SLA_CRITICAL_HOURS", "48")),
                escalation_hours=int(os.environ.get("SLA_ESCALATION_HOURS", "72")),
            ),
        )
        return cfg

    def validate(self) -> list[str]:
        """Return a list of validation errors (empty = valid)."""
        errors = []
        if not self.crm_base_url:
            errors.append("CRM_BASE_URL is required")
        if self.crm_auth_mode == "api_key" and not self.crm_api_key:
            errors.append("CRM_API_KEY is required when CRM_AUTH_MODE=api_key")
        if self.crm_auth_mode == "oauth2":
            for var in ("CRM_TENANT_ID", "CRM_CLIENT_ID", "CRM_CLIENT_SECRET"):
                if not os.environ.get(var):
                    errors.append(f"{var} is required when CRM_AUTH_MODE=oauth2")
        return errors
