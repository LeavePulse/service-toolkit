"""gRPC server lifecycle helpers."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, NamedTuple

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from grpc_reflection.v1alpha import reflection

if TYPE_CHECKING:
    from auth_service_sdk import JWTVerifier

    from .interceptors import AlternateTokenVerifier

logger = logging.getLogger(__name__)

_SERVER: grpc.aio.Server | None = None


class ServerTls(NamedTuple):
    """What a service needs to serve gRPC over TLS, and to demand it back.

    Optional everywhere it is accepted: a service that passes nothing keeps the
    plaintext port it had. That is deliberate rather than lenient — these ports
    sit on private networks and several services have no certificate to serve,
    so a toolkit that refused to start without one would be a toolkit nobody
    could upgrade to.

    ``client_ca`` is the half that turns TLS into mTLS. With it the server
    demands a certificate signed by that CA and refuses a caller without one, so
    the peer is authenticated by something it holds rather than by something it
    says. A bearer token in a header proves whoever sent it read the token; a
    client certificate proves the sender holds a private key, and that key never
    travels.

    Certificates as PEM bytes rather than paths: a caller may hold them in a
    secret store, an env var, or a file, and a helper that only accepted paths
    would force the first two to write them to disk to be usable.
    """

    #: The server's own certificate chain, PEM.
    certificate: bytes
    #: Its private key, PEM.
    private_key: bytes
    #: CA that client certificates must be signed by. None serves TLS without
    #: asking the caller for one.
    client_ca: bytes | None = None


def peer_certificate_pem(context: object) -> str:
    """The verified client certificate of the caller, or "".

    Only meaningful when the server demanded one: the value comes from gRPC's
    auth context, which is populated after the handshake has already checked the
    chain against the configured CA. So a servicer reading this is reading an
    identity that has been proven, not one that has been claimed — which is the
    distinction that makes it worth more than a header.

    Empty for a plaintext port, or for a TLS port serving without client auth.
    A caller has to treat that as "unknown", never as "trusted".
    """
    auth = getattr(context, "auth_context", None)
    if auth is None:
        return ""
    try:
        identities = auth().get("x509_pem_cert") or ()
    except Exception:
        # Reported, not swallowed: a context that cannot answer is either a
        # double in a test or a gRPC version whose auth context has moved, and
        # the second is worth knowing about — every caller of this would
        # silently read "no certificate" and treat the peer as unknown.
        logger.warning("could not read the peer certificate from the call context")
        return ""
    if not identities:
        return ""
    first = identities[0]
    return first.decode() if isinstance(first, bytes) else str(first)


def create_grpc_server(
    *,
    port: int = 50051,
    interceptors: Sequence[grpc.aio.ServerInterceptor] = (),
    reflection_enabled: bool = True,
    service_names: Sequence[str] = (),
    tls: ServerTls | None = None,
) -> grpc.aio.Server:
    """Create a gRPC async server (not yet started).

    Returns the server so callers can register servicers before starting.

    *tls* serves the port over TLS, and demands a client certificate when it
    carries a ``client_ca``. None keeps the plaintext port, which is what every
    caller had before this existed.
    """
    global _SERVER  # noqa: PLW0603

    server = grpc.aio.server(
        interceptors=list(interceptors),
        options=[
            ("grpc.max_receive_message_length", 16 * 1024 * 1024),
            ("grpc.max_send_message_length", 16 * 1024 * 1024),
        ],
    )
    if tls is None:
        server.add_insecure_port(f"[::]:{port}")
    else:
        server.add_secure_port(
            f"[::]:{port}",
            grpc.ssl_server_credentials(
                [(tls.private_key, tls.certificate)],
                root_certificates=tls.client_ca,
                # Demanded, not merely accepted. Optional client auth means a
                # caller without a certificate still gets through, so every
                # servicer would have to check for one and the guarantee would
                # be whatever the least careful of them does.
                require_client_auth=tls.client_ca is not None,
            ),
        )

    # Health check
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    health_servicer.set(
        "", health_pb2.HealthCheckResponse.SERVING,
    )
    for name in service_names:
        health_servicer.set(
            name, health_pb2.HealthCheckResponse.SERVING,
        )

    # Reflection
    if reflection_enabled:
        service_names_for_reflection = [
            health_pb2.DESCRIPTOR.services_by_name["Health"].full_name,
            *service_names,
            reflection.SERVICE_NAME,
        ]
        reflection.enable_server_reflection(service_names_for_reflection, server)

    _SERVER = server
    logger.info("Created gRPC server on port %d", port)
    return server


async def start_grpc_server(server: grpc.aio.Server | None = None) -> None:
    """Start the gRPC server."""
    srv = server or _SERVER
    if srv is None:
        msg = "No gRPC server created. Call create_grpc_server() first."
        raise RuntimeError(msg)
    await srv.start()
    logger.info("gRPC server started")


async def stop_grpc_server(
    server: grpc.aio.Server | None = None,
    *,
    grace: float = 5.0,
) -> None:
    """Gracefully stop the gRPC server."""
    global _SERVER  # noqa: PLW0603
    srv = server or _SERVER
    if srv is None:
        return
    await srv.stop(grace=grace)
    _SERVER = None
    logger.info("gRPC server stopped")


ServiceRegistrar = Callable[[grpc.aio.Server], None]


def build_grpc_lifecycle(
    *,
    service_name: str,
    port: int,
    internal_token: str | None = None,
    internal_token_exempt_methods: Sequence[str] = (),
    require_internal_token: bool = True,
    alternate_token_verifier: AlternateTokenVerifier | None = None,
    jwt_verifier: JWTVerifier[Any] | None = None,
    reflection_enabled: bool = True,
    service_names: Sequence[str] = (),
    registrars: Sequence[ServiceRegistrar] = (),
    tls: ServerTls | None = None,
) -> tuple[Callable[[], object], Callable[[], object]]:
    """Build startup/shutdown callables for Litestar ``on_startup``/``on_shutdown``.

    This is the gRPC counterpart of ``create_service_app()`` — it eliminates
    the duplicated setup.py boilerplate across services.

    *registrars* are callables that receive the ``grpc.aio.Server`` and should
    call ``add_<X>Servicer_to_server(servicer, server)``.

    *jwt_verifier* (optional) — when supplied, installs
    :class:`~service_toolkit.grpc.jwt_forwarding.JwtContextServerInterceptor`,
    which decodes the inbound ``authorization`` JWT and exposes it via the
    ``current_jwt_payload`` contextvar for servicers to consume with their
    existing permission helpers.

    *require_internal_token* (default ``True``) — fail closed. Many servicers
    treat "no inbound JWT" as a trusted internal/admin caller, which is only
    sound when the :class:`InternalTokenInterceptor` is actually installed to
    gate the port. The interceptor is installed only when *internal_token* is
    set, so a missing token would silently open the entire internal/admin
    surface to anyone who can reach the port. When this flag is ``True`` and no
    token is configured, startup raises instead of booting wide open. Pass
    ``require_internal_token=False`` (e.g. ``not settings.debug``) only for
    services that deliberately run token-less, such as local dev or services
    whose every RPC is JWT-authenticated.

    *alternate_token_verifier* (optional) — a second credential this service
    accepts on ``x-internal-token``, consulted only when the shared secret does
    not match. It exists so a service can admit callers it authenticates its own
    way (the control-plane's per-host agent tokens) without every service
    learning about them, and without loosening anything by default: unset means
    shared-token-only, exactly as before.

    Returns ``(startup, shutdown)`` async callables.
    """

    if require_internal_token and not internal_token:
        msg = (
            f"{service_name}: internal_token is required but not configured. "
            "Set INTERNAL_TOKEN (service-to-service auth) or pass "
            "require_internal_token=False to explicitly run the gRPC server "
            "without internal-token gating (dev / fully JWT-authenticated only)."
        )
        raise RuntimeError(msg)

    async def _startup() -> None:
        from .metrics import GrpcServerMetricsInterceptor
        from .servicer import DomainErrorServerInterceptor

        interceptors: list[grpc.aio.ServerInterceptor] = [
            GrpcServerMetricsInterceptor(service_name)
        ]
        if internal_token:
            from .interceptors import InternalTokenInterceptor

            interceptors.append(
                InternalTokenInterceptor(
                    internal_token,
                    exempt_methods=internal_token_exempt_methods,
                    alternate_verifier=alternate_token_verifier,
                )
            )

        if jwt_verifier is not None:
            from .jwt_forwarding import JwtContextServerInterceptor

            interceptors.append(JwtContextServerInterceptor(jwt_verifier))

        # Innermost interceptor: convert awesome-errors domain exceptions raised
        # by a servicer into precise gRPC status codes so the metrics interceptor
        # records the real code and clients see UNAUTHENTICATED/NOT_FOUND/…
        # instead of UNKNOWN. Must wrap the servicer directly, hence appended
        # last (grpc.aio applies the last interceptor closest to the handler).
        interceptors.append(DomainErrorServerInterceptor())

        server = create_grpc_server(
            port=port,
            interceptors=interceptors,
            reflection_enabled=reflection_enabled,
            service_names=service_names,
            tls=tls,
        )

        for registrar in registrars:
            registrar(server)

        await start_grpc_server(server)
        logger.info("%s gRPC server started on port %d", service_name, port)

    async def _shutdown() -> None:
        await stop_grpc_server()
        logger.info("%s gRPC server stopped", service_name)

    return _startup, _shutdown


__all__ = [
    "build_grpc_lifecycle",
    "create_grpc_server",
    "start_grpc_server",
    "stop_grpc_server",
]
