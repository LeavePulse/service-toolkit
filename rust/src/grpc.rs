//! Reusable gRPC client helpers for Rust consumers (e.g. server-poller).
//!
//! Mirrors the Python `service_toolkit.grpc` client contract: an insecure
//! channel to an internal service, with the shared secret sent as the
//! `x-internal-token` metadata header (validated server-side by
//! `InternalTokenInterceptor`).

use std::time::Duration;

use tonic::metadata::MetadataValue;
use tonic::service::Interceptor;
use tonic::transport::{Channel, Endpoint};
use tonic::{Request, Status};

/// Interceptor that attaches the internal shared secret to every request as
/// the `x-internal-token` header, matching the server-side guard.
#[derive(Clone)]
pub struct InternalTokenInterceptor {
    token: MetadataValue<tonic::metadata::Ascii>,
}

impl InternalTokenInterceptor {
    /// Build an interceptor. Returns `None` when the token is empty, so callers
    /// can decide whether an unauthenticated channel is acceptable.
    pub fn new(token: &str) -> Option<Self> {
        let trimmed = token.trim();
        if trimmed.is_empty() {
            return None;
        }
        let value = trimmed.parse::<MetadataValue<_>>().ok()?;
        Some(Self { token: value })
    }
}

impl Interceptor for InternalTokenInterceptor {
    fn call(&mut self, mut request: Request<()>) -> Result<Request<()>, Status> {
        request
            .metadata_mut()
            .insert("x-internal-token", self.token.clone());
        Ok(request)
    }
}

/// How often an idle connection is pinged, and how long an unanswered ping is
/// waited for before the connection is declared dead.
///
/// A long-lived stream is the case these exist for. Without them a peer that
/// dies without closing its socket — a container recreated under a NAT that
/// then forgets the conntrack entry — leaves the client holding a socket
/// nobody will ever answer. TCP alone will not notice: there is nothing to
/// send, so there is nothing to fail.
///
/// That is not hypothetical. The control-agent on app-vps-1 promoted the very
/// control-service it reports to, and the recreated container came back on a
/// new connection. The agent held the dead one for 27 minutes without a single
/// log line, its heartbeat task having exited on the closed channel, until it
/// was restarted by hand.
const KEEPALIVE_INTERVAL: Duration = Duration::from_secs(20);
const KEEPALIVE_TIMEOUT: Duration = Duration::from_secs(10);

/// Cap on the TCP+HTTP2 handshake, so a black-holed address fails and lets the
/// caller's own retry loop run rather than hanging inside `connect`.
const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);

/// Lazily connect an insecure channel to `target` (e.g. `http://10.200.0.101:50300`).
///
/// `connect_lazy` does not require the endpoint to be reachable at construction
/// time, matching the Python client's behaviour of building channels eagerly
/// but dialing on first use.
///
/// Keepalives are on for every channel, not only the streaming ones: a caller
/// cannot generally tell whether the RPC it is about to make will be long-lived,
/// and the cost of a ping every 20s on an idle connection is nothing next to
/// the failure it prevents.
pub fn build_channel(target: &str) -> Result<Channel, tonic::transport::Error> {
    Ok(Endpoint::from_shared(target.to_owned())?
        .connect_timeout(CONNECT_TIMEOUT)
        // On an idle connection too: a command stream that is merely waiting
        // for the next command has no traffic of its own, and that is exactly
        // when a silently dead peer must still be noticed.
        .keep_alive_while_idle(true)
        .http2_keep_alive_interval(KEEPALIVE_INTERVAL)
        .keep_alive_timeout(KEEPALIVE_TIMEOUT)
        .connect_lazy())
}
