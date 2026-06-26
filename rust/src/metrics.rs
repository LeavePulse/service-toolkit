//! Prometheus `/metrics` HTTP endpoint shared by Rust services.
//!
//! Both the node agent and the poller previously hand-rolled the same hyper
//! listener + `TextEncoder` render loop, differing only in which metrics they
//! registered. The serving machinery lives here; each service keeps only its
//! own counter/gauge definitions and chooses a registry source:
//!
//!   * [`serve`] gathers the process-global default registry (services that use
//!     `prometheus::register_*!` macros).
//!   * [`serve_registry`] gathers a caller-owned [`Registry`] (services that
//!     keep their metrics in a private registry).

use std::convert::Infallible;
use std::net::SocketAddr;

use http_body_util::Full;
use hyper::body::{Bytes, Incoming};
use hyper::service::service_fn;
use hyper::{Request, Response};
use hyper_util::rt::TokioIo;
use prometheus::{Encoder, Registry, TextEncoder};
use tokio::net::TcpListener;
use tracing::warn;

/// Encode gathered metric families into the Prometheus text exposition format.
/// Empty string on an encode error (logged), so the endpoint always responds.
fn render(metric_families: Vec<prometheus::proto::MetricFamily>) -> String {
    let encoder = TextEncoder::new();
    let mut buf = Vec::new();
    if let Err(e) = encoder.encode(&metric_families, &mut buf) {
        warn!(error = %e, "metrics: encode failed");
        return String::new();
    }
    String::from_utf8_lossy(&buf).into_owned()
}

async fn respond(body: String) -> Result<Response<Full<Bytes>>, Infallible> {
    Ok(Response::new(Full::new(Bytes::from(body))))
}

/// Serve `/metrics` (any path) on `addr` for the process lifetime, gathering
/// the process-global default Prometheus registry. Returns only if the bind
/// fails (logged).
pub async fn serve(addr: SocketAddr) {
    serve_with(addr, prometheus::gather).await
}

/// Serve `/metrics` on `addr` gathering a caller-owned `registry`.
pub async fn serve_registry(addr: SocketAddr, registry: Registry) {
    serve_with(addr, move || registry.gather()).await
}

/// Core accept loop, parameterised over how metric families are gathered.
async fn serve_with<G>(addr: SocketAddr, gather: G)
where
    G: Fn() -> Vec<prometheus::proto::MetricFamily> + Clone + Send + Sync + 'static,
{
    let listener = match TcpListener::bind(addr).await {
        Ok(l) => l,
        Err(e) => {
            warn!(error = %e, %addr, "metrics: failed to bind endpoint");
            return;
        }
    };
    loop {
        let Ok((stream, _)) = listener.accept().await else {
            continue;
        };
        let io = TokioIo::new(stream);
        let gather = gather.clone();
        let handler = service_fn(move |_req: Request<Incoming>| respond(render(gather())));
        tokio::spawn(async move {
            let _ = hyper::server::conn::http1::Builder::new()
                .serve_connection(io, handler)
                .await;
        });
    }
}
