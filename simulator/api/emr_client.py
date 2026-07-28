from __future__ import annotations

import itertools
import logging
import random
import time
from typing import Any
from urllib.parse import quote
from urllib.parse import urljoin

import requests

from simulator.core.models import EMROAuthConfig


class EMRClient:
    def __init__(
        self,
        base_url: str,
        access_token: str | None = None,
        verify_ssl: bool | str = True,
        timeout_sec: float = 3.0,
        retries: int = 2,
        backoff_sec: float = 0.3,
        dry_run: bool = True,
        oauth_config: EMROAuthConfig | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        self.verify_ssl = verify_ssl
        self.timeout_sec = timeout_sec
        self.retries = retries
        self.backoff_sec = backoff_sec
        self.dry_run = dry_run
        self.oauth_config = oauth_config
        self.log = logging.getLogger("simulator.emr_client")
        self._dummy_counter = itertools.count(1)
        self._token_expires_at: float = 0.0
        self._dry_patients_by_identifier: dict[str, str] = {}
        self._dry_assignments_by_device: dict[str, str] = {}

    @staticmethod
    def _latency_ms(start_ts: float) -> int:
        return int((time.perf_counter() - start_ts) * 1000)

    @staticmethod
    def _clip_text(value: Any, max_len: int = 180) -> str:
        if value is None:
            return ""
        text = str(value).replace("\n", " ").replace("\r", " ").strip()
        if len(text) <= max_len:
            return text
        return text[: max_len - 3] + "..."

    def _refresh_access_token(self) -> bool:
        if self.dry_run or not self.oauth_config:
            return False

        form = {
            "grant_type": self.oauth_config.grant_type,
            "client_id": self.oauth_config.client_id,
            "client_secret": self.oauth_config.client_secret,
            "username": self.oauth_config.username,
            "password": self.oauth_config.password,
            "user_role": self.oauth_config.user_role,
            "scope": self.oauth_config.scope,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        try:
            start_ts = time.perf_counter()
            response = requests.post(
                self.oauth_config.token_url,
                headers=headers,
                data=form,
                timeout=self.timeout_sec,
                verify=self.verify_ssl,
            )
            latency_ms = self._latency_ms(start_ts)
            response.raise_for_status()
            payload = response.json() if response.content else {}
            token = str(payload.get("access_token", "")).strip()
            if not token:
                self.log.warning("OAuth token response missing access_token")
                return False
            expires_in = int(payload.get("expires_in", 300))
            self.access_token = token
            self._token_expires_at = time.monotonic() + max(expires_in - 30, 30)
            self.log.debug(
                "oauth_token_fetch ok status=%s latency_ms=%s url=%s",
                response.status_code,
                latency_ms,
                self.oauth_config.token_url,
            )
            return True
        except Exception as exc:
            self.log.debug(
                "oauth_token_fetch error latency_ms=%s url=%s error=%s",
                self._latency_ms(start_ts) if "start_ts" in locals() else -1,
                self.oauth_config.token_url,
                self._clip_text(exc),
            )
            self.log.warning("OAuth token fetch failed: %s", exc)
            return False

    def _ensure_access_token(self) -> None:
        if self.dry_run:
            return
        if self.access_token and time.monotonic() < self._token_expires_at:
            return
        if self.access_token and self._token_expires_at == 0.0:
            # Static token provided externally.
            return
        self._refresh_access_token()

    def _build_headers(self, accept: str | None = None) -> dict[str, str]:
        self._ensure_access_token()
        headers: dict[str, str] = {}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        if accept:
            headers["Accept"] = accept
        return headers

    def get_patient(self, identifier: str) -> dict[str, Any] | None:
        if self.dry_run:
            key = str(identifier).strip()
            if key in self._dry_patients_by_identifier:
                pid = self._dry_patients_by_identifier[key]
            elif key.isdigit():
                pid = key
                self._dry_patients_by_identifier[key] = pid
            else:
                pid = f"PAT-{next(self._dummy_counter)}"
                self._dry_patients_by_identifier[key] = pid
            return {
                "patient_id": pid,
                "encounter_id": None,
            }

        encoded_identifier = quote(str(identifier), safe="")
        url = f"{self.base_url}/api/patient?Identifier={encoded_identifier}"
        headers = self._build_headers(accept="application/fhir+json")
        for attempt in range(1, self.retries + 2):
            try:
                start_ts = time.perf_counter()
                response = requests.get(
                    url,
                    headers=headers,
                    timeout=self.timeout_sec,
                    verify=self.verify_ssl,
                )
                if response.status_code == 404:
                    self.log.debug(
                        "patient_lookup miss identifier=%s status=404 latency_ms=%s url=%s",
                        identifier,
                        self._latency_ms(start_ts),
                        url,
                    )
                    return None
                if response.status_code == 401 and self._refresh_access_token():
                    headers = self._build_headers(accept="application/fhir+json")
                    start_ts = time.perf_counter()
                    response = requests.get(
                        url,
                        headers=headers,
                        timeout=self.timeout_sec,
                        verify=self.verify_ssl,
                    )
                latency_ms = self._latency_ms(start_ts)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    self.log.warning("Patient API returned non-object payload for identifier %s", identifier)
                    return None

                data = payload.get("data")
                if not isinstance(data, list) or not data:
                    return None

                first = data[0]
                if not isinstance(first, dict):
                    return None

                patient_id = first.get("pid", first.get("id"))
                if patient_id is None:
                    return None

                self.log.debug(
                    "patient_lookup ok identifier=%s patient_id=%s status=%s latency_ms=%s url=%s",
                    identifier,
                    patient_id,
                    response.status_code,
                    latency_ms,
                    url,
                )

                return {
                    "patient_id": str(patient_id),
                    "encounter_id": None,
                    "patient_uuid": first.get("uuid"),
                }
            except requests.RequestException as exc:
                self.log.debug(
                    "patient_lookup retry identifier=%s attempt=%s error=%s",
                    identifier,
                    attempt,
                    self._clip_text(exc),
                )
                if attempt > self.retries:
                    self.log.warning("Patient API failed for identifier %s: %s", identifier, exc)
                    return None
                time.sleep(self.backoff_sec * attempt)

        return None

    def get_device_assignment(self, lookup_device_id: str, patient_id: str | None = None) -> dict[str, Any] | None:
        if self.dry_run:
            key = str(lookup_device_id)
            existing_pid = self._dry_assignments_by_device.get(key)

            if patient_id is None:
                if existing_pid is None:
                    return None
                return {
                    "patient_id": existing_pid,
                    "device_id": key,
                    "encounter_id": None,
                }

            resolved_pid = str(patient_id)
            self._dry_assignments_by_device[key] = resolved_pid
            return {
                "patient_id": resolved_pid,
                "device_id": key,
                "encounter_id": None,
            }

        encoded_id = quote(str(lookup_device_id), safe="")
        url = f"{self.base_url}/hemodynamics/device_id/{encoded_id}"
        headers = self._build_headers(accept="application/json")

        for attempt in range(1, self.retries + 2):
            try:
                start_ts = time.perf_counter()
                response = requests.get(
                    url,
                    headers=headers,
                    timeout=self.timeout_sec,
                    verify=self.verify_ssl,
                )
                if response.status_code == 401 and self._refresh_access_token():
                    headers = self._build_headers(accept="application/json")
                    start_ts = time.perf_counter()
                    response = requests.get(
                        url,
                        headers=headers,
                        timeout=self.timeout_sec,
                        verify=self.verify_ssl,
                    )
                if response.status_code == 404:
                    self.log.debug(
                        "device_assignment miss device_id=%s patient_id=%s status=404 latency_ms=%s url=%s",
                        lookup_device_id,
                        patient_id if patient_id is not None else "<any>",
                        self._latency_ms(start_ts),
                        url,
                    )
                    return None
                latency_ms = self._latency_ms(start_ts)
                response.raise_for_status()

                payload = response.json()
                if not isinstance(payload, dict):
                    return None
                data = payload.get("data")
                if not isinstance(data, list):
                    return None

                rows = [row for row in data if isinstance(row, dict)]
                if patient_id is not None:
                    rows = [row for row in rows if str(row.get("pid")) == str(patient_id)]
                if not rows:
                    return None

                def _sort_key(item: dict[str, Any]) -> tuple[int, str]:
                    return (int(item.get("id", 0)), str(item.get("timestamp") or item.get("created_at") or ""))

                row = sorted(rows, key=_sort_key, reverse=True)[0]
                mapped_id = row.get("device_id")
                if not mapped_id:
                    return None

                # Simulator contract: keep simulator/lookup device id stable for outbound posts.
                # We still require EMR row existence for the patient assignment check.
                if str(mapped_id) != str(lookup_device_id):
                    self.log.info(
                        "EMR assignment device_id differs from lookup id (lookup=%s, emr=%s). Using lookup id.",
                        lookup_device_id,
                        mapped_id,
                    )

                self.log.debug(
                    "device_assignment ok lookup_device_id=%s patient_id=%s status=%s latency_ms=%s url=%s",
                    lookup_device_id,
                    row.get("pid"),
                    response.status_code,
                    latency_ms,
                    url,
                )

                return {
                    "patient_id": str(row.get("pid")),
                    "device_id": str(lookup_device_id),
                    "encounter_id": row.get("encounter"),
                }
            except requests.RequestException as exc:
                self.log.debug(
                    "device_assignment retry lookup_device_id=%s patient_id=%s attempt=%s error=%s",
                    lookup_device_id,
                    patient_id if patient_id is not None else "<any>",
                    attempt,
                    self._clip_text(exc),
                )
                if attempt > self.retries:
                    self.log.warning(
                        "Device assignment API failed for lookup_id=%s patient_id=%s: %s",
                        lookup_device_id,
                        patient_id if patient_id is not None else "<any>",
                        exc,
                    )
                    return None
                time.sleep(self.backoff_sec * attempt)

        return None

    def send_device_data(self, payload: dict[str, Any]) -> tuple[bool, str]:
        if self.dry_run:
            # Simulate occasional transient failures in dry-run mode to exercise retry paths.
            if random.random() < 0.02:
                return False, "dry_run_transient_failure"
            return True, "dry_run_ok"

        url = f"{self.base_url}/hemodynamics"
        headers = self._build_headers()
        headers["Content-Type"] = "application/json"
        for attempt in range(1, self.retries + 2):
            try:
                start_ts = time.perf_counter()
                response = requests.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout_sec,
                    verify=self.verify_ssl,
                )
                if response.status_code == 401 and self._refresh_access_token():
                    headers = self._build_headers()
                    headers["Content-Type"] = "application/json"
                    start_ts = time.perf_counter()
                    response = requests.post(
                        url,
                        json=payload,
                        headers=headers,
                        timeout=self.timeout_sec,
                        verify=self.verify_ssl,
                    )
                latency_ms = self._latency_ms(start_ts)
                response.raise_for_status()
                self.log.debug(
                    "hemodynamics_post ok device_id=%s pid=%s status=%s latency_ms=%s url=%s",
                    payload.get("device_id", "unknown"),
                    payload.get("pid", "unknown"),
                    response.status_code,
                    latency_ms,
                    url,
                )
                return True, f"http_{response.status_code}"
            except requests.RequestException as exc:
                resp = exc.response
                self.log.debug(
                    "hemodynamics_post retry device_id=%s pid=%s attempt=%s http_status=%s error=%s response=%s",
                    payload.get("device_id", "unknown"),
                    payload.get("pid", "unknown"),
                    attempt,
                    resp.status_code if resp is not None else "n/a",
                    self._clip_text(exc),
                    self._clip_text(resp.text) if resp is not None else "",
                )
                if attempt > self.retries:
                    self.log.warning(
                        "Device data POST failed for %s: %s",
                        payload.get("device_id", "unknown"),
                        exc,
                    )
                    return False, str(exc)
                time.sleep(self.backoff_sec * attempt)

        return False, "unknown_error"

    def send_device_status(
        self,
        lookup_device_id: str,
        patient_id: str,
        status: str = "Inactive",
    ) -> tuple[bool, str]:
        if not lookup_device_id or not patient_id:
            return False, "missing_device_or_patient"

        if self.dry_run:
            return True, "dry_run_status_inactive"

        encoded_id = quote(str(lookup_device_id), safe="")
        url = f"{self.base_url}/hemodynamics/device_id/{encoded_id}/status"
        headers = self._build_headers()
        headers["Content-Type"] = "application/json"

        payload = {
            "pid": int(patient_id) if str(patient_id).isdigit() else str(patient_id),
            "status": status,
        }

        for attempt in range(1, self.retries + 2):
            try:
                start_ts = time.perf_counter()
                response = requests.put(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout_sec,
                    verify=self.verify_ssl,
                )
                if response.status_code == 401 and self._refresh_access_token():
                    headers = self._build_headers()
                    headers["Content-Type"] = "application/json"
                    start_ts = time.perf_counter()
                    response = requests.put(
                        url,
                        json=payload,
                        headers=headers,
                        timeout=self.timeout_sec,
                        verify=self.verify_ssl,
                    )
                latency_ms = self._latency_ms(start_ts)
                response.raise_for_status()
                self.log.debug(
                    "device_status_put ok lookup_device_id=%s pid=%s status=%s latency_ms=%s url=%s",
                    lookup_device_id,
                    payload.get("pid"),
                    response.status_code,
                    latency_ms,
                    url,
                )
                return True, f"status_http_{response.status_code}"
            except requests.RequestException as exc:
                resp = exc.response
                self.log.debug(
                    "device_status_put retry lookup_device_id=%s pid=%s attempt=%s http_status=%s error=%s response=%s",
                    lookup_device_id,
                    payload.get("pid"),
                    attempt,
                    resp.status_code if resp is not None else "n/a",
                    self._clip_text(exc),
                    self._clip_text(resp.text) if resp is not None else "",
                )
                if attempt > self.retries:
                    self.log.warning(
                        "Device status PUT failed for lookup_id=%s patient_id=%s: %s",
                        lookup_device_id,
                        patient_id,
                        exc,
                    )
                    return False, str(exc)
                time.sleep(self.backoff_sec * attempt)

        return False, "unknown_error"
