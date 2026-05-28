use pyo3::prelude::*;
use regex::Regex;
use std::sync::LazyLock;
use unicode_normalization::UnicodeNormalization;

// ---------------------------------------------------------------------------
// Compiled regex patterns (initialized once)
// ---------------------------------------------------------------------------

static FORMATTING_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"§.").expect("invalid formatting regex"));

static WHITESPACE_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\s+").expect("invalid whitespace regex"));

/// Compiled maintenance-pattern matcher.
///
/// Accepts regex patterns from Python at construction time,
/// compiles them once in Rust, and caches the compiled forms.
#[pyclass]
pub struct MotdMatcher {
    patterns: Vec<Regex>,
}

#[pymethods]
impl MotdMatcher {
    /// Create a new matcher from a list of regex pattern strings.
    #[new]
    fn new(patterns: Vec<String>) -> PyResult<Self> {
        let compiled: Result<Vec<Regex>, _> = patterns.iter().map(|p| Regex::new(p)).collect();
        let compiled = compiled.map_err(|e| {
            pyo3::exceptions::PyValueError::new_err(format!("invalid regex pattern: {e}"))
        })?;
        Ok(Self { patterns: compiled })
    }

    /// Check if an MOTD string matches any of the configured maintenance patterns.
    /// The MOTD is normalized before matching.
    fn is_maintenance(&self, motd: &str) -> bool {
        if motd.is_empty() {
            return false;
        }
        let normalized = normalize_motd(motd);
        if normalized.is_empty() {
            return false;
        }
        self.patterns.iter().any(|p| p.is_match(&normalized))
    }
}

// Unicode name prefixes for small-cap / modifier transliteration.
const SMALL_CAPITAL_PREFIX: &str = "LATIN LETTER SMALL CAPITAL ";
const MODIFIER_SMALL_PREFIX: &str = "MODIFIER LETTER SMALL ";

// ---------------------------------------------------------------------------
// Character normalization
// ---------------------------------------------------------------------------

/// Normalize a single character: NFKD → strip combining → small-cap fallback.
fn normalize_char(ch: char) -> String {
    // 1. NFKD decomposition, drop combining marks.
    let stripped: String = ch
        .nfkd()
        .filter(|c| !unicode_normalization::char::is_combining_mark(*c))
        .collect();

    if !stripped.is_empty() && stripped.chars().next() != Some(ch) {
        return stripped;
    }

    // 2. Fallback: check Unicode name for LATIN LETTER SMALL CAPITAL / MODIFIER LETTER SMALL.
    if let Some(name) = unicode_name(ch) {
        if let Some(letter) = name.strip_prefix(SMALL_CAPITAL_PREFIX) {
            if letter.len() == 1 {
                return letter.to_lowercase();
            }
        }
        if let Some(letter) = name.strip_prefix(MODIFIER_SMALL_PREFIX) {
            if letter.len() == 1 {
                return letter.to_lowercase();
            }
        }
    }

    ch.to_string()
}

/// Best-effort Unicode character name lookup.
///
/// Uses the `unicode_names2` algorithm: Characters are looked up by their
/// Unicode name via a compact table.  We inline a minimal version here
/// that covers the two ranges we care about (Latin small capitals U+1D00..
/// U+1D2B and modifier letters U+1D2C..U+1D6A, plus a few scattered ones).
fn unicode_name(ch: char) -> Option<&'static str> {
    // We only need names for the small-capital / modifier-small ranges.
    // Instead of pulling in a full Unicode name database crate, maintain a
    // small lookup table covering the characters Minecraft MOTDs actually use.
    SMALL_CAP_NAMES.get(&ch).copied()
}

use std::collections::HashMap;
use std::sync::LazyLock as LL2;

static SMALL_CAP_NAMES: LL2<HashMap<char, &'static str>> = LL2::new(|| {
    let mut m = HashMap::new();
    // Latin Letter Small Capital (U+1D00..U+1D2B range used in Minecraft MOTDs)
    m.insert('\u{1D00}', "LATIN LETTER SMALL CAPITAL A");
    m.insert('\u{1D01}', "LATIN LETTER SMALL CAPITAL AE");
    m.insert('\u{1D03}', "LATIN LETTER SMALL CAPITAL BARRED B");
    m.insert('\u{1D04}', "LATIN LETTER SMALL CAPITAL C");
    m.insert('\u{1D05}', "LATIN LETTER SMALL CAPITAL D");
    m.insert('\u{1D07}', "LATIN LETTER SMALL CAPITAL E");
    m.insert('\u{1D08}', "LATIN LETTER SMALL CAPITAL TURNED E"); // won't match single-letter
    m.insert('\u{1D0A}', "LATIN LETTER SMALL CAPITAL J");
    m.insert('\u{1D0B}', "LATIN LETTER SMALL CAPITAL K");
    m.insert('\u{1D0C}', "LATIN LETTER SMALL CAPITAL L WITH STROKE"); // won't match
    m.insert('\u{1D0D}', "LATIN LETTER SMALL CAPITAL M");
    m.insert('\u{1D0F}', "LATIN LETTER SMALL CAPITAL O");
    m.insert('\u{1D18}', "LATIN LETTER SMALL CAPITAL P");
    m.insert('\u{1D1B}', "LATIN LETTER SMALL CAPITAL T");
    m.insert('\u{1D1C}', "LATIN LETTER SMALL CAPITAL U");
    m.insert('\u{1D20}', "LATIN LETTER SMALL CAPITAL V");
    m.insert('\u{1D21}', "LATIN LETTER SMALL CAPITAL W");
    m.insert('\u{1D22}', "LATIN LETTER SMALL CAPITAL Z");
    // Additional commonly abused small capitals:
    m.insert('\u{0262}', "LATIN LETTER SMALL CAPITAL G");
    m.insert('\u{029C}', "LATIN LETTER SMALL CAPITAL H");
    m.insert('\u{026A}', "LATIN LETTER SMALL CAPITAL I");
    m.insert('\u{0274}', "LATIN LETTER SMALL CAPITAL N");
    m.insert('\u{0280}', "LATIN LETTER SMALL CAPITAL R");
    m.insert('\u{028F}', "LATIN LETTER SMALL CAPITAL Y");
    // Modifier Letter Small (common ones)
    m.insert('\u{1D43}', "MODIFIER LETTER SMALL A");
    m.insert('\u{1D47}', "MODIFIER LETTER SMALL B");
    m.insert('\u{1D48}', "MODIFIER LETTER SMALL D");
    m.insert('\u{1D49}', "MODIFIER LETTER SMALL E");
    m.insert('\u{1D4D}', "MODIFIER LETTER SMALL G");
    m.insert('\u{02B0}', "MODIFIER LETTER SMALL H");
    m.insert('\u{2071}', "MODIFIER LETTER SMALL I"); // superscript i
    m.insert('\u{02B2}', "MODIFIER LETTER SMALL J");
    m.insert('\u{1D4F}', "MODIFIER LETTER SMALL K");
    m.insert('\u{02E1}', "MODIFIER LETTER SMALL L");
    m.insert('\u{1D50}', "MODIFIER LETTER SMALL M");
    m.insert('\u{207F}', "MODIFIER LETTER SMALL N"); // superscript n
    m.insert('\u{1D52}', "MODIFIER LETTER SMALL O");
    m.insert('\u{1D56}', "MODIFIER LETTER SMALL P");
    m.insert('\u{02B3}', "MODIFIER LETTER SMALL R");
    m.insert('\u{02E2}', "MODIFIER LETTER SMALL S");
    m.insert('\u{1D57}', "MODIFIER LETTER SMALL T");
    m.insert('\u{1D58}', "MODIFIER LETTER SMALL U");
    m.insert('\u{1D5B}', "MODIFIER LETTER SMALL V");
    m.insert('\u{02B7}', "MODIFIER LETTER SMALL W");
    m.insert('\u{02E3}', "MODIFIER LETTER SMALL X");
    m
});

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Normalize a Minecraft MOTD string:
/// 1. Strip Minecraft formatting codes (§x)
/// 2. Per-character Unicode normalization (NFKD + small-cap transliteration)
/// 3. Case-fold + collapse whitespace
#[pyfunction]
pub fn normalize_motd(value: &str) -> String {
    if value.is_empty() {
        return String::new();
    }

    let no_formatting = FORMATTING_RE.replace_all(value, "");

    let transliterated: String = no_formatting.chars().map(normalize_char).collect();

    let casefolded = transliterated.to_lowercase();
    let collapsed = WHITESPACE_RE.replace_all(&casefolded, " ");

    collapsed.trim().to_string()
}
