#!/usr/bin/env bash
# knob-sweep.sh - the levers never tested on Maya-XS, one at a time, each on its own restart.
#
# Baseline (Maya-XS, v1.1.24 + our 10, pool 100, stride 8192): prefill 8k ~456 / 16k ~428,
# decode prose 18.4-18.9 tok/s, 14,349/15,744 experts resident.
#
# Why these six: two of them matter MORE now that 91% of experts are resident - the CPU lane serves the
# remaining 1,417 experts (so its thread count and the transfer-pruning path are a bigger share of a token),
# and the others were never measured on this model at all.
set -u
CFG={{HOME}}/.local/opt/maya-ds4/ds4-config.json
LOG=/tmp/opencode/qa/xs-knob-sweep.log
DIR={{HOME}}/.local/opt/maya-ds4
BASE='{"DS4_POOL_GB": "100", "DS4_RING_MB": "512", "DS4_READERS": "16", "DS4_PREFIX_REUSE": "1", "MAYA_DS4_PROMPT_SKIP": "2", "DS4_SKIP_UNPICKED": "1", "MAYA_DS4_SLOT_KEEP": "1", "MAYA_DS4_SLOT_CAP": "4", "MAYA_DS4_SLOT_GB": "4", "DS4_PREFIX_STRIDE": "8192", "MAYA_DS4_Q8_BATCH": "1"}'

settle() { for i in $(seq 1 40); do l1=$(cut -d' ' -f1 /proc/loadavg); awk -v a="$l1" 'BEGIN{exit !(a<=1.0)}' && return 0; sleep 10; done; return 1; }

apply() {   # $1 = python dict of overrides
    python3 - "$1" <<'PY'
import json, sys
p = "{{HOME}}/.local/opt/maya-ds4/ds4-config.json"
c = json.load(open(p))
c["env"] = json.loads(sys.argv[1])
json.dump(c, open(p, "w"), indent=1)
PY
}

run_one() {   # $1 = label, $2 = python dict of overrides
    echo "" >> "$LOG"
    echo "########## $1" >> "$LOG"
    apply "$2"
    systemctl --user restart maya-ds4
    for i in $(seq 1 120); do curl -sf -m 2 http://127.0.0.1:17866/health >/dev/null 2>&1 && break; sleep 4; done
    settle
    echo "  placement: $(grep 'in VRAM)' {{HOME}}/.local/state/maya-ds4/engine.log | tail -1 | grep -oE '[0-9]+ of 15744')  avail $(free -g | awk '/^Mem:/{print $7}') GB" >> "$LOG"
    python3 - <<'PY' >> "$LOG" 2>&1
import json, urllib.request
for i in range(2):
    b = json.dumps({"model":"deepseek-v4.1-flash-1017-maya-s","messages":[{"role":"user","content":"Write two paragraphs about why the sky appears blue. Do not use bullet points."}],"max_tokens":200}).encode()
    urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:17866/v1/chat/completions", b, {"Content-Type":"application/json"}), timeout=1800).read()
PY
    cd "$DIR" && python3 bench_decode.py --prompt prose --reps 6 --variant "" >> "$LOG" 2>&1
    settle
    python3 measure.py --only prefill --big --repeat 2 >> "$LOG" 2>&1
}

: > "$LOG"
echo "=== Maya-XS knob sweep, $(date '+%Y-%m-%d %H:%M:%S') - baseline: decode 18.4-18.9, prefill 8k ~456 / 16k ~428" >> "$LOG"

run_one "A: DS4_SKIP_UNPICKED=0 (zero-row transfer pruning off - does it still pay with 91% resident?)" \
        '{"DS4_POOL_GB": "100", "DS4_RING_MB": "512", "DS4_READERS": "16", "DS4_PREFIX_REUSE": "1", "MAYA_DS4_PROMPT_SKIP": "2", "DS4_SKIP_UNPICKED": "0", "MAYA_DS4_SLOT_KEEP": "1", "MAYA_DS4_SLOT_CAP": "4", "MAYA_DS4_SLOT_GB": "4", "DS4_PREFIX_STRIDE": "8192", "MAYA_DS4_Q8_BATCH": "1"}'

run_one "B: MAYA_DS4_CPU_LANE=smt (32 threads for the 1,417 lane experts instead of 14)" \
        '{"DS4_POOL_GB": "100", "DS4_RING_MB": "512", "DS4_READERS": "16", "DS4_PREFIX_REUSE": "1", "MAYA_DS4_PROMPT_SKIP": "2", "DS4_SKIP_UNPICKED": "1", "MAYA_DS4_SLOT_KEEP": "1", "MAYA_DS4_SLOT_CAP": "4", "MAYA_DS4_SLOT_GB": "4", "DS4_PREFIX_STRIDE": "8192", "MAYA_DS4_Q8_BATCH": "1", "MAYA_DS4_CPU_LANE": "smt"}'

run_one "C: DS4_READERS=32 (disk readers; Engram rows and any non-resident expert pages)" \
        '{"DS4_POOL_GB": "100", "DS4_RING_MB": "512", "DS4_READERS": "32", "DS4_PREFIX_REUSE": "1", "MAYA_DS4_PROMPT_SKIP": "2", "DS4_SKIP_UNPICKED": "1", "MAYA_DS4_SLOT_KEEP": "1", "MAYA_DS4_SLOT_CAP": "4", "MAYA_DS4_SLOT_GB": "4", "DS4_PREFIX_STRIDE": "8192", "MAYA_DS4_Q8_BATCH": "1"}'

run_one "D: DS4_ROUTER_ROWS=0 (router one token at a time, against the default 8)" \
        '{"DS4_POOL_GB": "100", "DS4_RING_MB": "512", "DS4_READERS": "16", "DS4_PREFIX_REUSE": "1", "MAYA_DS4_PROMPT_SKIP": "2", "DS4_SKIP_UNPICKED": "1", "MAYA_DS4_SLOT_KEEP": "1", "MAYA_DS4_SLOT_CAP": "4", "MAYA_DS4_SLOT_GB": "4", "DS4_PREFIX_STRIDE": "8192", "MAYA_DS4_Q8_BATCH": "1", "DS4_ROUTER_ROWS": "0"}'

run_one "E: MAYA_DS4_STREAM=0 (PCIe lane off - every non-resident expert on the CPU)" \
        '{"DS4_POOL_GB": "100", "DS4_RING_MB": "512", "DS4_READERS": "16", "DS4_PREFIX_REUSE": "1", "MAYA_DS4_PROMPT_SKIP": "2", "DS4_SKIP_UNPICKED": "1", "MAYA_DS4_SLOT_KEEP": "1", "MAYA_DS4_SLOT_CAP": "4", "MAYA_DS4_SLOT_GB": "4", "DS4_PREFIX_STRIDE": "8192", "MAYA_DS4_Q8_BATCH": "1", "MAYA_DS4_STREAM": "0"}'

run_one "F: DS4_POOL_GB=104 (more residency, less headroom than the 100 that won before)" \
        '{"DS4_POOL_GB": "104", "DS4_RING_MB": "512", "DS4_READERS": "16", "DS4_PREFIX_REUSE": "1", "MAYA_DS4_PROMPT_SKIP": "2", "DS4_SKIP_UNPICKED": "1", "MAYA_DS4_SLOT_KEEP": "1", "MAYA_DS4_SLOT_CAP": "4", "MAYA_DS4_SLOT_GB": "4", "DS4_PREFIX_STRIDE": "8192", "MAYA_DS4_Q8_BATCH": "1"}'

# leave the box on the baseline that won
echo "" >> "$LOG"
echo "########## restoring the baseline config" >> "$LOG"
apply "$BASE"
systemctl --user restart maya-ds4
for i in $(seq 1 120); do curl -sf -m 2 http://127.0.0.1:17866/health >/dev/null 2>&1 && break; sleep 4; done
echo "=== sweep finished $(date '+%H:%M:%S'); baseline restored, health $(curl -sf -m 3 http://127.0.0.1:17866/health >/dev/null && echo up || echo DOWN)" >> "$LOG"
