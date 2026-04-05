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
    "gateway_ingest",
    "monitoring_service",
    "realtime_service",
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
        "pattern": r"aiohttp\.ClientSession\(",
        "name": "Forbidden aiohttp",
        "reason": "Use 'httpx.AsyncClient' or shared clients from service-toolkit.",
    },
    {
        "pattern": r"^\s*(import\s+aiohttp|from\s+aiohttp)",
        "name": "Forbidden aiohttp import",
        "reason": "Use 'httpx' instead of 'aiohttp'.",
    },
    {
        "pattern": r"\brequests\.(get|post|put|delete|patch)\(",
        "name": "Blocking requests call",
        "reason": "Forbidden in async services. Use 'httpx.AsyncClient'.",
    },
    {
        "pattern": r"\btime\.sleep\(",
        "name": "Blocking time.sleep",
        "reason": "Forbidden in async services. Use 'asyncio.sleep()'.",
    },
    {
        "pattern": r"\bprint\(",
        "name": "Raw print() found",
        "reason": "Use structured logging (structlog) with proper context.",
    },
    {
        "pattern": r"except\s*:",
        "name": "Bare except",
        "reason": "Forbidden. Catch 'Exception' or specific errors.",
    },
    {
        "pattern": r"\bTODO\b|\bFIXME\b",
        "negative_trigger": r"LP-\d+|https?://|#\d+",
        "name": "Untracked TODO",
        "reason": "TODO/FIXME must have a reference to an issue (LP-123) or URL.",
    },
]

# 2. Conditional Forbidden Patterns
CONDITIONALLY_FORBIDDEN: list[Rule] = [
    {
        "pattern": r"asyncio\.Lock\(\)",
        "trigger": r"\b(cache|ttl|memo|dedup)\b",
        "name": "Manual Cache Lock",
        "reason": "Manual locks around cache logic are error-prone. Use 'service_toolkit.LookupCache'.",
    },
    {
        "pattern": r"httpx\.AsyncClient\(",
        "exclude_files": r"lifespan|startup|singleton|client|conftest|core/config|deps|container|di|wiring|app",
        "name": "Ad-hoc Client Creation",
        "reason": "Clients must be managed in lifespan/singletons to reuse connections.",
    },
    {
        "pattern": r"timeout\s*=\s*None|httpx\.Timeout\(\s*None\s*\)",
        "name": "Dangerous Timeout",
        "reason": "Infinite timeout detected. Always specify a finite timeout (e.g. 5.0).",
    },
    {
        "pattern": r"os\.(environ|getenv)\(",
        "exclude_files": r"core/config|settings|env|constants",
        "name": "Direct Env Access",
        "reason": "Avoid accessing env vars directly in business logic. Use the 'settings' object.",
    },
    {
        "pattern": r"JWTVerifier\(|JWKSCache\(",
        "trigger": r"class\s+\w+(Handler|Socket|Connection)",
        "exclude_files": r"singleton|lifespan|app|deps|container",
        "name": "Request-scope Verifier",
        "reason": "JWTVerifier/JWKSCache created in connection/request scope. This causes JWKS spam. Move to singleton/lifespan or use a provider-owned shared verifier helper.",
    },
]

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


def check_file(file_path: Path, current_service: Optional[str]) -> list[str]:
    errors = []
    try:
        content = file_path.read_text(encoding="utf-8")
        if "# noqa: archlint" in content:
            return []

        lines = content.splitlines()

        # 1. Global & Conditional Rules
        for rule in FORBIDDEN_GLOBAL + CONDITIONALLY_FORBIDDEN:
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
                if "# noqa" in line:
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
        if current_service:
            for other_service in INTERNAL_SERVICE_NAMES:
                import_name = other_service.replace("-", "_")
                if import_name == current_service:
                    continue

                # Precise boundary pattern: from|import (\w+.)*import_name\b
                pattern = rf"^\s*(from|import)\s+(\w+\.)*{import_name}\b"
                for i, line in enumerate(lines):
                    if "# noqa" in line:
                        continue
                    if re.search(pattern, strip_comments(line)):
                        errors.append(
                            f"{file_path}:{i + 1}: Boundary Violation - Direct import from '{other_service}' forbidden.\n  -> {line.strip()}"
                        )

        # 3. Silent Exception Audit
        for i, line in enumerate(lines):
            if "except Exception:" in line or "except Exception as" in line:
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
        for i, line in enumerate(lines):
            if "while True:" in line:
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
    errors = []
    content_combined = ""
    for py_file in search_root.rglob("*.py"):
        if any(part in IGNORE_PATHS for part in py_file.parts):
            continue
        content_combined += py_file.read_text(encoding="utf-8") + "\n"

    if re.search(r"\bcreate_service_app\s*\(", content_combined):
        return errors

    markers = [
        (
            [
                r'(\.get\(|\.route\()\s*[\'"]\/metrics[\'"]',
                r"build_prometheus_instrumentation\s*\(",
            ],
            "Prometheus /metrics",
        ),
        (
            [
                r'(\.get\(|\.route\()\s*[\'"]\/health[\'"]',
                r"\bHealthController\b",
            ],
            "Health /health",
        ),
        (
            [
                r'(\.get\(|\.route\()\s*[\'"]\/ready[\'"]',
                r"\bHealthController\b",
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
