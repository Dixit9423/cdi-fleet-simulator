from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from simulator.core.models import DeviceConfig, EMROAuthConfig, ParameterDefinition


class ConfigError(ValueError):
    pass


def _load_yaml_file(file_path: Path) -> dict[str, Any]:
    if not file_path.exists():
        raise ConfigError(f"Config file not found: {file_path}")
    with file_path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ConfigError(f"Config root must be a mapping: {file_path}")
    return payload


def load_emr_api_base_url(file_path: str | Path, default: str | None = None) -> str | None:
    path = Path(file_path)
    if not path.exists():
        return default

    payload = _load_yaml_file(path)
    raw_value = payload.get("api_base_url", payload.get("emr_api_base_url"))
    if raw_value is None:
        return default

    value = str(raw_value).strip()
    return value or default


def _parse_bool(raw_value: Any, field_name: str) -> bool:
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, str):
        value = raw_value.strip().lower()
        if value in ("true", "1", "yes", "y", "on"):
            return True
        if value in ("false", "0", "no", "n", "off"):
            return False
    raise ConfigError(f"Invalid boolean for '{field_name}': {raw_value!r}")


def load_emr_tls_verify(file_path: str | Path, default: bool | str | None = None) -> bool | str | None:
    path = Path(file_path)
    if not path.exists():
        return default

    payload = _load_yaml_file(path)
    tls_cfg = payload.get("tls") if isinstance(payload.get("tls"), dict) else {}

    raw_ca_path = payload.get("ca_cert_path", tls_cfg.get("ca_cert_path"))
    if raw_ca_path is not None:
        ca_path = str(raw_ca_path).strip()
        if ca_path:
            return ca_path

    raw_verify = payload.get("verify_ssl", tls_cfg.get("verify_ssl"))
    if raw_verify is None:
        return default

    return _parse_bool(raw_verify, "verify_ssl")


def load_parameter_definitions(file_path: str | Path) -> list[ParameterDefinition]:
    path = Path(file_path)
    payload = _load_yaml_file(path)
    raw_parameters = payload.get("parameters", payload)
    if not isinstance(raw_parameters, dict):
        raise ConfigError("Parameter config must contain a 'parameters' mapping")

    definitions: list[ParameterDefinition] = []
    for name, cfg in raw_parameters.items():
        if not isinstance(cfg, dict):
            raise ConfigError(f"Parameter '{name}' config must be an object")

        try:
            definition = ParameterDefinition(
                name=str(name),
                display_name=str(cfg["display_name"]),
                unit=str(cfg["unit"]),
                category=str(cfg["category"]),
                min_value=float(cfg["min"]),
                max_value=float(cfg["max"]),
            )
            definition.validate()
        except KeyError as exc:
            raise ConfigError(f"Parameter '{name}' missing required field: {exc}") from exc

        definitions.append(definition)

    _validate_category_mapping(definitions)
    return definitions


def _validate_category_mapping(definitions: list[ParameterDefinition]) -> None:
    required_categories = {
        "map": "Arterial",
        "systolic_bp": "Arterial",
        "diastolic_bp": "Arterial",
        "etco2": "Venous",
    }
    all_other = {
        "heart_rate",
        "arterial_flow",
        "sweep",
        "fio2",
        "rso2",
        "act",
        "lactate",
        "glucose",
    }

    by_name = {item.name: item for item in definitions}

    for key, category in required_categories.items():
        if key not in by_name:
            raise ConfigError(f"Required parameter missing: {key}")
        if by_name[key].category != category:
            raise ConfigError(f"Parameter '{key}' must use category '{category}'")

    for key in all_other:
        if key not in by_name:
            raise ConfigError(f"Required parameter missing: {key}")
        if by_name[key].category != "Other":
            raise ConfigError(f"Parameter '{key}' must use category 'Other'")


def load_device_configs(file_path: str | Path) -> list[DeviceConfig]:
    path = Path(file_path)
    payload = _load_yaml_file(path)
    raw_devices = payload.get("devices")
    if not isinstance(raw_devices, list):
        raise ConfigError("Device config must contain a 'devices' list")

    devices: list[DeviceConfig] = []
    for cfg in raw_devices:
        if not isinstance(cfg, dict):
            raise ConfigError("Each device config must be an object")

        try:
            device = DeviceConfig(
                device_id=str(cfg["device_id"]),
                mode=str(cfg.get("mode", "emr")).lower(),
                emr_lookup_device_id=str(cfg.get("emr_lookup_device_id", cfg.get("device_id", ""))),
                device_type=str(cfg.get("device_type", "Terumo")),
                patient_identifier=str(cfg.get("patient_identifier", "1")),
                notes=str(cfg.get("notes", "Patient stable")),
                poll_interval=int(cfg.get("poll_interval", 5)),
                send_interval=int(cfg.get("send_interval", 1)),
                enabled=bool(cfg.get("enabled", True)),
                case=str(cfg.get("case", cfg.get("scenario", "within_range"))).lower(),
                profile=str(cfg.get("profile", "hemo_monitor_baseline")),
            )
            device.validate()
        except KeyError as exc:
            raise ConfigError(f"Device config missing required field: {exc}") from exc

        devices.append(device)

    return devices


def load_emr_oauth_config(file_path: str | Path) -> EMROAuthConfig | None:
    path = Path(file_path)
    if not path.exists():
        return None

    payload = _load_yaml_file(path)
    oauth = payload.get("oauth", payload)
    if not isinstance(oauth, dict) or not oauth:
        return None

    required = (
        "token_url",
        "client_id",
        "client_secret",
        "username",
        "password",
    )
    values = {key: str(oauth.get(key, "")).strip() for key in required}
    if all(not value for value in values.values()):
        return None
    if any(not value for value in values.values()):
        return None

    cfg = EMROAuthConfig(
        token_url=str(oauth.get("token_url", "")),
        client_id=str(oauth.get("client_id", "")),
        client_secret=str(oauth.get("client_secret", "")),
        username=str(oauth.get("username", "")),
        password=str(oauth.get("password", "")),
        user_role=str(oauth.get("user_role", "users")),
        scope=str(oauth.get("scope", "api:oemr")),
        grant_type=str(oauth.get("grant_type", "password")),
    )
    cfg.validate()
    return cfg


def load_emr_profiles(file_path: str | Path, known_params: set[str]) -> dict[str, dict[str, Any]]:
    path = Path(file_path)
    payload = _load_yaml_file(path)
    raw_profiles = payload.get("profiles")
    if not isinstance(raw_profiles, dict) or not raw_profiles:
        raise ConfigError("EMR profiles config must contain a non-empty 'profiles' mapping")

    profiles: dict[str, dict[str, Any]] = {}
    for profile_name, profile_cfg in raw_profiles.items():
        if not isinstance(profile_cfg, dict):
            raise ConfigError(f"Profile '{profile_name}' must be an object")

        phase_sequence = profile_cfg.get("phase_sequence")
        targets = profile_cfg.get("targets")
        phase_seconds = int(profile_cfg.get("phase_seconds", 45))

        if not isinstance(phase_sequence, list) or not phase_sequence:
            raise ConfigError(f"Profile '{profile_name}' must define non-empty phase_sequence")
        if not isinstance(targets, dict) or not targets:
            raise ConfigError(f"Profile '{profile_name}' must define non-empty targets")
        if phase_seconds < 5:
            raise ConfigError(f"Profile '{profile_name}' phase_seconds must be >= 5")

        for phase in phase_sequence:
            if phase not in targets:
                raise ConfigError(f"Profile '{profile_name}' phase '{phase}' missing from targets")

        for phase_name, phase_targets in targets.items():
            if not isinstance(phase_targets, dict):
                raise ConfigError(f"Profile '{profile_name}' phase '{phase_name}' targets must be an object")
            unknown_params = set(phase_targets.keys()) - known_params
            if unknown_params:
                unknown = ", ".join(sorted(unknown_params))
                raise ConfigError(f"Profile '{profile_name}' phase '{phase_name}' has unknown params: {unknown}")

        profiles[str(profile_name)] = {
            "description": str(profile_cfg.get("description", "")),
            "phase_seconds": phase_seconds,
            "phase_sequence": [str(x) for x in phase_sequence],
            "targets": {
                str(phase): {str(k): float(v) for k, v in phase_targets.items()}
                for phase, phase_targets in targets.items()
            },
            "volatility": float(profile_cfg.get("volatility", 1.0)),
        }

    return profiles
