#!/usr/bin/env bash
# Builds the page and serves it locally at http://localhost:8080
# No install needed: only bash and python3.
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PORT:-8080}"

echo "Building ShelfSight..."
./build.sh

echo "Starting ShelfSight on http://localhost:${PORT}"
python3 -m http.server "$PORT" >/dev/null 2>&1 &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null' EXIT
sleep 1

echo "Checking the app is up..."
if curl -s "http://localhost:${PORT}/index.html" | grep -q "ShelfSight Console"; then
  echo "OK: ShelfSight is running. Open http://localhost:${PORT} in your browser."
else
  echo "Server did not respond as expected." && exit 1
fi

echo "Press Ctrl+C to stop."
wait $SERVER_PID
