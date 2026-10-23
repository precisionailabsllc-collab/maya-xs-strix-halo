#!/usr/bin/env bash
# install.sh - point the packaged scripts at your home directory.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
for f in "$here"/*.py "$here"/*.sh; do
    [ "$(basename "$f")" = "install.sh" ] && continue
    sed -i "s|{{HOME}}|$HOME|g" "$f"
done
chmod +x "$here"/*.sh 2>/dev/null || true
echo "rewrote {{HOME}} -> $HOME in $(ls "$here" | wc -l) files"
echo "next: python3 measure.py --only prefill --big --repeat 2"
