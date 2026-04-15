"""
fleet_sim/mtls.py
─────────────────
Create gRPC channels — mTLS 1.3 (secure) or insecure.
"""

import os
import sys
import grpc

try:
    from cryptography import x509
except ImportError:
    x509 = None


def _read_bytes(path: str) -> bytes:
    """Read file as bytes; exit if missing."""
    if not os.path.isfile(path):
        print(f"[mTLS] ERROR: certificate file not found: {path}")
        sys.exit(1)
    with open(path, "rb") as f:
        return f.read()


def _warn_if_host_not_in_server_cert(host: str, server_cert_path: str, override_name: str | None):
    """
    Print diagnostics if the requested host/override is not present in cert SAN.

    This is a preflight check only; final verification is done by gRPC/OpenSSL.
    """
    if x509 is None:
        print("[mTLS] INFO: cryptography not installed; skipping SAN preflight check")
        return

    if not server_cert_path or not os.path.isfile(server_cert_path):
        print("[mTLS] INFO: server cert file not provided/found; skipping SAN preflight check")
        return

    try:
        cert = x509.load_pem_x509_certificate(_read_bytes(server_cert_path))
        try:
            san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            dns_names = san.get_values_for_type(x509.DNSName)
            ip_names = [str(ip) for ip in san.get_values_for_type(x509.IPAddress)]
        except Exception:
            dns_names = []
            ip_names = []

        verify_name = override_name if override_name else host
        ok = verify_name in dns_names or verify_name in ip_names

        print(f"[mTLS] Server cert SAN DNS: {dns_names}")
        print(f"[mTLS] Server cert SAN IP : {ip_names}")
        print(f"[mTLS] Verification name  : {verify_name}")

        if not ok:
            print("[mTLS] WARNING: Verification name is not present in server certificate SAN.")
            print("[mTLS]          TLS handshake is expected to fail with hostname mismatch.")
            print("[mTLS]          Fix by either:")
            print("[mTLS]            1) using server host that matches cert SAN, or")
            print("[mTLS]            2) setting tls.server_name_override in YAML, or")
            print("[mTLS]            3) regenerating server cert with target IP/DNS in SAN.")
    except Exception as e:
        print(f"[mTLS] INFO: failed SAN preflight check: {e}")


def create_channel(server_cfg: dict) -> grpc.Channel:
    """
    Return a gRPC channel based on server config.

    If tls.enabled is True, creates an mTLS channel using:
      - ca_cert_path      (root CA — verifies server)
      - client_cert_path  (client certificate — sent to server)
      - client_key_path   (client private key)

    Otherwise returns an insecure channel.
    """
    target = f"{server_cfg['host']}:{server_cfg['port']}"
    tls = server_cfg.get("tls", {})

    if tls.get("enabled"):
        ca_cert    = _read_bytes(tls["ca_cert_path"])
        client_cert = _read_bytes(tls["client_cert_path"])
        client_key  = _read_bytes(tls["client_key_path"])

        credentials = grpc.ssl_channel_credentials(
            root_certificates=ca_cert,
            private_key=client_key,
            certificate_chain=client_cert,
        )

        # Channel options:
        #   - grpc.ssl_target_name_override should only be set when needed
        #     (for example, when connecting by IP but cert contains DNS name)
        #   - min TLS 1.3 is negotiated automatically when both sides support it
        override_name = tls.get("server_name_override")

        _warn_if_host_not_in_server_cert(
            host=server_cfg["host"],
            server_cert_path=tls.get("server_cert_path", ""),
            override_name=override_name,
        )

        # Keepalive is intentionally disabled by default to avoid server-side
        # GOAWAY ENHANCE_YOUR_CALM("too_many_pings").
        # Enable only when explicitly configured in YAML.
        options = []

        keepalive_time_ms = tls.get("keepalive_time_ms")
        keepalive_timeout_ms = tls.get("keepalive_timeout_ms", 20000)
        keepalive_permit_without_calls = tls.get("keepalive_permit_without_calls", False)
        if keepalive_time_ms:
            options.append(("grpc.keepalive_time_ms", int(keepalive_time_ms)))
            options.append(("grpc.keepalive_timeout_ms", int(keepalive_timeout_ms)))
            options.append(("grpc.keepalive_permit_without_calls", int(bool(keepalive_permit_without_calls))))
        if override_name:
            options.append(("grpc.ssl_target_name_override", override_name))
            options.append(("grpc.default_authority", override_name))

        print(f"[mTLS] Creating SECURE channel → {target}")
        print(f"[mTLS]   CA cert  : {tls['ca_cert_path']}")
        print(f"[mTLS]   Client   : {tls['client_cert_path']}")
        print(f"[mTLS]   Key      : {tls['client_key_path']}")
        if override_name:
            print(f"[mTLS]   Verify as: {override_name} (override)")
        else:
            print(f"[mTLS]   Verify as: {server_cfg['host']} (target host)")
        if keepalive_time_ms:
            print(f"[mTLS]   Keepalive : ON ({keepalive_time_ms}ms)")
        else:
            print("[mTLS]   Keepalive : OFF (default)")
        return grpc.secure_channel(target, credentials, options=options)

    else:
        print(f"[Chan] Creating INSECURE channel → {target}")
        return grpc.insecure_channel(target)
