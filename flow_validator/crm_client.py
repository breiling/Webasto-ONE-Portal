"""
CRM API client for fetching WSD access requests.

Supports two auth modes:
  - api_key:  X-Api-Key header (simple REST APIs / custom integration layer)
  - oauth2:   Azure AD client-credentials flow (Dynamics 365 / Power Platform)

Retry logic: exponential backoff on transient HTTP errors (429, 5xx) and
connection failures, up to config.max_retries attempts.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import requests
from requests import Response, Session
from requests.exceptions import ConnectionError, Timeout

from .config import Config
from .models import AccessRequest, RequestStatus

logger = logging.getLogger(__name__)


class CRMClientError(Exception):
    """Raised when the CRM API returns a non-retryable error."""


class CRMClient:
    def __init__(self, config: Config):
        self._config = config
        self._session: Session = requests.Session()
        self._access_token: Optional[str] = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_active_requests(self) -> list[AccessRequest]:
        """
        Fetch all active (non-terminal) WSD access requests from the CRM.

        Returns a list of AccessRequest objects sorted by submission date
        (oldest first — highest priority for review).
        """
        statuses = list(self._config.active_statuses)
        raw = self._fetch_requests_by_statuses(statuses)
        requests_list = [AccessRequest.from_api_response(r) for r in raw]
        requests_list.sort(key=lambda r: r.submitted_at)
        logger.info("Fetched %d active requests from CRM", len(requests_list))
        return requests_list

    def get_request_by_id(self, request_id: str) -> Optional[AccessRequest]:
        """Fetch a single request by its CRM ID."""
        endpoint = f"{self._config.crm_base_url}/access-requests/{request_id}"
        data = self._get(endpoint)
        if data is None:
            return None
        return AccessRequest.from_api_response(data)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_requests_by_statuses(self, statuses: list[str]) -> list[dict]:
        """
        Fetch all requests matching the given status list.

        The integration layer is expected to support:
          GET /access-requests?status=SUBMITTED,UNDER_REVIEW,...
        """
        status_param = ",".join(statuses)
        endpoint = f"{self._config.crm_base_url}/access-requests"
        params = {"status": status_param, "pageSize": 500}

        all_items: list[dict] = []
        page = 1

        while True:
            params["page"] = page
            response_data = self._get(endpoint, params=params)
            if response_data is None:
                break

            # Support both {"items": [...]} and plain list responses
            if isinstance(response_data, list):
                items = response_data
                has_more = False
            else:
                items = response_data.get("items") or response_data.get("value") or []
                has_more = response_data.get("hasMore", False) or response_data.get("nextLink") is not None

            all_items.extend(items)

            if not has_more or not items:
                break
            page += 1

        return all_items

    def _get(self, url: str, params: Optional[dict] = None) -> Any:
        """GET with auth headers and retry logic. Returns parsed JSON or None."""
        return self._request_with_retry("GET", url, params=params)

    def _request_with_retry(self, method: str, url: str, **kwargs) -> Any:
        headers = self._build_auth_headers()
        last_exc: Optional[Exception] = None
        delay = self._config.retry_base_delay_seconds

        for attempt in range(1, self._config.max_retries + 2):  # +2: first try + N retries
            try:
                response: Response = self._session.request(
                    method,
                    url,
                    headers=headers,
                    timeout=self._config.request_timeout_seconds,
                    **kwargs,
                )

                if response.status_code == 200:
                    return response.json()

                if response.status_code == 404:
                    logger.warning("Resource not found: %s", url)
                    return None

                if response.status_code in (401, 403):
                    if self._config.crm_auth_mode == "oauth2":
                        # Token may have expired — refresh once and retry
                        logger.info("Auth error, refreshing OAuth2 token")
                        self._access_token = None
                        headers = self._build_auth_headers()
                        continue
                    raise CRMClientError(
                        f"Auth error {response.status_code} for {url}: {response.text[:200]}"
                    )

                if response.status_code == 429 or response.status_code >= 500:
                    # Transient — retry with backoff
                    retry_after = int(response.headers.get("Retry-After", delay))
                    logger.warning(
                        "Transient HTTP %s on %s (attempt %d/%d), retrying in %.1fs",
                        response.status_code, url, attempt, self._config.max_retries + 1, retry_after,
                    )
                    if attempt <= self._config.max_retries:
                        time.sleep(retry_after)
                        delay *= 2
                        continue

                raise CRMClientError(
                    f"Unexpected HTTP {response.status_code} for {url}: {response.text[:200]}"
                )

            except (ConnectionError, Timeout) as exc:
                last_exc = exc
                logger.warning(
                    "Network error on %s (attempt %d/%d): %s",
                    url, attempt, self._config.max_retries + 1, exc,
                )
                if attempt <= self._config.max_retries:
                    time.sleep(delay)
                    delay *= 2

        raise CRMClientError(
            f"Failed to reach {url} after {self._config.max_retries + 1} attempts"
        ) from last_exc

    def _build_auth_headers(self) -> dict[str, str]:
        if self._config.crm_auth_mode == "api_key":
            return {
                "X-Api-Key": self._config.crm_api_key,
                "Accept": "application/json",
            }
        # OAuth2 client credentials
        token = self._get_oauth2_token()
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

    def _get_oauth2_token(self) -> str:
        if self._access_token:
            return self._access_token

        token_url = (
            f"https://login.microsoftonline.com/{self._config.crm_tenant_id}/oauth2/v2.0/token"
        )
        data = {
            "grant_type": "client_credentials",
            "client_id": self._config.crm_client_id,
            "client_secret": self._config.crm_client_secret,
            "scope": f"{self._config.crm_base_url}/.default",
        }
        try:
            response = requests.post(token_url, data=data, timeout=30)
            response.raise_for_status()
            self._access_token = response.json()["access_token"]
            return self._access_token
        except Exception as exc:
            raise CRMClientError(f"Failed to obtain OAuth2 token: {exc}") from exc
