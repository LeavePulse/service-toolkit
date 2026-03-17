#!/usr/bin/env bash
#
# Generate Python gRPC stubs from proto definitions.
#
# Usage:
#   ./scripts/generate_grpc.sh
#
# The generated code is committed to src/service_toolkit/grpc/generated/.
# CI should run this script and verify no files changed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

PROTO_DIR="$ROOT_DIR/protos"
OUT_DIR="$ROOT_DIR/src/service_toolkit/grpc/generated"

# Clean previous output (except __init__.py)
find "$OUT_DIR" -name '*.py' -not -name '__init__.py' -delete
find "$OUT_DIR" -name '*.pyi' -delete

# Collect all .proto files
PROTO_FILES=()
while IFS= read -r -d '' file; do
    PROTO_FILES+=("$file")
done < <(find "$PROTO_DIR" -name '*.proto' -print0)

if [ ${#PROTO_FILES[@]} -eq 0 ]; then
    echo "No .proto files found in $PROTO_DIR"
    exit 1
fi

echo "Generating gRPC stubs for ${#PROTO_FILES[@]} proto files..."

python -m grpc_tools.protoc \
    --proto_path="$PROTO_DIR" \
    --python_out="$OUT_DIR" \
    --grpc_python_out="$OUT_DIR" \
    --pyi_out="$OUT_DIR" \
    "${PROTO_FILES[@]}"

# Fix imports in generated files: replace "from leavepulse." with absolute imports
# so that the package works as service_toolkit.grpc.generated.leavepulse.*
python -c "
import pathlib, re
out = pathlib.Path('$OUT_DIR')
pat = re.compile(r'^from leavepulse\.', re.MULTILINE)
repl = 'from service_toolkit.grpc.generated.leavepulse.'
for f in out.rglob('*_pb2*.py'):
    text = f.read_text()
    new = pat.sub(repl, text)
    if new != text:
        f.write_text(new)
"

# Ensure __init__.py files exist in all generated package directories
find "$OUT_DIR" -type d -exec touch {}/__init__.py \;

echo "Done. Generated stubs in $OUT_DIR"
