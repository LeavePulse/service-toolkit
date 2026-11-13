#![cfg(feature = "http")]

use std::io::Write;
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Duration;

use reqwest::Method;
use service_toolkit_rust::http::{Encoding, HttpPolicy, MemoryEtagStore, Retries};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;

type Reply = fn(usize, &str) -> Vec<u8>;

async fn serve(reply: Reply) -> (String, Arc<AtomicUsize>, Arc<std::sync::Mutex<Vec<String>>>) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}/thing", listener.local_addr().unwrap());
    let hits = Arc::new(AtomicUsize::new(0));
    let seen = Arc::new(std::sync::Mutex::new(Vec::new()));
    let (h, s) = (hits.clone(), seen.clone());
    tokio::spawn(async move {
        loop {
            let (mut socket, _) = listener.accept().await.unwrap();
            let (h, s) = (h.clone(), s.clone());
            tokio::spawn(async move {
                let mut buf = vec![0u8; 8192];
                let n = socket.read(&mut buf).await.unwrap_or(0);
                let head = String::from_utf8_lossy(&buf[..n]).to_lowercase();
                let index = h.fetch_add(1, Ordering::SeqCst);
                s.lock().unwrap().push(head.clone());
                let _ = socket.write_all(&reply(index, &head)).await;
            });
        }
    });
    (url, hits, seen)
}

fn response(status: &str, headers: &[(&str, &str)], body: &[u8]) -> Vec<u8> {
    let mut out = format!(
        "HTTP/1.1 {status}\r\ncontent-length: {}\r\nconnection: close\r\n",
        body.len()
    );
    for (k, v) in headers {
        out.push_str(&format!("{k}: {v}\r\n"));
    }
    out.push_str("\r\n");
    let mut bytes = out.into_bytes();
    bytes.extend_from_slice(body);
    bytes
}

fn quick() -> HttpPolicy {
    let mut policy = HttpPolicy::new("toolkit-test");
    policy.retries = Retries {
        attempts: 3,
        base: Duration::from_millis(1),
        max_wait: Duration::from_secs(2),
    };
    policy
}

#[tokio::test]
async fn throttled_get_waits_and_retries() {
    let (url, hits, _) = serve(|i, _| {
        if i == 0 {
            response("503 Service Unavailable", &[("retry-after", "0")], b"")
        } else {
            response("200 OK", &[], b"ok")
        }
    })
    .await;
    let client = quick().build().unwrap();
    let body = client
        .send(client.get(&url))
        .await
        .unwrap()
        .bytes()
        .await
        .unwrap();
    assert_eq!(&body[..], b"ok");
    assert_eq!(hits.load(Ordering::SeqCst), 2);
}

#[tokio::test]
async fn post_is_not_replayed_and_error_names_the_url() {
    let (url, hits, _) =
        serve(|_, _| response("503 Service Unavailable", &[("retry-after", "0")], b"down")).await;
    let client = quick().build().unwrap();
    let error = client
        .send(client.request(Method::POST, &url).body("x"))
        .await
        .unwrap_err();
    assert_eq!(hits.load(Ordering::SeqCst), 1);
    assert!(error.to_string().contains(&url));
    assert_eq!(error.status().map(|s| s.as_u16()), Some(503));
}

#[tokio::test]
async fn retry_after_beyond_max_wait_gives_up() {
    let (url, hits, _) =
        serve(|_, _| response("429 Too Many Requests", &[("retry-after", "3600")], b"")).await;
    let client = quick().build().unwrap();
    assert!(client.send(client.get(&url)).await.is_err());
    assert_eq!(hits.load(Ordering::SeqCst), 1);
}

#[tokio::test]
async fn conditional_get_reuses_the_stored_body() {
    let (url, hits, _) = serve(|_, head| {
        if head.contains("if-none-match: w/\"v1\"") {
            response("304 Not Modified", &[("etag", "W/\"v1\"")], b"")
        } else {
            response("200 OK", &[("etag", "W/\"v1\"")], b"payload")
        }
    })
    .await;
    let client = quick().build().unwrap();
    let store = MemoryEtagStore::default();
    assert_eq!(
        &client.conditional_get(&url, &store).await.unwrap().unwrap()[..],
        b"payload"
    );
    assert_eq!(
        &client.conditional_get(&url, &store).await.unwrap().unwrap()[..],
        b"payload"
    );
    assert_eq!(hits.load(Ordering::SeqCst), 2);
}

#[tokio::test]
async fn not_modified_without_a_stored_body_is_recovered() {
    let (url, _, seen) = serve(|_, head| {
        if head.contains("cache-control: no-cache") {
            response("200 OK", &[("etag", "\"v2\"")], b"fresh")
        } else {
            response("304 Not Modified", &[], b"")
        }
    })
    .await;
    let client = quick().build().unwrap();
    let store = MemoryEtagStore::default();
    let body = client.conditional_get(&url, &store).await.unwrap().unwrap();
    assert_eq!(&body[..], b"fresh");
    assert_eq!(seen.lock().unwrap().len(), 2);
}

#[tokio::test]
async fn missing_resource_is_none() {
    let (url, _, _) = serve(|_, _| response("404 Not Found", &[], b"")).await;
    let client = quick().build().unwrap();
    assert!(
        client
            .conditional_get(&url, &MemoryEtagStore::default())
            .await
            .unwrap()
            .is_none()
    );
}

#[tokio::test]
async fn encodings_are_negotiated_or_refused() {
    let (url, _, seen) = serve(|_, head| {
        if head.contains("accept-encoding: identity") {
            return response("200 OK", &[], b"plain");
        }
        response("200 OK", &[("content-encoding", "gzip")], &gzip(b"decoded"))
    })
    .await;
    let negotiated = quick().build().unwrap();
    let body = negotiated
        .send(negotiated.get(&url))
        .await
        .unwrap()
        .bytes()
        .await
        .unwrap();
    assert_eq!(&body[..], b"decoded");
    assert!(seen.lock().unwrap()[0].contains("zstd"));
    let mut identity = quick();
    identity.encoding = Encoding::Identity;
    let client = identity.build().unwrap();
    let body = client
        .send(client.get(&url))
        .await
        .unwrap()
        .bytes()
        .await
        .unwrap();
    assert_eq!(&body[..], b"plain");
}

fn gzip(data: &[u8]) -> Vec<u8> {
    let mut child = std::process::Command::new("gzip")
        .arg("-c")
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .spawn()
        .unwrap();
    child.stdin.take().unwrap().write_all(data).unwrap();
    child.wait_with_output().unwrap().stdout
}

#[tokio::test]
#[ignore = "needs a running Transfer server: TRANSFER_URL=http://host/path-with-etag"]
async fn against_a_real_transfer_server() {
    let url = std::env::var("TRANSFER_URL").expect("TRANSFER_URL");
    let negotiated = quick().build().unwrap();
    let mut plain = quick();
    plain.encoding = Encoding::Identity;
    let plain = plain.build().unwrap();
    let a = negotiated
        .send(negotiated.get(&url))
        .await
        .unwrap()
        .bytes()
        .await
        .unwrap();
    let b = plain
        .send(plain.get(&url))
        .await
        .unwrap()
        .bytes()
        .await
        .unwrap();
    assert_eq!(a, b);
    let store = MemoryEtagStore::default();
    let first = negotiated
        .conditional_get(&url, &store)
        .await
        .unwrap()
        .unwrap();
    let second = negotiated
        .conditional_get(&url, &store)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(first, second);
}
