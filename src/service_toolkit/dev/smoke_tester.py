#!/usr/bin/env python3
"""
LeavePulse Smoke Tester.
Verifies service observability contract in a running container.
"""
# noqa: archlint=print,time-sleep — synchronous CLI tool: print() is
# user-facing output and time.sleep() is correct (no event loop to await on).

import sys
import time
import httpx


def check_endpoint(client: httpx.Client, url: str, name: str) -> bool:
    try:
        response = client.get(url)
        if response.status_code == 200:
            print(f"✅ {name} check passed ({url})")
            return True
        print(f"❌ {name} check failed: status {response.status_code} ({url})")
    except Exception as e:
        print(f"❌ {name} check failed: {e} ({url})")
    return False


def main():
    if len(sys.argv) < 2:
        print("Usage: lp-smoke-test <base_url> [service_name]")
        sys.exit(1)

    base_url = sys.argv[1].rstrip("/")
    service_name = sys.argv[2] if len(sys.argv) > 2 else "unknown"

    # Wait for service to be ready (retry loop)
    print(f"🔍 Starting smoke tests for {service_name} at {base_url}...")

    with httpx.Client(timeout=5.0) as client:
        # 1. Wait for /health
        retries = 10
        healthy = False
        for i in range(retries):
            if check_endpoint(client, f"{base_url}/health", "Health"):
                healthy = True
                break
            print(f"   Waiting for service... ({i + 1}/{retries})")
            time.sleep(2)

        if not healthy:
            sys.exit(1)

        # 2. Check /ready
        if not check_endpoint(client, f"{base_url}/ready", "Readiness"):
            sys.exit(1)

        # 3. Check /metrics
        try:
            resp = client.get(f"{base_url}/metrics")
            if resp.status_code == 200:
                # Check for custom metrics presence
                content = resp.text
                if "http_requests_total" in content or "process_cpu_seconds" in content:
                    print("✅ Metrics check passed (found Prometheus data)")
                else:
                    print(
                        "❌ Metrics check failed: No standard metrics found in output"
                    )
                    sys.exit(1)
            else:
                print(f"❌ Metrics check failed: status {resp.status_code}")
                sys.exit(1)
        except Exception as e:
            print(f"❌ Metrics check failed: {e}")
            sys.exit(1)

    print(f"🎉 Smoke tests passed for {service_name}!")
    sys.exit(0)


if __name__ == "__main__":
    main()
