#!/usr/bin/env python3
"""Measure the running engine the way the engine counts it.

Sends probes through the OpenAI endpoint and then reads the engine's own
accounting line back out of engine.log, so prefill and decode rates are the
engine's numbers rather than wall-clock guesses:

  prompt 2461 tokens = 0 reused + 2461 read in 26450 ms (93.0 tok/s),
  1 generated in 0 ms (4122.13 tok/s); CPU lane 0.1% of the experts

Two prefill probes (one repetitive, one unique text) and three decode probes
(prose, code, numbers) because decode speed depends almost entirely on whether
a prompt's experts are in the hot set.

  python3 measure.py
  python3 measure.py --url http://127.0.0.1:17866 --log ~/.local/state/maya-ds4/engine.log
"""

import argparse
import json
import os
import re
import time
import urllib.request

NUMB = ("The quick brown fox jumps over the lazy dog. A KV cache stores the attention "
        "keys and values so a continuation does not recompute them. ")
PROSE = ("Sourdough begins with a wet flour paste left to stand until wild yeasts and "
         "lactobacilli take hold. The starter doubles, smells of yoghurt and green apple, "
         "and is folded into a dough that is turned every forty minutes so the gluten "
         "strengthens without tearing. ")
CODE = ("def merge(left, right):\n    out = []\n    while left and right:\n"
        "        if left[0] <= right[0]:\n            out.append(left.pop(0))\n"
        "        else:\n            out.append(right.pop(0))\n"
        "    return out + left + right\n\n")
UNIQUE = ("The ferry timetable changed twice that winter, so the post arrived on Tuesdays with "
          "the doctor and on Fridays with the coal. Nobody minded until the harbour office "
          "started charging storage by the hour, at which point every crate in the shed became "
          "somebody's grievance. ")


def long_unique(chars):
    """A long prompt of genuinely unique prose: concatenated local docs, not one paragraph repeated.

    Repetition would make the routing hot and flatter the number; the layer-skip's own claim is
    about prompts of ~3,700 new tokens or more, so it needs a plain document-sized prompt.
    """
    import glob
    text = ""
    for pat in ["{{HOME}}/.config/opencode/AGENTS.md", "{{HOME}}/.config/opencode/rules/*.md",
                "{{HOME}}/.local/opt/maya-ds4/*.md",
                "{{HOME}}/Documents/Default Project/**/*.md"]:
        for p in sorted(glob.glob(pat, recursive=True)):
            try:
                text += open(p, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            text += "\n\n"
            if len(text) >= chars:
                return text[:chars]
    return (text + UNIQUE * 400)[:chars]

LINE = re.compile(r"prompt (\d+) tokens = (\d+) reused \+ (\d+) read in (\d+) ms \(([\d.]+) tok/s\), "
                  r"(\d+) generated in (\d+) ms \(([\d.]+) tok/s\)(?:; CPU lane ([\d.]+)% of the experts)?")


def last_counts(log_path, before):
    """Return the accounting lines written since `before` (byte offset)."""
    try:
        with open(log_path, "rb") as f:
            f.seek(before)
            tail = f.read().decode("utf-8", "ignore")
    except OSError:
        return []
    out = []
    for m in LINE.finditer(tail):
        out.append({"prompt": int(m.group(1)), "reused": int(m.group(2)), "read": int(m.group(3)),
                    "prefill_ms": int(m.group(4)), "prefill_tps": float(m.group(5)),
                    "gen": int(m.group(6)), "decode_ms": int(m.group(7)),
                    "decode_tps": float(m.group(8)),
                    "lane": float(m.group(9)) if m.group(9) else None})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:17866")
    ap.add_argument("--model", default="deepseek-v4.1-flash-1017-maya-xs")
    ap.add_argument("--log", default=os.path.expanduser("~/.local/state/maya-ds4/engine.log"))
    ap.add_argument("--repeat", type=int, default=1,
                    help="run each probe N times in a row; rising numbers = the expert cache adapting")
    ap.add_argument("--big", action="store_true",
                    help="also probe 8k and 16k unique-document prompts (where upstream's layer-skip pays)")
    ap.add_argument("--max-load", type=float, default=1.0,
                    help="strict: the 1-minute load must be at or below this (a quiet box idles under 0.5)")
    ap.add_argument("--max-load-5m", type=float, default=2.0,
                    help="strict: the 5-minute load must be at or below this too; it still carries the previous "
                         "run's own tail, which is why the wait takes a few minutes")
    ap.add_argument("--wait-quiet", type=float, default=600.0,
                    help="wait this many seconds for a strict quiet window before giving up")
    ap.add_argument("--quiet-samples", type=int, default=3,
                    help="consecutive quiet samples, 20 s apart, needed before measuring")
    ap.add_argument("--allow-dirty", action="store_true",
                    help="measure anyway on a busy box; the numbers then belong in the bin, not the docs")
    ap.add_argument("--only", default="",
                    help="run only the probes whose label contains this text (e.g. 'decode', '16k')")
    args = ap.parse_args()

    probes = [
        ("prefill 2.5k repetitive", PROSE * 45, 1),
        ("prefill 2.5k unique", UNIQUE * 20, 1),
    ]
    if args.big:
        probes += [("prefill 8k unique doc", long_unique(30000), 1),
                   ("prefill 16k unique doc", long_unique(60000), 1)]
    probes += [
        ("decode prose", PROSE * 12, 200),
        ("decode code", CODE * 40, 200),
        ("decode numbers", NUMB * 12, 200),
    ]
    if args.only:
        probes = [p for p in probes if args.only in p[0]]
        if not probes:
            raise SystemExit(f"no probe matches --only {args.only!r}")
    def box_state():
        """Dirty-box detection: a measurement taken beside another workload is not a measurement."""
        try:
            la = open("/proc/loadavg").read().split()
            avail = [l for l in open("/proc/meminfo") if l.startswith("MemAvailable")][0].split()[1]
            return float(la[0]), float(la[1]), int(avail) // 1024
        except OSError:
            return 0.0, 0.0, 0

    def check_quiet():
        l1, l5, avail = box_state()
        return (l1 <= args.max_load and l5 <= args.max_load_5m), l1, l5, avail

    ok, l1, l5, avail = check_quiet()
    if not ok and not args.allow_dirty:
        # Strict window: both averages, several consecutive samples 20 s apart. The 5-minute average still
        # carries this script's OWN previous phase (a prefill, or a restart's read-ahead), so the wait is
        # what makes the numbers mean anything - a run that starts at the threshold reads 10-20% low and
        # erratic, which is worse than no number at all.
        print(f"box: load {l1:.2f}/{l5:.2f}  memavail {avail} MB - waiting up to {args.wait_quiet:.0f}s for "
              f"load <= {args.max_load}/{args.max_load_5m} x{args.quiet_samples}", flush=True)
        deadline = time.time() + args.wait_quiet
        streak = 0
        while time.time() < deadline and streak < args.quiet_samples:
            time.sleep(20)
            ok, l1, l5, avail = check_quiet()
            streak = streak + 1 if ok else 0
            print(f"  waiting: load {l1:.2f}/{l5:.2f} ({streak}/{args.quiet_samples})", flush=True)
        if streak < args.quiet_samples:
            raise SystemExit(f"no strict quiet window in {args.wait_quiet:.0f}s (load {l1:.2f}/{l5:.2f}); "
                             f"nothing measured. Re-run when the box is idle, or pass --allow-dirty knowingly.")
    print(f"box: load {l1:.2f}/{l5:.2f}  memavail {avail} MB"
          + ("" if ok else "   ** DIRTY (allowed) - not comparable **"))
    print(f"{'probe':<24} {'in':>5} {'out':>4} {'prefill':>9} {'decode':>8} {'lane':>6}  wall")
    seen = {}
    for rep in range(args.repeat):
        for label, prompt, n in probes:
            try:
                before = os.path.getsize(args.log)
            except OSError:
                before = 0
            body = json.dumps({"model": args.model,
                               "messages": [{"role": "user", "content": prompt}],
                               "max_tokens": n}).encode()
            t0 = time.time()
            req = urllib.request.Request(f"{args.url}/v1/chat/completions", body,
                                         {"Content-Type": "application/json"})
            json.loads(urllib.request.urlopen(req, timeout=3600).read())
            wall = time.time() - t0
            lines = last_counts(args.log, before)
            if not lines:
                print(f"{label:<24} {'-':>5} {'-':>4} {'-':>9} {'-':>8} {'-':>6}  {wall:.0f}s", flush=True)
                continue
            c = lines[-1]
            lane = f"{c['lane']:.1f}%" if c["lane"] is not None else "-"
            tag = f"{label:<24}" if args.repeat == 1 else f"{label} #{rep + 1:<3}"
            print(f"{tag:<24} {c['prompt']:>5} {c['gen']:>4} {c['prefill_tps']:>7.1f} t "
                  f"{c['decode_tps']:>6.1f} t {lane:>6}  {wall:.0f}s", flush=True)
            seen.setdefault(label, []).append(c["decode_tps"] if n > 1 else c["prefill_tps"])
    if args.repeat > 1:
        print("\nfirst -> last (same probe repeated, no restart in between):")
        for label, vals in seen.items():
            unit = "tok/s decode" if len(vals) and probes[[p[0] for p in probes].index(label)][2] > 1 else "tok/s prefill"
            print(f"  {label:<24} {vals[0]:6.1f} -> {vals[-1]:6.1f} {unit}  "
                  f"({(vals[-1] / vals[0] - 1) * 100:+.0f}%)")
    print("(t = tok/s from the engine's own account; lane = share of that request's experts "
          "served by the CPU lane)")
    l1, l5, avail = box_state()
    print(f"box at end: load {l1:.1f}/{l5:.1f}  memavail {avail} MB"
          "   (the run itself lifts the load; the start-of-run check is the one that matters)")


if __name__ == "__main__":
    main()
