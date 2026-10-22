#!/usr/bin/env bash
# Build the installable extension zip into dist/ (needs Blender 4.2+ on PATH).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p dist
blender --command extension build --source-dir papercraft_pro --output-dir dist
