#!/usr/bin/env python3
"""
LeavePulse Architecture Linter (part of service-toolkit).
Enforces strict architectural boundaries, safety rules, and observability contracts.
"""

import re
import sys
from pathlib import Path
from typing import TypedDict, Pattern, Optional

# --- Types & Schemas ---


class Rule(TypedDict, total=False):
    pattern: str
    compiled: Pattern
    code: str
    name: str
    reason: str
    trigger: Optional[str]
    compiled_trigger: Optional[Pattern]
    negative_trigger: Optional[str]
    compiled_negative_trigger: Optional[Pattern]
    exclude_files: Optional[str]
    compiled_exclude: Optional[Pattern]


# --- Configuration ---

INTERNAL_SERVICE_NAMES = [
    "auth_service",
    "billing_service",
    "bot_service",
    "community_service",
    "agent_gateway",
    "monitoring_service",
    "realtime_gateway",
    "server_service",
    "whitelist_service",
]

IGNORE_PATHS = {
    ".venv",
    "venv",
    "__pycache__",
    ".git",
    "tests",
    "migrations",
    "alembic",
    "node_modules",
    "dist",
    "generated",
}

# 1. Global Forbidden Patterns (Optimized with pre-compilation)
FORBIDDEN_GLOBAL: list[Rule] = [
    {
        "code": "aiohttp",
        "pattern": r"aiohttp\.ClientSession\(",
        "name": "Forbidden aiohttp",
        "reason": "Use 'httpx.AsyncClient' or shared clients from service-toolkit.",
    },
    {
        "code": "aiohttp-import",
        "pattern": r"^\s*(import\s+aiohttp|from\s+aiohttp)",
        "name": "Forbidden aiohttp import",
        "reason": "Use 'httpx' instead of 'aiohttp'.",
    },
    {
        "code": "requests",
        "pattern": r"\brequests\.(get|post|put|delete|patch)\(",
        "name": "Blocking requests call",
        "reason": "Forbidden in async services. Use 'httpx.AsyncClient'.",
    },
    {
        "code": "time-sleep",
        "pattern": r"\btime\.sleep\(",
        "name": "Blocking time.sleep",
        "reason": "Forbidden in async services. Use 'asyncio.sleep()'.",
    },
    {
        "code": "print",
        "pattern": r"\bprint\(",
        "name": "Raw print() found",
        "reason": "Use structured logging (structlog) with proper context.",
    },
    {
        "code": "bare-except",
        "pattern": r"except\s*:",
        "name": "Bare except",
        "reason": "Forbidden. Catch 'Exception' or specific errors.",
    },
    {
        "code": "untracked-todo",
        "pattern": r"\bTODO\b|\bFIXME\b",
        "negative_trigger": r"LP-\d+|https?://|#\d+",
        "name": "Untracked TODO",
        "reason": "TODO/FIXME must have a reference to an issue (LP-123) or URL.",
    },
]

# 2. Conditional Forbidden Patterns
CONDITIONALLY_FORBIDDEN: list[Rule] = [
    {
        "code": "cache-lock",
        "pattern": r"asyncio\.Lock\(\)",
        "trigger": r"\b(cache|ttl|memo|dedup)\b",
        "name": "Manual Cache Lock",
        "reason": "Manual locks around cache logic are error-prone. Use 'service_toolkit.LookupCache'.",
    },
    {
        "code": "ad-hoc-client",
        "pattern": r"httpx\.AsyncClient\(",
        "exclude_files": r"lifespan|startup|singleton|client|conftest|core/config|deps|container|di|wiring|app",
        "name": "Ad-hoc Client Creation",
        "reason": "Clients must be managed in lifespan/singletons to reuse connections.",
    },
    {
        "code": "dangerous-timeout",
        "pattern": r"timeout\s*=\s*None|httpx\.Timeout\(\s*None\s*\)",
        "name": "Dangerous Timeout",
        "reason": "Infinite timeout detected. Always specify a finite timeout (e.g. 5.0).",
    },
    {
        "code": "env-access",
        "pattern": r"os\.(environ|getenv)\(",
        "exclude_files": r"core/config|settings|env|constants",
        "name": "Direct Env Access",
        "reason": "Avoid accessing env vars directly in business logic. Use the 'settings' object.",
    },
    {
        "code": "request-scope-verifier",
        "pattern": r"JWTVerifier\(|JWKSCache\(",
        "trigger": r"class\s+\w+(Handler|Socket|Connection)",
        "exclude_files": r"singleton|lifespan|app|deps|container",
        "name": "Request-scope Verifier",
        "reason": "JWTVerifier/JWKSCache created in connection/request scope. This causes JWKS spam. Move to singleton/lifespan or use a provider-owned shared verifier helper.",
    },
]

# Codes for the built-in audits that aren't table-driven rules.
CODE_SILENT_EXCEPTION = "silent-except"
CODE_TIGHT_LOOP = "tight-loop"
CODE_BOUNDARY = "boundary"

# --- Pre-compile Regexes ---
for r in FORBIDDEN_GLOBAL + CONDITIONALLY_FORBIDDEN:
    r["compiled"] = re.compile(r["pattern"])
    if "trigger" in r:
        r["compiled_trigger"] = re.compile(r["trigger"], re.IGNORECASE)
    if "negative_trigger" in r:
        r["compiled_negative_trigger"] = re.compile(
            r["negative_trigger"], re.IGNORECASE
        )
    if "exclude_files" in r:
        r["compiled_exclude"] = re.compile(r["exclude_files"], re.IGNORECASE)

# --- Logic ---


def strip_comments(line: str) -> str:
    return line.split("#")[0].rstrip()


# Matches ``# noqa: archlint`` optionally followed by ``=code1,code2`` (or
# ``(code1,code2)``). A bare ``archlint`` suppresses every rule; the listed
# form suppresses only the named rule codes.
_NOQA_ARCHLINT_RE = re.compile(
    r"#\s*noqa:\s*archlint(?:\s*[=(]\s*(?P<codes>[\w\-][\w\-,\s]*?)\s*\)?\s*$)?",
    re.IGNORECASE | re.MULTILINE,
)
# Sentinel returned for a bare ``# noqa: archlint`` (suppress all codes).
_SUPPRESS_ALL = frozenset({"*"})


def _archlint_suppression(text: str) -> Optional[frozenset[str]]:
    """Return the set of suppressed codes for ``text``, or ``None`` if absent.

    ``{"*"}`` means "suppress everything" (bare ``# noqa: archlint``); a
    concrete set means "suppress only these codes". ``None`` means the text
    carries no archlint suppression at all.
    """
    match = _NOQA_ARCHLINT_RE.search(text)
    if match is None:
        return None
    codes = match.group("codes")
    if not codes:
        return _SUPPRESS_ALL
    return frozenset(c.strip() for c in codes.split(",") if c.strip())


def _is_suppressed(code: str, line: str, file_suppression: frozenset[str]) -> bool:
    """Whether ``code`` is suppressed for one ``line`` of a file."""
    if file_suppression is _SUPPRESS_ALL or code in file_suppression:
        return True
    line_suppression = _archlint_suppression(line)
    if line_suppression is None:
        return False
    return line_suppression is _SUPPRESS_ALL or code in line_suppression


def check_file(file_path: Path, current_service: Optional[str]) -> list[str]:
    errors = []
    try:
        content = file_path.read_text(encoding="utf-8")
        lines = content.splitlines()

        # File-level suppression: a bare ``# noqa: archlint`` anywhere disables
        # everything (back-compat); ``# noqa: archlint=code,...`` disables only
        # the named codes file-wide. Line-level suppression is handled per code.
        file_suppression: frozenset[str] = frozenset()
        for raw_line in lines:
            found = _archlint_suppression(raw_line)
            if found is None:
                continue
            if found is _SUPPRESS_ALL:
                file_suppression = _SUPPRESS_ALL
                break
            file_suppression = file_suppression | found
        if file_suppression is _SUPPRESS_ALL:
            return []

        # 1. Global & Conditional Rules
        for rule in FORBIDDEN_GLOBAL + CONDITIONALLY_FORBIDDEN:
            code = rule["code"]
            if code in file_suppression:
                continue
            # Performance: check whole file first
            if not rule["compiled"].search(content):
                continue

            exclude_pattern = rule.get("compiled_exclude")
            if exclude_pattern and exclude_pattern.search(str(file_path)):
                continue

            trigger_pattern = rule.get("compiled_trigger")
            if trigger_pattern and not trigger_pattern.search(content):
                continue

            for i, line in enumerate(lines):
                clean_line = strip_comments(line)
                if _is_suppressed(code, line, file_suppression):
                    continue

                if rule["compiled"].search(clean_line):
                    negative_trigger_pattern = rule.get("compiled_negative_trigger")
                    if negative_trigger_pattern and negative_trigger_pattern.search(
                        clean_line
                    ):
                        continue
                    errors.append(
                        f"{file_path}:{i + 1}: {rule['name']} - {rule['reason']}\n  -> {line.strip()}"
                    )

        # 2. Cross-service boundaries
        if current_service and CODE_BOUNDARY not in file_suppression:
            for other_service in INTERNAL_SERVICE_NAMES:
                import_name = other_service.replace("-", "_")
                if import_name == current_service:
                    continue

                # Precise boundary pattern: from|import (\w+.)*import_name\b
                pattern = rf"^\s*(from|import)\s+(\w+\.)*{import_name}\b"
                for i, line in enumerate(lines):
                    if _is_suppressed(CODE_BOUNDARY, line, file_suppression):
                        continue
                    if re.search(pattern, strip_comments(line)):
                        errors.append(
                            f"{file_path}:{i + 1}: Boundary Violation - Direct import from '{other_service}' forbidden.\n  -> {line.strip()}"
                        )

        # 3. Silent Exception Audit
        if CODE_SILENT_EXCEPTION not in file_suppression:
            for i, line in enumerate(lines):
                if "except Exception:" in line or "except Exception as" in line:
                    if _is_suppressed(CODE_SILENT_EXCEPTION, line, file_suppression):
                        continue
                    # Scan next 5 lines for logging or raise
                    block = "\n".join(lines[i + 1 : i + 6]).lower()
                    if not any(
                        kw in block
                        for kw in [
                            "log",
                            "error",
                            "exception",
                            "raise",
                            "structlog",
                            "pass",
                        ]
                    ):
                        errors.append(
                            f"{file_path}:{i + 1}: Silent Exception - Exception caught but no logging or re-raise found in the next 5 lines."
                        )

        # 4. Anti-Self-DDoS: Tight Loops
        if CODE_TIGHT_LOOP not in file_suppression:
            for i, line in enumerate(lines):
                if "while True:" in line:
                    if _is_suppressed(CODE_TIGHT_LOOP, line, file_suppression):
                        continue
                    # Check next 10 lines for await asyncio.sleep
                    block = "\n".join(lines[i + 1 : i + 11])
                    if (
                        "asyncio.sleep" not in block
                        and "await " not in block
                        and "break" not in block
                    ):
                        errors.append(
                            f"{file_path}:{i + 1}: Tight Loop Warning - 'while True' loop found without 'asyncio.sleep' in the next 10 lines."
                        )

    except Exception as e:
        print(f"Error reading {file_path}: {e}")

    return errors


def check_observability(search_root: Path) -> list[str]:
    errors: list[str] = []
    content_combined = ""
    for py_file in search_root.rglob("*.py"):
        if any(part in IGNORE_PATHS for part in py_file.parts):
            continue
        content_combined += py_file.read_text(encoding="utf-8") + "\n"

    if re.search(r"\bcreate_service_app\s*\(", content_combined):
        return errors

    # A route path counts when it is registered via Litestar (`.get`/`.route`),
    # served through the shared HealthController, exposed via
    # build_prometheus_instrumentation, or matched by a stdlib HTTP handler
    # (`self.path == "/health"`, membership in a set, etc.). The last form lets
    # non-Litestar processes such as the Discord bot satisfy the contract with
    # their lightweight health server.
    markers = [
        (
            [
                r'(\.get\(|\.route\()\s*[\'"]\/metrics[\'"]',
                r"build_prometheus_instrumentation\s*\(",
                r'[\'"]\/metrics[\'"]',
            ],
            "Prometheus /metrics",
        ),
        (
            [
                r'(\.get\(|\.route\()\s*[\'"]\/health[\'"]',
                r"\bHealthController\b",
                r'[\'"]\/health[\'"]',
            ],
            "Health /health",
        ),
        (
            [
                r'(\.get\(|\.route\()\s*[\'"]\/ready[\'"]',
                r"\bHealthController\b",
                r'[\'"]\/ready[\'"]',
            ],
            "Readiness /ready",
        ),
        (
            [r"opentelemetry|instrumentation|tracer|setup_tracing\s*\("],
            "OpenTelemetry init",
        ),
    ]

    for patterns, name in markers:
        if not any(re.search(pattern, content_combined) for pattern in patterns):
            errors.append(
                f"Missing Observability Contract: Project must implement {name}"
            )

    return errors


def main():
    if len(sys.argv) < 2:
        print("Usage: lp-arch-lint <directory>")
        sys.exit(1)

    search_root = Path(sys.argv[1]).absolute()
    if not search_root.exists():
        print(f"Path not found: {search_root}")
        sys.exit(1)

    # Robust current_service detection
    SERVICE_DIRS = {s.replace("-", "_") for s in INTERNAL_SERVICE_NAMES} | {
        s.replace("_", "-") for s in INTERNAL_SERVICE_NAMES
    }

    current_service = None
    for part in search_root.parts:
        if part in SERVICE_DIRS:
            current_service = part.replace("-", "_")
            break

    total_errors = []

    for file_path in search_root.rglob("*.py"):
        if any(part in IGNORE_PATHS for part in file_path.parts):
            continue
        total_errors.extend(check_file(file_path, current_service))

    total_errors.extend(check_observability(search_root))

    if total_errors:
        print(f"\n❌ Architecture Lint Failures in {search_root.name}:\n")
        for err in total_errors:
            print(err)
        print(f"\nTotal violations: {len(total_errors)}")
        sys.exit(1)
    else:
        print(f"✅ Architecture check passed for {search_root.name}")
        sys.exit(0)


if __name__ == "__main__":
    main()
