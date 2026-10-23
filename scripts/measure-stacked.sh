#!/usr/bin/env bash
# measure-stacked.sh - the 04:00 measurement of the stacked DS4 config, on a clean box or not at all.
#
# Scheduled by the transient unit ds4-measure-4am (systemd-run --user --on-calendar). It starts the
# engine, waits for /health, waits for the box to be quiet, then measures. The measurement tools
# themselves refuse to run when the 1- or 5-minute load is above 3.0, so a busy box yields NO numbers
# rather than misleading ones - that is deliberate.
#
# Writes /tmp/opencode/qa/stacked-4am.log and leaves the engine running.
set -u
LOG=/tmp/opencode/qa/stacked-4am.log
LOCK=/tmp/opencode/qa/.stacked-4am.lock
DIR={{HOME}}/.local/opt/maya-ds4
MAX_WAIT_MIN=${1:-45}      # how long to wait for a clean box before giving up

exec 9>"$LOCK"
if ! flock -n 9; then
    echo "another stacked measurement is already running; exiting" >>"$LOG"
    exit 0
fi

mkdir -p "$(dirname "$LOG")"
{
    echo "=================================================================="
    echo "stacked-config measurement, started $(date '+%Y-%m-%d %H:%M:%S %Z')"
    echo "binary: $(ls -l --time-style=+%H:%M {{HOME}}/.local/opt/project-maya-trial/upstream-update-20261020/build/maya-ds4 | awk '{print $6, $5}') bytes"
    echo "config: $(python3 -c "import json;c=json.load(open('$DIR/ds4-config.json'));print(json.dumps(c['env']))")"
    echo "load at start: $(cut -d' ' -f1-3 /proc/loadavg)   memavail: $(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo) MB"
} >>"$LOG"

# 1. the engine must be up
if ! systemctl --user is-active --quiet maya-ds4; then
    echo "starting maya-ds4 ..." >>"$LOG"
    systemctl --user start maya-ds4
fi
for _ in $(seq 1 60); do
    curl -sf -m 2 http://127.0.0.1:17866/health >/dev/null 2>&1 && break
    sleep 5
done
if ! curl -sf -m 3 http://127.0.0.1:17866/health >/dev/null 2>&1; then
    echo "ENGINE NEVER CAME UP - nothing measured" >>"$LOG"
    exit 1
fi
echo "engine healthy at $(date '+%H:%M:%S')" >>"$LOG"

# 2. wait for a genuinely quiet box (two consecutive clean minutes)
clean=0
for _ in $(seq 1 "$MAX_WAIT_MIN"); do
    l1=$(cut -d' ' -f1 /proc/loadavg); l5=$(cut -d' ' -f2 /proc/loadavg)
    if awk -v a="$l1" -v b="$l5" 'BEGIN{exit !(a<=2.0 && b<=2.0)}'; then clean=$((clean+1)); else clean=0; fi
    [ "$clean" -ge 2 ] && break
    sleep 60
done
if [ "$clean" -lt 2 ]; then
    echo "NO CLEAN WINDOW in ${MAX_WAIT_MIN} min (load $(cut -d' ' -f1-3 /proc/loadavg)) - nothing measured, by design" >>"$LOG"
    exit 0
fi
echo "clean at $(date '+%H:%M:%S') (load $(cut -d' ' -f1-3 /proc/loadavg)) - measuring" >>"$LOG"

# 3. the measurement: prefill 3 passes (drop the first 16k reading, it is page-cache cold after a
#    restart) and a warmed decode run. Both tools refuse if the box turns busy under them.
cd "$DIR" || exit 1
python3 measure.py --only prefill --big --repeat 3 >>"$LOG" 2>&1

# 4. settle before decode: the prefill run itself lifts the load average, and the decode tool rightly
#    refuses while it is high. Wait for it to decay (the 5-minute average needs a few minutes of idle)
#    instead of passing --allow-dirty, which would defeat the guard.
settled=0
for _ in $(seq 1 60); do
    l1=$(cut -d' ' -f1 /proc/loadavg); l5=$(cut -d' ' -f2 /proc/loadavg)
    if awk -v a="$l1" -v b="$l5" 'BEGIN{exit !(a<=2.0 && b<=2.0)}'; then settled=1; break; fi
    sleep 10
done
if [ "$settled" -eq 1 ]; then
    echo "settled at $(date '+%H:%M:%S') (load $(cut -d' ' -f1-3 /proc/loadavg)) - decoding" >>"$LOG"
    echo "--- decode (prose, 6 reps) ---" >>"$LOG"
    python3 bench_decode.py --prompt prose --reps 6 --variant "" >>"$LOG" 2>&1
else
    echo "--- decode SKIPPED: load never settled below 2.0 after the prefill run ---" >>"$LOG"
fi
echo "finished $(date '+%H:%M:%S'); engine left running" >>"$LOG"
