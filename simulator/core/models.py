from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


ParameterCategory = Literal["Arterial", "Venous", "Other"]
SimulationCase = Literal["within_range", "outside_range", "low_limit", "high_limit", "mixed"]
DeviceMode = Literal["cdi", "emr"]


@dataclass(frozen=True)
class ParameterDefinition:
    name: str
    display_name: str
    unit: str
    category: ParameterCategory
    min_value: float
    max_value: float

    def validate(self) -> None:
        if self.min_value >= self.max_value:
            raise ValueError(f"Parameter '{self.name}' has invalid range: {self.min_value} >= {self.max_value}")
        if self.category not in ("Arterial", "Venous", "Other"):
            raise ValueError(f"Parameter '{self.name}' has invalid category: {self.category}")


@dataclass
class ParameterState:
    definition: ParameterDefinition
    current_value: float


@dataclass(frozen=True)
class DeviceConfig:
    device_id: str
    mode: DeviceMode
    emr_lookup_device_id: str = ""
    device_type: str = "Terumo"
    patient_identifier: str = "1"
    notes: str = "Patient stable"
    poll_interval: int = 5
    send_interval: int = 1
    enabled: bool = True
    case: SimulationCase = "within_range"
    profile: str = "hemo_monitor_baseline"

    def validate(self) -> None:
        if self.mode not in ("cdi", "emr"):
            raise ValueError(f"Device '{self.device_id}' has unsupported mode '{self.mode}'")
        if self.mode == "emr" and not self.emr_lookup_device_id.strip():
            raise ValueError(f"Device '{self.device_id}' must define a non-empty emr_lookup_device_id")
        if not self.device_type.strip():
            raise ValueError(f"Device '{self.device_id}' must define a non-empty device_type")
        if not self.patient_identifier.strip():
            raise ValueError(f"Device '{self.device_id}' must define a non-empty patient_identifier")
        if self.poll_interval < 1:
            raise ValueError(f"Device '{self.device_id}' poll_interval must be >= 1")
        if self.send_interval < 1:
            raise ValueError(f"Device '{self.device_id}' send_interval must be >= 1")
        if self.case not in ("within_range", "outside_range", "low_limit", "high_limit", "mixed"):
            raise ValueError(f"Device '{self.device_id}' has unsupported case '{self.case}'")
        if not self.profile:
            raise ValueError(f"Device '{self.device_id}' must define a non-empty profile")


@dataclass
class EMRDeviceStatus:
    device: DeviceConfig
    active: bool = False
    patient_id: str | None = None
    encounter_id: str | None = None
    last_sent: str | None = None
    last_error: str | None = None
    last_values: dict[str, float] = field(default_factory=dict)
    last_payload_status: str | None = None


@dataclass(frozen=True)
class EMROAuthConfig:
    token_url: str
    client_id: str
    client_secret: str
    username: str
    password: str
    user_role: str = "users"
    scope: str = "api:oemr"
    grant_type: str = "password"

    def validate(self) -> None:
        if not self.token_url.strip():
            raise ValueError("OAuth token_url must be non-empty")
        if not self.client_id.strip():
            raise ValueError("OAuth client_id must be non-empty")
        if not self.client_secret.strip():
            raise ValueError("OAuth client_secret must be non-empty")
        if not self.username.strip():
            raise ValueError("OAuth username must be non-empty")
        if not self.password.strip():
            raise ValueError("OAuth password must be non-empty")
