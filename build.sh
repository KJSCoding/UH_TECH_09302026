#!/usr/bin/env bash
# Wraps src/app.html in a full HTML document and writes index.html (what GitHub Pages serves).
set -euo pipefail
cd "$(dirname "$0")"
{
  echo '<!doctype html>'
  echo '<html lang="en">'
  echo '<head>'
  echo '<meta charset="utf-8">'
  echo '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
  echo '<style>html,body{height:100%;margin:0}</style>'
  echo '</head>'
  echo '<body>'
  cat src/app.html
  echo '</body>'
  echo '</html>'
} > index.html
echo "Built index.html"
