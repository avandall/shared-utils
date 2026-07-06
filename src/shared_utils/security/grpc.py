from __future__ import annotations

import os
from pathlib import Path

import grpc


def _enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def grpc_server_tls_enabled() -> bool:
    return _enabled(os.getenv("GRPC_TLS_ENABLED"))


def grpc_client_tls_enabled() -> bool:
    return _enabled(os.getenv("GRPC_CLIENT_TLS_ENABLED"))


def add_configured_grpc_port(server: grpc.Server, host: str, port: int) -> int:
    address = f"{host}:{port}"
    if not grpc_server_tls_enabled():
        return server.add_insecure_port(address)

    cert_file = os.getenv("GRPC_TLS_CERT_FILE")
    key_file = os.getenv("GRPC_TLS_KEY_FILE")
    if not cert_file or not key_file:
        raise RuntimeError("GRPC_TLS_ENABLED requires GRPC_TLS_CERT_FILE and GRPC_TLS_KEY_FILE")

    cert_chain = Path(cert_file).read_bytes()
    private_key = Path(key_file).read_bytes()
    client_ca_file = os.getenv("GRPC_TLS_CLIENT_CA_FILE")
    client_ca = Path(client_ca_file).read_bytes() if client_ca_file else None
    credentials = grpc.ssl_server_credentials(
        ((private_key, cert_chain),),
        root_certificates=client_ca,
        require_client_auth=client_ca is not None,
    )
    return server.add_secure_port(address, credentials)


def configured_grpc_channel(address: str) -> grpc.Channel:
    if not grpc_client_tls_enabled():
        return grpc.insecure_channel(address)

    root_cert_file = os.getenv("GRPC_CLIENT_ROOT_CERT_FILE")
    root_certificates = Path(root_cert_file).read_bytes() if root_cert_file else None
    cert_file = os.getenv("GRPC_CLIENT_CERT_FILE")
    key_file = os.getenv("GRPC_CLIENT_KEY_FILE")
    if bool(cert_file) != bool(key_file):
        raise RuntimeError("GRPC_CLIENT_CERT_FILE and GRPC_CLIENT_KEY_FILE must be set together")

    certificate_chain = Path(cert_file).read_bytes() if cert_file else None
    private_key = Path(key_file).read_bytes() if key_file else None
    credentials = grpc.ssl_channel_credentials(
        root_certificates=root_certificates,
        private_key=private_key,
        certificate_chain=certificate_chain,
    )
    return grpc.secure_channel(address, credentials)
