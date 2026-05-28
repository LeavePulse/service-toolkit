use pyo3::prelude::*;
use regex::Regex;
use std::sync::LazyLock;

static MOTD_FORMATTING_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"§.").expect("invalid formatting regex"));

static HTML_TAG_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"<[^>]*>").expect("invalid html tag regex"));

static WS_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\s+").expect("invalid whitespace regex"));

/// Normalize text for sitemap/SEO: strip Minecraft formatting codes,
/// HTML tags, collapse whitespace.
#[pyfunction]
pub fn normalize_sitemap_text(value: &str) -> String {
    if value.is_empty() {
        return String::new();
    }
    let text = value.trim();
    if text.is_empty() {
        return String::new();
    }
    let no_formatting = MOTD_FORMATTING_RE.replace_all(text, "");
    let no_html = HTML_TAG_RE.replace_all(&no_formatting, " ");
    let no_newlines = no_html.replace("\\n", " ").replace('\n', " ");
    let collapsed = WS_RE.replace_all(&no_newlines, " ");
    collapsed.trim().to_string()
}

/// Batch-normalize multiple texts at once.
#[pyfunction]
pub fn normalize_sitemap_texts_batch(values: Vec<String>) -> Vec<String> {
    values.iter().map(|v| normalize_sitemap_text(v)).collect()
}
