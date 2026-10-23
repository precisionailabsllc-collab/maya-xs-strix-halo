#!/usr/bin/env bash
# Restart the engine safely and prove it came up.
#
#  - A start is only a start once /health answers; the unit's own auto-restart loops instead of telling you.
#  - <pack>/expert-profile.bin is the learned hot set: it decides *which* experts live in VRAM. Keep it. Dropping it
#    still fills the same 7,634 slots, but with the wrong experts, and decode falls from ~33-35 to ~9-15 tok/s
#    (measured 2026-10-21). `./restart.sh --fresh` drops it deliberately (a placement experiment), nothing else does.
#  - A start that dies on "cudaMalloc of layer N's ... experts failed" is short of system RAM, not short of VRAM:
#    every GB of GTT is pinned system RAM, and ~99 GB is needed. The usual thief is llama-swap's flash_serve
#    (laguna-s-2.1, ttl 0, ~15.4 GB): `systemctl --user stop halo-llamaswap` before a restart, start it again after.
#    So the retry waits longer for memory instead of throwing away the hot set.
#
#   ./restart.sh              # start, wait for health, report the placement
#   ./restart.sh --fresh      # drop the learned profile first (placement experiments only)
set -u
PACK={{HOME}}/models/DeepSeek-V4.1-Flash-1017-Maya-GGUF/Maya-S/maya-ds4
UNIT=maya-ds4
FRESH=0
[ "${1:-}" = "--fresh" ] && FRESH=1

gtt_gb() { awk '{printf "%.0f", $1/1e9}' /sys/class/drm/card0/device/mem_info_gtt_used; }

wait_gtt() {  # the previous instance's GTT is not free the instant systemd returns
    for _ in $(seq 1 40); do [ "$(gtt_gb)" -lt 5 ] && return 0; sleep 3; done
    echo "  (GTT still $(gtt_gb) GB after waiting)"
}

wait_health() {
    for i in $(seq 1 60); do
        if curl -sf -m 2 http://127.0.0.1:17866/health >/dev/null; then echo "  health ok after $((i * 5 - 5))s"; return 0; fi
        sleep 5
    done
    return 1
}

start_once() { systemctl --user start "$UNIT"; wait_health; }

systemctl --user stop "$UNIT"
wait_gtt
[ "$FRESH" = "1" ] && { echo "  dropping the learned profile (placement experiment)"; rm -f "$PACK/expert-profile.bin"; }

if ! start_once; then
    echo "  start failed; waiting for memory (llama-swap's flash_serve is the usual holder) and retrying"
    systemctl --user stop "$UNIT"
    sleep 30
    wait_gtt
    if ! start_once; then
        echo "  still failing; last lines:"
        tail -3 {{HOME}}/.local/state/maya-ds4/engine.log
        echo "  (if it is the cudaMalloc line: stop halo-llamaswap, then run this again)"
        exit 1
    fi
fi

echo "  lane:      $(grep -E 'routed experts on the CPU lane' {{HOME}}/.local/state/maya-ds4/engine.log | tail -1 | sed 's/^ds4: //')"
echo "  vram:      $(grep -E 'in VRAM' {{HOME}}/.local/state/maya-ds4/engine.log | tail -1 | sed 's/^ds4: //')"
echo "  gtt:       $(awk '{printf "%.1f GB", $1/1e9}' /sys/class/drm/card0/device/mem_info_gtt_used)"
echo "  edge temp: $(awk '{printf "%.0f C", $1/1000}' /sys/class/drm/card0/device/hwmon/hwmon*/temp1_input | head -1)"
echo "  env:       $(systemctl --user show "$UNIT" -p Environment | tr ' ' '\n' | grep -E 'DS4|STRATA' | tr '\n' ' ')"
