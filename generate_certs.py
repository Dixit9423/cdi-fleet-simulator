#!/usr/bin/env python3
"""
generate_certs.py  —  Generate self-signed CA + client certificates for mTLS testing.

Creates the following in ~/Downloads/cert1/:
  ca.key          CA private key
  ca.crt          CA certificate (self-signed, 10 year validity)
  client.key      Client private key
  client.crt      Client certificate (signed by CA, 1 year validity)
  server.key      Server private key  (bonus, for running a test server)
  server.crt      Server certificate (signed by CA, SAN=device-manager,localhost)

All certificates use RSA 2048-bit keys compatible with TLS 1.3.
"""

import ipaddress
import os
import sys
import datetime

try:
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
except ImportError:
    print("ERROR: 'cryptography' package is required.")
    print("  pip install cryptography")
    sys.exit(1)


def gen_key():
    """Generate RSA-2048 private key."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def write_key(path, key):
    with open(path, "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))
    print(f"  ✓ {os.path.basename(path)}")


def write_cert(path, cert):
    with open(path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    print(f"  ✓ {os.path.basename(path)}")


def main():
    out_dir = os.path.join(os.path.expanduser("~"), "Downloads", "cert1")
    os.makedirs(out_dir, exist_ok=True)

    print()
    print("=" * 60)
    print("  CDI mTLS Certificate Generator")
    print("=" * 60)
    print(f"  Output: {out_dir}")
    print()

    now = datetime.datetime.now(datetime.timezone.utc)

    # ── 1. CA ────────────────────────────────────────────────────────────
    print("[1/3] Generating CA...")
    ca_key = gen_key()
    ca_name = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "CDI OneView"),
        x509.NameAttribute(NameOID.COMMON_NAME, "CDI-Telemetry-CA"),
    ])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_cert_sign=True, crl_sign=True,
                content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    write_key(os.path.join(out_dir, "ca.key"), ca_key)
    write_cert(os.path.join(out_dir, "ca.crt"), ca_cert)

    # ── 2. Client cert ───────────────────────────────────────────────────
    print("[2/3] Generating client cert...")
    client_key = gen_key()
    client_name = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "CDI OneView"),
        x509.NameAttribute(NameOID.COMMON_NAME, "cdi-core-client"),
    ])
    client_cert = (
        x509.CertificateBuilder()
        .subject_name(client_name)
        .issuer_name(ca_name)
        .public_key(client_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("cdi-core-client"),
            ]),
            critical=False,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    write_key(os.path.join(out_dir, "client.key"), client_key)
    write_cert(os.path.join(out_dir, "client.crt"), client_cert)

    # ── 3. Server cert (bonus — for test gRPC server) ────────────────────
    print("[3/3] Generating server cert...")
    server_key = gen_key()
    server_name = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "CDI OneView"),
        x509.NameAttribute(NameOID.COMMON_NAME, "device-manager"),
    ])
    server_cert = (
        x509.CertificateBuilder()
        .subject_name(server_name)
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("device-manager"),
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
            ]),
            critical=False,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    write_key(os.path.join(out_dir, "server.key"), server_key)
    write_cert(os.path.join(out_dir, "server.crt"), server_cert)

    print()
    print("=" * 60)
    print("  All certificates generated successfully!")
    print("=" * 60)
    print(f"""
  Files in {out_dir}:
    ca.key / ca.crt        — Certificate Authority
    client.key / client.crt — Client (for Fleet Simulator)
    server.key / server.crt — Server (for test gRPC server)

  Usage with Fleet Simulator:
    python run_fleet.py

  Usage with test gRPC server:
    Set CDI_TELEMETRY_CA_CERT, CDI_TELEMETRY_CLIENT_CERT, CDI_TELEMETRY_CLIENT_KEY
    environment variables, or update devices_config.yaml cert_dir.
""")


if __name__ == "__main__":
    main()
