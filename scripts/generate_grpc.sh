#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

python -m service_toolkit.grpc.codegen \
    --proto-dir "$ROOT_DIR/protos" \
    --out-dir "$ROOT_DIR/src/service_toolkit/grpc/generated" \
    --import-prefix "service_toolkit.grpc.generated.leavepulse"
