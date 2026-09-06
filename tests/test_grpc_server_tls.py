"""Serving gRPC over TLS, and demanding a certificate back.

A bearer token in a header authenticates the CALLER and says nothing about the
PEER, and on a plaintext port it is readable by anyone on the wire. A client
certificate is the other half: the caller proves it holds a private key that
never travels, and the server proves who it is before the caller sends
anything.

These use a real handshake rather than asserting on the arguments passed to
grpc: the question is whether a caller without a certificate is actually
refused, and only a connection can answer it.
"""

from __future__ import annotations

import datetime as dt

import grpc
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from service_toolkit.grpc import ServerTls, create_grpc_server, peer_certificate_pem


def _pair(
    common_name: str,
    *,
    issuer: tuple[x509.Certificate, ec.EllipticCurvePrivateKey] | None = None,
    ca: bool = False,
) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    """A certificate and its key, self-signed or signed by *issuer*."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    issuer_cert, issuer_key = issuer or (None, key)
    now = dt.datetime.now(dt.UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(issuer_cert.subject if issuer_cert else name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    )
    if not ca:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False
        )
    return builder.sign(issuer_key, algorithm=hashes.SHA256()), key


def _pem(certificate: x509.Certificate) -> bytes:
    return certificate.public_bytes(serialization.Encoding.PEM)


def _key_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


@pytest.fixture
def pki() -> dict:
    """A CA, a server certificate it signed, and a client certificate."""
    ca_cert, ca_key = _pair("test CA", ca=True)
    server_cert, server_key = _pair("localhost", issuer=(ca_cert, ca_key))
    client_cert, client_key = _pair("host:node-1", issuer=(ca_cert, ca_key))
    stranger_ca_cert, stranger_ca_key = _pair("other CA", ca=True)
    stranger_cert, stranger_key = _pair(
        "host:node-1", issuer=(stranger_ca_cert, stranger_ca_key)
    )
    return {
        "ca": _pem(ca_cert),
        "server": (_pem(server_cert), _key_pem(server_key)),
        "client": (_pem(client_cert), _key_pem(client_key)),
        "stranger": (_pem(stranger_cert), _key_pem(stranger_key)),
    }


async def _serve(port: int, tls: ServerTls | None) -> grpc.aio.Server:
    server = create_grpc_server(port=port, tls=tls, reflection_enabled=False)
    await server.start()
    return server


async def _health(channel: grpc.aio.Channel) -> bool:
    """Whether the connection completed. The health service is registered by
    `create_grpc_server`, so it needs no servicer of our own."""
    from grpc_health.v1 import health_pb2, health_pb2_grpc

    stub = health_pb2_grpc.HealthStub(channel)
    try:
        await stub.Check(health_pb2.HealthCheckRequest(), timeout=5)
    except grpc.aio.AioRpcError:
        return False
    return True


@pytest.mark.asyncio
async def test_a_client_with_a_signed_certificate_is_admitted(pki: dict) -> None:
    server = await _serve(
        50231,
        ServerTls(
            certificate=pki["server"][0],
            private_key=pki["server"][1],
            client_ca=pki["ca"],
        ),
    )
    try:
        credentials = grpc.ssl_channel_credentials(
            root_certificates=pki["ca"],
            private_key=pki["client"][1],
            certificate_chain=pki["client"][0],
        )
        async with grpc.aio.secure_channel("localhost:50231", credentials) as channel:
            assert await _health(channel)
    finally:
        await server.stop(grace=0)


@pytest.mark.asyncio
async def test_a_client_with_no_certificate_is_refused(pki: dict) -> None:
    """The point of require_client_auth: without it such a caller gets through
    and every servicer has to check for itself."""
    server = await _serve(
        50232,
        ServerTls(
            certificate=pki["server"][0],
            private_key=pki["server"][1],
            client_ca=pki["ca"],
        ),
    )
    try:
        credentials = grpc.ssl_channel_credentials(root_certificates=pki["ca"])
        async with grpc.aio.secure_channel("localhost:50232", credentials) as channel:
            assert not await _health(channel)
    finally:
        await server.stop(grace=0)


@pytest.mark.asyncio
async def test_a_certificate_from_another_ca_is_refused(pki: dict) -> None:
    """Holding *a* certificate is not the same as holding one this fleet
    issued — otherwise anyone with a CA could mint themselves in."""
    server = await _serve(
        50233,
        ServerTls(
            certificate=pki["server"][0],
            private_key=pki["server"][1],
            client_ca=pki["ca"],
        ),
    )
    try:
        credentials = grpc.ssl_channel_credentials(
            root_certificates=pki["ca"],
            private_key=pki["stranger"][1],
            certificate_chain=pki["stranger"][0],
        )
        async with grpc.aio.secure_channel("localhost:50233", credentials) as channel:
            assert not await _health(channel)
    finally:
        await server.stop(grace=0)


@pytest.mark.asyncio
async def test_a_plaintext_caller_cannot_reach_a_tls_port(pki: dict) -> None:
    server = await _serve(
        50234,
        ServerTls(
            certificate=pki["server"][0],
            private_key=pki["server"][1],
            client_ca=pki["ca"],
        ),
    )
    try:
        async with grpc.aio.insecure_channel("localhost:50234") as channel:
            assert not await _health(channel)
    finally:
        await server.stop(grace=0)


@pytest.mark.asyncio
async def test_tls_without_a_client_ca_serves_without_demanding_one(pki: dict) -> None:
    """Encryption on its own is a legitimate configuration — a service may want
    the wire protected without issuing certificates to its callers."""
    server = await _serve(
        50235,
        ServerTls(certificate=pki["server"][0], private_key=pki["server"][1]),
    )
    try:
        credentials = grpc.ssl_channel_credentials(root_certificates=pki["ca"])
        async with grpc.aio.secure_channel("localhost:50235", credentials) as channel:
            assert await _health(channel)
    finally:
        await server.stop(grace=0)


@pytest.mark.asyncio
async def test_without_tls_the_port_is_the_plaintext_one_it_always_was() -> None:
    """Every existing caller passes no tls, and must keep working unchanged."""
    server = await _serve(50236, None)
    try:
        async with grpc.aio.insecure_channel("localhost:50236") as channel:
            assert await _health(channel)
    finally:
        await server.stop(grace=0)


def test_a_context_with_no_peer_certificate_reads_as_unknown() -> None:
    """A plaintext port, or a TLS port serving without client auth. The caller
    has to treat this as "unknown", never as "trusted"."""

    class _NoAuth:
        pass

    class _Empty:
        def auth_context(self) -> dict:
            return {}

    assert peer_certificate_pem(_NoAuth()) == ""
    assert peer_certificate_pem(_Empty()) == ""


def test_a_peer_certificate_is_read_back_as_pem() -> None:
    class _WithCert:
        def auth_context(self) -> dict:
            return {"x509_pem_cert": [b"-----BEGIN CERTIFICATE-----\nx\n"]}

    assert peer_certificate_pem(_WithCert()).startswith("-----BEGIN CERTIFICATE-----")
