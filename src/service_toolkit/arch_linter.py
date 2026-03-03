#!/usr/bin/env python3
"""
LeavePulse Architecture Linter (part of service-toolkit).
Enforces strict architectural boundaries and safety rules.
"""

import re
import sys
from pathlib import Path

# --- Configuration ---

# Patterns that are forbidden globally
FORBIDDEN_GLOBAL = [
    {
        "pattern": r"aiohttp\.ClientSession\(",
        "name": "Forbidden aiohttp",
        "reason": "Use 'httpx.AsyncClient' or shared clients from service-toolkit."
    },
    {
        "pattern": r"import aiohttp",
        "name": "Forbidden aiohttp import",
        "reason": "Use 'httpx' instead of 'aiohttp'."
    },
    {
        "pattern": r"requests\.(get|post|put|delete|patch)\(",
        "name": "Blocking requests call",
        "reason": "Forbidden in async services. Use 'httpx.AsyncClient'."
    },
    {
        "pattern": r"time\.sleep\(",
        "name": "Blocking time.sleep",
        "reason": "Forbidden in async services. Use 'asyncio.sleep()'."
    },
    {
        "pattern": r"\bprint\(",
        "name": "Raw print() found",
        "reason": "Use structured logging (structlog) with proper context."
    },
    {
        "pattern": r"except\s+Exception:\s*pass",
        "name": "Silent except pass",
        "reason": "Never silence exceptions without logging or re-raising."
    }
]

# Patterns that are forbidden in specific contexts
CONDITIONALLY_FORBIDDEN = [
    {
        "pattern": r"asyncio\.Lock\(\)",
        "trigger": r"cache|ttl|memo|dedup",
        "name": "Manual Cache Lock",
        "reason": "Manual locks around cache logic are error-prone. Use 'service_toolkit.AsyncLookupCache'."
    },
    {
        "pattern": r"(httpx\.AsyncClient\(|ClientSession\()",
        "exclude_files": r"lifespan|startup|singleton|client|conftest",
        "name": "Ad-hoc Client Creation",
        "reason": "Clients must be managed in lifespan/singletons to reuse connections."
    },
    {
        "pattern": r"timeout\s*=\s*None",
        "name": "Infinite Timeout",
        "reason": "Always specify a finite timeout to prevent hung requests."
    }
]

# Cross-service import boundaries
# Fails if e.g. server-service imports from auth_service
INTERNAL_SERVICE_NAMES = [
    "auth_service", "billing_service", "bot_service", 
    "community_service", "gateway_ingest", "monitoring_service",
    "realtime_service", "server_service", "whitelist_service"
]

IGNORE_PATHS = {
    ".venv", "venv", "__pycache__", ".git", "tests", "migrations", "alembic", "node_modules", "dist"
}

# --- Logic ---

def check_file(file_path: Path, current_service: str | None) -> list[str]:
    errors = []
    try:
        content = file_path.read_text(encoding="utf-8")
        lines = content.splitlines()
        content_lower = content.lower()
        
        # 1. Global Forbidden Patterns
        for rule in FORBIDDEN_GLOBAL:
            for i, line in enumerate(lines):
                if re.search(rule["pattern"], line):
                    errors.append(f"{file_path}:{i+1}: {rule['name']} - {rule['reason']}\n  -> {line.strip()}")

        # 2. Conditional Forbidden Patterns
        for rule in CONDITIONALLY_FORBIDDEN:
            if "exclude_files" in rule and re.search(rule["exclude_files"], str(file_path).lower()):
                continue
                
            for i, line in enumerate(lines):
                if re.search(rule["pattern"], line):
                    if "trigger" in rule and not re.search(rule["trigger"], content_lower):
                        continue
                    errors.append(f"{file_path}:{i+1}: {rule['name']} - {rule['reason']}\n  -> {line.strip()}")

        # 3. Cross-service boundaries
        if current_service:
            for other_service in INTERNAL_SERVICE_NAMES:
                # Normalize service name for import check (replace - with _)
                import_name = other_service.replace("-", "_")
                if import_name == current_service.replace("-", "_"):
                    continue
                
                pattern = rf"(from|import)\s+{import_name}"
                for i, line in enumerate(lines):
                    if re.search(pattern, line):
                        errors.append(
                            f"{file_path}:{i+1}: Boundary Violation - Direct import from '{other_service}' forbidden. "
                            f"Use API/SDK or service-toolkit.\n  -> {line.strip()}"
                        )

    except Exception as e:
        print(f"Error reading {file_path}: {e}")
        
    return errors

def check_observability(search_root: Path) -> list[str]:
    """Check if mandatory observability markers are present in the project."""
    errors = []
    content_combined = ""
    for py_file in search_root.rglob("*.py"):
        if any(ignored in str(py_file) for ignored in IGNORE_PATHS):
            continue
        content_combined += py_file.read_text(encoding="utf-8").lower() + "\n"

    markers = [
        (r"/metrics", "Prometheus metrics endpoint"),
        (r"/health", "Health check endpoint"),
        (r"/ready", "Readiness check endpoint"),
        (r"instrumentation|tracer|opentelemetry", "OpenTelemetry initialization")
    ]
    
    for pattern, name in markers:
        if not re.search(pattern, content_combined):
            errors.append(f"Missing Observability Contract: Project must implement {name}")
            
    return errors

def main():
    if len(sys.argv) < 2:
        print("Usage: lp-arch-lint <directory>")
        sys.exit(1)
        
    search_root = Path(sys.argv[1]).absolute()
    if not search_root.exists():
        print(f"Path not found: {search_root}")
        sys.exit(1)
        
    # Try to determine current service name from path
    current_service = None
    for part in search_root.parts:
        if any(svc in part for py_svc in INTERNAL_SERVICE_NAMES for svc in [py_svc, py_svc.replace("_", "-")]):
            current_service = part
            break

    total_errors = []
    
    # Check individual files
    for file_path in search_root.rglob("*.py"):
        if any(ignored in str(file_path) for ignored in IGNORE_PATHS):
            continue
        total_errors.extend(check_file(file_path, current_service))
        
    # Check project-wide contracts
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
