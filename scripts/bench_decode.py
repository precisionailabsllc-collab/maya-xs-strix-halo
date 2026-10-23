#!/usr/bin/env python3
"""Decode benchmark: the engine's own decode_ms over interleaved variants.

Per-request tok/s is too noisy to compare settings (short requests, warm-up, the tier heat).
This keeps two things strict:

  1. **Only the lines this request wrote.** The log offset is taken before the request and
     only the lines appended after it are parsed, so a late flush or a neighbouring request
     cannot be counted twice. (The first version of this script read the *last* prompt line
     at return time and reported 2x token counts for one of the variants.)
  2. **The engine's decode_ms, not the wall clock.** `N generated in X ms` excludes the
     prompt, so a cold page cache does not look like slow decode.

`drafts N of M kept` is a line of its own and is read separately from the prompt line.

    ./bench_decode.py --prompt prose --variant "" --variant dspark_conf=0.8 --reps 6

Variants are interleaved round-robin, so drift (heat, background load, page cache) hits every
variant the same; the printed tok/s is the sum over reps, which is what makes it reproducible.
"""
import argparse, json, os, re, statistics, sys, time, urllib.request

URL = "http://127.0.0.1:17866"
LOG = "{{HOME}}/.local/state/maya-ds4/engine.log"
MODEL = "deepseek-v4.1-flash-1017-maya-xs"
NUMBER_PROMPT = "List the numbers 1 to 30 as a comma separated list. Numbers only."
PROSE_PROMPT = "Write two paragraphs about why the sky appears blue. Do not use bullet points."
CODE_PROMPT = ("Write a Python function that merges two sorted lists into one sorted list, "
               "with a docstring and two doctest examples.")


def ask(prompt, variant, max_tokens):
    body = {"model": MODEL, "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens}
    if variant:
        k, v = variant.split("=")
        body.setdefault("strata_tune", {})[k] = float(v)
    req = urllib.request.Request(URL + "/v1/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as f:
        out = json.load(f)
    usage = out.get("usage") or {}
    return usage.get("completion_tokens") or 0, out["choices"][0].get("finish_reason")


def new_lines(start):
    """Only what was appended to the log since `start`."""
    try:
        with open(LOG, errors="replace") as f:
            f.seek(start)
            return f.read().splitlines()
    except OSError:
        return []


def figures(lines):
    """(generated, decode_ms, kept, offered) from this request's own lines."""
    gen = ms = kept = offered = None
    for l in lines:
        if l.startswith("maya ds4 serve: prompt"):
            m = re.search(r"(\d+) generated in (\d+) ms", l)
            if m:
                gen, ms = int(m.group(1)), int(m.group(2))
        m = re.search(r"drafts (\d+) of (\d+) kept", l)
        if m:
            kept, offered = int(m.group(1)), int(m.group(2))
    return gen, ms, kept, offered


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", action="append", default=None,
                    help="key=value for strata_tune; pass --variant '' for the engine's own default")
    ap.add_argument("--reps", type=int, default=6)
    ap.add_argument("--prompt", default="prose", choices=["prose", "numbers", "code"])
    ap.add_argument("--max-tokens", type=int, default=200)
    ap.add_argument("--max-load", type=float, default=1.0,
                    help="strict: the 1-minute load must be at or below this (a quiet box idles under 0.5)")
    ap.add_argument("--max-load-5m", type=float, default=2.0,
                    help="strict: the 5-minute load must be at or below this too")
    ap.add_argument("--wait-quiet", type=float, default=600.0,
                    help="wait this many seconds for a strict quiet window before giving up")
    ap.add_argument("--quiet-samples", type=int, default=3,
                    help="consecutive quiet samples, 20 s apart, needed before measuring")
    ap.add_argument("--allow-dirty", action="store_true", help="measure anyway on a busy box")
    a = ap.parse_args()

    def loadavg():
        try:
            v = open("/proc/loadavg").read().split()
            return float(v[0]), float(v[1])
        except OSError:
            return 0.0, 0.0

    def check_quiet():
        l1, l5 = loadavg()
        return (l1 <= a.max_load and l5 <= a.max_load_5m), l1, l5

    ok, l1, l5 = check_quiet()
    if not ok and not a.allow_dirty:
        print(f"box: load {l1:.2f}/{l5:.2f} - waiting up to {a.wait_quiet:.0f}s for load <= "
              f"{a.max_load}/{a.max_load_5m} x{a.quiet_samples}", flush=True)
        deadline = time.time() + a.wait_quiet
        streak = 0
        while time.time() < deadline and streak < a.quiet_samples:
            time.sleep(20)
            ok, l1, l5 = check_quiet()
            streak = streak + 1 if ok else 0
            print(f"  waiting: load {l1:.2f}/{l5:.2f} ({streak}/{a.quiet_samples})", flush=True)
        if streak < a.quiet_samples:
            raise SystemExit(f"no strict quiet window in {a.wait_quiet:.0f}s (load {l1:.2f}/{l5:.2f}); "
                             f"nothing measured. Re-run when idle, or pass --allow-dirty knowingly.")
    print(f"box: load {l1:.2f}/{l5:.2f}  " + ("(strict quiet)" if ok else "** DIRTY (allowed) **"))
    variants = a.variant if a.variant else [""]
    prompt = {"prose": PROSE_PROMPT, "numbers": NUMBER_PROMPT, "code": CODE_PROMPT}[a.prompt]

    acc = {v: {"tok": 0, "ms": 0, "per": [], "kept": 0, "off": 0, "mismatch": 0}
           for v in variants}
    for _ in range(a.reps):
        for v in variants:                       # interleaved: drift hits every variant alike
            start = os.path.getsize(LOG) if os.path.exists(LOG) else 0
            client_tok, finish = ask(prompt, v, a.max_tokens)
            time.sleep(0.25)                     # let the engine's log flush
            gen, ms, kept, off = figures(new_lines(start))
            o = acc[v]
            if not ms:
                print(f"  (no decode line for {v!r}, finish={finish})", file=sys.stderr)
                continue
            o["tok"] += gen
            o["ms"] += ms
            o["per"].append(gen / (ms / 1000.0))
            if kept is not None:
                o["kept"] += kept
                o["off"] += off
            if gen != client_tok:                # the two accounts must agree, else the
                o["mismatch"] += 1               # offset/log plumbing is lying to us again
    print(f"prompt={a.prompt} reps={a.reps} max_tokens={a.max_tokens}")
    print(f"{'variant':>18s} {'tok/s':>7s} {'median':>7s} {'tokens':>7s} {'accept':>7s} {'mismatch':>9s}")
    for v in variants:
        o = acc[v]
        if not o["ms"]:
            continue
        rate = o["tok"] / (o["ms"] / 1000.0)
        med = statistics.median(o["per"])
        accs = f"{100.0 * o['kept'] / o['off']:.0f}%" if o["off"] else "-"
        print(f"{v or 'engine-default':>18s} {rate:>7.2f} {med:>7.2f} {o['tok']:>7d} {accs:>7s} {o['mismatch']:>9d}")


if __name__ == "__main__":
    main()
