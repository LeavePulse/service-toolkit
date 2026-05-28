//! Reusable gRPC client helpers for Rust consumers (e.g. server-poller).
//!
//! Mirrors the Python `service_toolkit.grpc` client contract: an insecure
//! channel to an internal service, with the shared secret sent as the
//! `x-internal-token` metadata header (validated server-side by
//! `InternalTokenInterceptor`).

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

/// Lazily connect an insecure channel to `target` (e.g. `http://10.200.0.101:50300`).
///
/// `connect_lazy` does not require the endpoint to be reachable at construction
/// time, matching the Python client's behaviour of building channels eagerly
/// but dialing on first use.
pub fn build_channel(target: &str) -> Result<Channel, tonic::transport::Error> {
    Ok(Endpoint::from_shared(target.to_owned())?.connect_lazy())
}
