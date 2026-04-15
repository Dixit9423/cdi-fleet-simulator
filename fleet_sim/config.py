"""
fleet_sim/config.py
───────────────────
Load and validate devices_config.yaml.  Resolves cert paths,
builds typed dicts for each section.
"""

import os
import sys
from pathlib import Path

import yaml


def _expand(p: str) -> str:
    """Expand ~, env vars, and make absolute."""
    return str(Path(os.path.expandvars(os.path.expanduser(p))).resolve())


def load_config(config_path: str) -> dict:
    """Read YAML and return validated config dict."""
    cfg_file = Path(config_path)
    if not cfg_file.exists():
        print(f"[Config] ERROR: config file not found: {cfg_file}")
        sys.exit(1)

    with open(cfg_file, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    # ── Server ───────────────────────────────────────────────────────────
    srv = cfg.get("server", {})
    srv.setdefault("host", "localhost")
    srv.setdefault("port", 5555)
    tls = srv.get("tls", {})
    tls.setdefault("enabled", False)
    if tls.get("enabled"):
        cert_dir = _expand(tls.get("cert_dir", "~/Downloads/cert1"))
        tls["ca_cert_path"]     = os.path.join(cert_dir, tls.get("ca_cert", "ca.crt"))
        tls["client_cert_path"] = os.path.join(cert_dir, tls.get("client_cert", "client.crt"))
        tls["client_key_path"]  = os.path.join(cert_dir, tls.get("client_key", "client.key"))
        tls["server_cert_path"] = os.path.join(cert_dir, tls.get("server_cert", "server.crt"))
        tls.setdefault("server_name_override", None)
    srv["tls"] = tls

    # ── Param catalog (key → int) ────────────────────────────────────────
    raw_cat = cfg.get("param_catalog", {})
    param_catalog: dict[int, dict] = {}
    for k, v in raw_cat.items():
        param_catalog[int(k)] = v

    # ── Profiles ─────────────────────────────────────────────────────────
    profiles = cfg.get("profiles", {})
    for pname, pdef in profiles.items():
        pdef.setdefault("do2i_threshold", 280)
        pdef.setdefault("manual_hgb", 12.5)
        pdef.setdefault("manual_so2", 65)
        pdef.setdefault("flow_source", "Flow_Red")
        pdef["param_ids"] = [int(x) for x in pdef.get("param_ids", [])]

    # ── Devices ──────────────────────────────────────────────────────────
    devices = cfg.get("devices", [])
    for d in devices:
        d["device_id"] = f"CDI-{d['serial']}"
        d.setdefault("sw_version", "1.0.0")
        d.setdefault("site", "UNKNOWN")
        d.setdefault("initial_state", "IDLE")
        d.setdefault("patient_id", None)
        d.setdefault("profile", None)
        d.setdefault("probes", {})
        # Normalise tick_data keys to int
        td = d.get("tick_data", {})
        d["tick_data"] = {int(k): v for k, v in td.items()} if td else {}

    return {
        "server": srv,
        "param_catalog": param_catalog,
        "profiles": profiles,
        "devices": devices,
    }
