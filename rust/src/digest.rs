//! Content digests as lowercase hex: SHA-1, SHA-256 and SHA-512.
//!
//! The three algorithms the consumers check against: SHA-1 for Mojang and
//! CurseForge files, SHA-512 for Modrinth, SHA-256 for releases, update bundles
//! and pinned tools. Comparison ignores case and surrounding whitespace, since
//! manifests carry either case and sometimes a trailing newline.

use std::fmt;
use std::fs::File;
use std::io::{self, Write};
use std::path::Path;

use sha1::Sha1;
use sha2::{Digest as _, Sha256, Sha512};

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Algorithm {
    Sha1,
    Sha256,
    Sha512,
}

impl Algorithm {
    pub fn name(self) -> &'static str {
        match self {
            Algorithm::Sha1 => "sha1",
            Algorithm::Sha256 => "sha256",
            Algorithm::Sha512 => "sha512",
        }
    }
}

impl fmt::Display for Algorithm {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.name())
    }
}

/// An expected digest: an algorithm and its hex value, stored trimmed and
/// lowercase.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct Digest {
    algorithm: Algorithm,
    hex: String,
}

impl Digest {
    pub fn new(algorithm: Algorithm, hex: impl AsRef<str>) -> Self {
        Self {
            algorithm,
            hex: hex.as_ref().trim().to_ascii_lowercase(),
        }
    }

    pub fn sha1(hex: impl AsRef<str>) -> Self {
        Self::new(Algorithm::Sha1, hex)
    }

    pub fn sha256(hex: impl AsRef<str>) -> Self {
        Self::new(Algorithm::Sha256, hex)
    }

    pub fn sha512(hex: impl AsRef<str>) -> Self {
        Self::new(Algorithm::Sha512, hex)
    }

    pub fn algorithm(&self) -> Algorithm {
        self.algorithm
    }

    pub fn hex(&self) -> &str {
        &self.hex
    }

    /// Whether *actual* (hex of this digest's algorithm) is this digest. An
    /// empty expectation matches nothing.
    pub fn matches(&self, actual: &str) -> bool {
        !self.hex.is_empty() && actual.trim().eq_ignore_ascii_case(&self.hex)
    }
}

impl fmt::Display for Digest {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}:{}", self.algorithm, self.hex)
    }
}

/// A running digest of one algorithm, fed chunk by chunk. Also an
/// [`io::Write`], so `io::copy` can hash a reader.
#[derive(Clone)]
pub struct Hasher(Inner);

#[derive(Clone)]
enum Inner {
    Sha1(Sha1),
    Sha256(Sha256),
    Sha512(Sha512),
}

impl Hasher {
    pub fn new(algorithm: Algorithm) -> Self {
        Self(match algorithm {
            Algorithm::Sha1 => Inner::Sha1(Sha1::new()),
            Algorithm::Sha256 => Inner::Sha256(Sha256::new()),
            Algorithm::Sha512 => Inner::Sha512(Sha512::new()),
        })
    }

    pub fn algorithm(&self) -> Algorithm {
        match self.0 {
            Inner::Sha1(_) => Algorithm::Sha1,
            Inner::Sha256(_) => Algorithm::Sha256,
            Inner::Sha512(_) => Algorithm::Sha512,
        }
    }

    pub fn update(&mut self, bytes: &[u8]) {
        match &mut self.0 {
            Inner::Sha1(h) => h.update(bytes),
            Inner::Sha256(h) => h.update(bytes),
            Inner::Sha512(h) => h.update(bytes),
        }
    }

    pub fn finalize_hex(self) -> String {
        match self.0 {
            Inner::Sha1(h) => to_hex(&h.finalize()),
            Inner::Sha256(h) => to_hex(&h.finalize()),
            Inner::Sha512(h) => to_hex(&h.finalize()),
        }
    }
}

impl Write for Hasher {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        self.update(buf);
        Ok(buf.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

/// Lowercase hex of *bytes*.
pub fn to_hex(bytes: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for &byte in bytes {
        out.push(char::from(DIGITS[usize::from(byte >> 4)]));
        out.push(char::from(DIGITS[usize::from(byte & 0x0f)]));
    }
    out
}

/// Hex digest of *bytes* under *algorithm*.
pub fn hex_of(algorithm: Algorithm, bytes: &[u8]) -> String {
    let mut hasher = Hasher::new(algorithm);
    hasher.update(bytes);
    hasher.finalize_hex()
}

pub fn sha1_hex(bytes: &[u8]) -> String {
    hex_of(Algorithm::Sha1, bytes)
}

pub fn sha256_hex(bytes: &[u8]) -> String {
    hex_of(Algorithm::Sha256, bytes)
}

pub fn sha512_hex(bytes: &[u8]) -> String {
    hex_of(Algorithm::Sha512, bytes)
}

/// Hex digest of the file at *path*, read in chunks rather than whole: the
/// files hashed here include JDKs and modpacks of hundreds of megabytes.
/// Blocking; run it off an async executor.
pub fn file_hex(path: &Path, algorithm: Algorithm) -> io::Result<String> {
    let mut hasher = Hasher::new(algorithm);
    io::copy(&mut File::open(path)?, &mut hasher)?;
    Ok(hasher.finalize_hex())
}

/// Whether the file at *path* has digest *expected*. Blocking.
pub fn file_matches(path: &Path, expected: &Digest) -> io::Result<bool> {
    Ok(expected.matches(&file_hex(path, expected.algorithm())?))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn known_vectors() {
        assert_eq!(sha1_hex(b"abc"), "a9993e364706816aba3e25717850c26c9cd0d89d");
        assert_eq!(
            sha256_hex(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
        assert_eq!(
            sha512_hex(b"abc"),
            "ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a\
             2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f"
        );
    }

    #[test]
    fn matching_ignores_case_and_surrounding_whitespace() {
        let expected = Digest::sha1(" A9993E364706816ABA3E25717850C26C9CD0D89D\n");
        assert_eq!(expected.hex(), "a9993e364706816aba3e25717850c26c9cd0d89d");
        assert!(expected.matches(&sha1_hex(b"abc")));
        assert!(!expected.matches(&sha1_hex(b"abd")));
        assert!(!Digest::sha1("").matches(""));
    }

    #[test]
    fn streaming_equals_one_shot() {
        let mut hasher = Hasher::new(Algorithm::Sha256);
        hasher.update(b"a");
        hasher.update(b"bc");
        assert_eq!(hasher.algorithm(), Algorithm::Sha256);
        assert_eq!(hasher.finalize_hex(), sha256_hex(b"abc"));
    }

    #[test]
    fn a_file_is_hashed_in_chunks() {
        let path =
            std::env::temp_dir().join(format!("service-toolkit-digest-{}", std::process::id()));
        let body = vec![7u8; 200_000];
        std::fs::write(&path, &body).unwrap();
        assert_eq!(
            file_hex(&path, Algorithm::Sha512).unwrap(),
            sha512_hex(&body)
        );
        assert!(file_matches(&path, &Digest::sha512(sha512_hex(&body))).unwrap());
        std::fs::remove_file(&path).unwrap();
    }
}
