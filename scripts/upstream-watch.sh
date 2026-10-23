#!/usr/bin/env bash
# upstream-watch.sh - watch everything that could move this build, for 48 hours, detached from any session.
#
# Watches, every 30 minutes:
#   mw00/project-maya   main's sha, the latest release, and comments on our issues #402 (tree verification)
#                       and #355 (the test-link bug, closed - a comment there may point at a fix)
#   SafeAILab/EAGLE     main's sha and tags: EAGLE-4's code (tree verification + harness) is what we want, and
#                       a DeepSeek-compatible head would be the other half
#   Hugging Face        EAGLE-Corp's models (an EAGLE-4 head for DeepSeek would change everything), any model
#                       matching EAGLE4 / v4.1 eagle / dspark / maya, and peasantsmith's own repo list
#
# A change is appended to the log with a banner and also written to a FLAG file, so "has anything moved?"
# is one `ls` away rather than a grep through a log. Nothing here touches the engine.
set -u
LOG=/tmp/opencode/qa/upstream-watch.log
FLAG=/tmp/opencode/qa/upstream-watch.CHANGED
BASE=/tmp/opencode/qa/upstream-watch.baseline
SRC={{HOME}}/.local/opt/project-maya-trial/upstream-update-20261020/source
INTERVAL=${1:-1800}          # seconds between checks
HOURS=${2:-48}
ITER=$(( HOURS * 3600 / INTERVAL ))

hf_models() {   # $1 = query string -> sorted list of ids, one per line
    curl -sf -m 30 "https://huggingface.co/api/models?search=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))" "$1")&limit=200" 2>/dev/null \
        | python3 -c "import json,sys
try: d=json.load(sys.stdin)
except Exception: d=[]
print('\n'.join(sorted(m['id'] for m in d)))" 2>/dev/null
}

snapshot() {
    echo "maya_main $(git -C $SRC ls-remote origin main 2>/dev/null | cut -f1)"
    echo "maya_release $(gh release view --repo mw00/project-maya --json tagName -q .tagName 2>/dev/null || echo none)"
    echo "issue402 $(gh issue view 402 --repo mw00/project-maya --json comments,state -q '(.state // \"?\") + \":\" + ((.comments|length)|tostring)' 2>/dev/null || echo '?:0')"
    echo "issue355 $(gh issue view 355 --repo mw00/project-maya --json comments,state -q '(.state // \"?\") + \":\" + ((.comments|length)|tostring)' 2>/dev/null || echo '?:0')"
    echo "eagle_main $(git ls-remote https://github.com/SafeAILab/EAGLE main 2>/dev/null | cut -f1)"
    echo "eagle_tags $(git ls-remote --tags https://github.com/SafeAILab/EAGLE 2>/dev/null | awk '{print $2}' | sort | tr '\n' ' ')"
    echo "eagle_corp $(hf_models 'EAGLE-Corp' | tr '\n' ' ')"
    echo "eagle4_hits $(hf_models 'EAGLE4' | tr '\n' ' ')"
    echo "ds_eagle $(hf_models 'deepseek eagle' | tr '\n' ' ')"
    echo "dspark_hits $(hf_models 'dspark' | tr '\n' ' ')"
    echo "maya_hits $(hf_models 'maya deepseek' | tr '\n' ' ')"
    echo "smith_hits $(hf_models 'peasantsmith' | tr '\n' ' ')"
}

mkdir -p "$(dirname "$LOG")"
if [ ! -f "$BASE" ]; then
    snapshot > "$BASE"
    { echo "=== watch started $(date '+%Y-%m-%d %H:%M:%S'), every ${INTERVAL}s for ${HOURS}h (${ITER} checks)";
      echo "--- baseline:"; sed 's/^/    /' "$BASE"; } >> "$LOG"
else
    echo "=== watch resumed $(date '+%Y-%m-%d %H:%M:%S'), reusing the baseline" >> "$LOG"
fi

for i in $(seq 1 "$ITER"); do
    sleep "$INTERVAL"
    snapshot > "$BASE.new"
    if ! diff -q "$BASE" "$BASE.new" >/dev/null 2>&1; then
        {
            echo
            echo "########## CHANGE at $(date '+%Y-%m-%d %H:%M:%S') (check $i/$ITER) ##########"
            diff "$BASE" "$BASE.new" | sed 's/^/  /'
            echo "--- latest release notes, if the release moved:"
            gh release view "$(grep maya_release "$BASE.new" | awk '{print $2}')" --repo mw00/project-maya --json body -q .body 2>/dev/null | head -30
            echo "--- comments on our issues:"
            for n in 402 355; do
                gh issue view $n --repo mw00/project-maya --json comments -q '.comments[-2:][] | "  #'"$n"' " + .author.login + ": " + (.body|.[0:400])' 2>/dev/null
            done
            echo "--- new commits on main:"
            timeout 120 git -C $SRC fetch -q origin 2>/dev/null
            git -C $SRC log --oneline $(awk '{print substr($2,1,7)}' <<< "$(grep maya_main "$BASE")")..origin/main 2>/dev/null | head -15
            echo "##########"
        } >> "$LOG"
        cp "$BASE.new" "$BASE"
        date '+%Y-%m-%d %H:%M:%S' > "$FLAG"
    fi
done
echo "=== watch ended $(date '+%Y-%m-%d %H:%M:%S') after ${HOURS}h" >> "$LOG"
