#!/usr/bin/env bash
# install.sh - point the packaged scripts and the systemd unit at your own home directory.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
root="$(dirname "$here")"
for f in "$here"/*.py "$here"/*.sh "$root"/config/*.service; do
    [ -f "$f" ] || continue
    [ "$(basename "$f")" = "install.sh" ] && continue
    sed -i "s|{{HOME}}|$HOME|g" "$f"
done
chmod +x "$here"/*.sh 2>/dev/null || true
echo "rewrote {{HOME}} -> $HOME"
echo "next: cp $root/config/maya-ds4.service ~/.config/systemd/user/ && systemctl --user daemon-reload"
echo "then: python3 $here/measure.py --only prefill --big --repeat 2"
