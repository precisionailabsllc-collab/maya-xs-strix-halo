#!/usr/bin/env python3
"""Learn a broad expert hot set by pushing mixed text through the running engine.

The engine keeps a per-expert usage count (`expert-profile.bin`) and puts the
hottest ~7,600 experts in VRAM. A profile learned from one repetitive prompt is
overfit: that prompt decodes fast and everything else routes to the CPU lane.
This walks prose, code, docs and a little filler past the prompt path (one
prefill pass per chunk, no decoding) so the placement favours experts that real
text actually uses.

Heat accumulates, so re-running is safe and just adds coverage. The profile is
saved by the engine periodically and on shutdown; restart the unit afterwards
for the new placement to take effect.

  python3 learn_hot_set.py --chunks 40 --url http://127.0.0.1:17866
"""

import argparse
import glob
import json
import time
import urllib.request

PROSE = ("Sourdough begins with a wet flour paste left to stand until wild yeasts and "
         "lactobacilli take hold. The starter doubles, smells of yoghurt and green apple, "
         "and is folded into a dough that is turned every forty minutes so the gluten "
         "strengthens without tearing. ")
CODE = ("def merge(left, right):\n    out = []\n    while left and right:\n"
        "        if left[0] <= right[0]:\n            out.append(left.pop(0))\n"
        "        else:\n            out.append(right.pop(0))\n"
        "    return out + left + right\n\n")
NUMB = ("The quick brown fox jumps over the lazy dog. A KV cache stores the attention "
        "keys and values so a continuation does not recompute them. ")
TALK = ("Richard asked whether the lane split was worth keeping. I said the bracket said "
        "three percent, not three times, and he wanted the number before we spent another "
        "night on it. ")
MULTI = ("El servidor arranca en unos sesenta segundos y luego responde en el puerto "
         "diecisiete mil ochocientos sesenta y seis. Der Speicher reicht nicht für beide "
         "Modelle gleichzeitig, deshalb läuft nur eines. ")

DOC_GLOBS = ["{{HOME}}/.config/opencode/AGENTS.md",
             "{{HOME}}/.config/opencode/rules/*.md",
             "{{HOME}}/.local/opt/maya-ds4/*.md",
             "{{HOME}}/Documents/Default Project/**/*.md"]
CODE_GLOBS = ["{{HOME}}/Documents/Default Project/**/*.{py,js,ts,sh}",
              "{{HOME}}/.local/opt/maya-ds4/*.py"]


def chunks_from(paths, size, per_file):
    out = []
    for p in paths:
        try:
            text = open(p, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        for i in range(0, min(len(text), size * per_file), size):
            piece = text[i:i + size]
            if len(piece) < size // 2:
                continue
            out.append(piece)
    return out


def build_pool(size, per_file):
    pool = []
    for g in DOC_GLOBS:
        pool += chunks_from(sorted(glob.glob(g, recursive=True)), size, per_file)
    for g in CODE_GLOBS:
        pool += chunks_from(sorted(glob.glob(g, recursive=True)), size, per_file)
    mixed = []
    for _ in range(4):
        mixed += [PROSE * 6, CODE * 25, TALK * 14, MULTI * 8]
    mixed.append(NUMB * 6)
    return pool, mixed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:17866")
    ap.add_argument("--model", default="deepseek-v4.1-flash-1017-maya-s")
    ap.add_argument("--chunks", type=int, default=40)
    ap.add_argument("--size", type=int, default=3500, help="characters per chunk")
    ap.add_argument("--per-file", type=int, default=1)
    args = ap.parse_args()

    docs, mixed = build_pool(args.size, args.per_file)
    # interleave: mostly real text, filler kept in so the bench prompt does not collapse
    pool, di, mi = [], 0, 0
    while len(pool) < args.chunks and (di < len(docs) or mi < len(mixed)):
        for _ in range(4):
            if di < len(docs) and len(pool) < args.chunks:
                pool.append(docs[di]); di += 1
        if mi < len(mixed) and len(pool) < args.chunks:
            pool.append(mixed[mi]); mi += 1

    print(f"pool: {len(docs)} doc/code chunks, {len(mixed)} mixed; sending {len(pool)}", flush=True)
    start = time.time()
    for i, text in enumerate(pool):
        body = json.dumps({"model": args.model,
                           "messages": [{"role": "user", "content": text}],
                           "max_tokens": 1}).encode()
        t0 = time.time()
        try:
            req = urllib.request.Request(f"{args.url}/v1/chat/completions", body,
                                         {"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=1800).read()
        except Exception as e:  # a bad chunk should not abort the run
            print(f"  chunk {i}: {e}", flush=True)
            continue
        print(f"  {i + 1}/{len(pool)}  {len(text)} chars in {time.time() - t0:.0f}s "
              f"(total {time.time() - start:.0f}s)", flush=True)
    print(f"done: {len(pool)} chunks in {time.time() - start:.0f}s", flush=True)


if __name__ == "__main__":
    main()
