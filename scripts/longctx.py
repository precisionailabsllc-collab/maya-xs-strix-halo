#!/usr/bin/env python3
"""longctx.py - does the engine survive a conversation past 32,768 tokens?

v1.1.23 exists for this: "since v1.1.22, once a conversation passed 32,768 tokens the engine stopped
(`ds4 gguf mv: invalid argument`)" on AMD (Strix Halo, Radeon) and RTX 20. The long-row top-k merge sized its
buffer per-lane; on a GPU whose shared memory is 64 KiB a block that launch failed past 32K.

    ./longctx.py [tokens]        # default 34000, just past the limit

Builds a prompt from local documents (~4 chars a token), sends it, then asks a follow-up so the conversation
is past the limit on both the prefill and the decode side. Prints what the engine did: the prompt's tokens,
its reused/read split, and the decode speed - or the failure.
"""
import glob, json, os, re, sys, time, urllib.request

URL = "http://127.0.0.1:17866"
LOG = "{{HOME}}/.local/state/maya-ds4/engine.log"
MODEL = "deepseek-v4.1-flash-1017-maya-xs"


def corpus(chars):
    out = ""
    pats = ["{{HOME}}/Documents/Default Project/**/*.md",
            "{{HOME}}/.config/opencode/**/*.md",
            "{{HOME}}/.local/opt/maya-ds4/*.md"]
    for pat in pats:
        for p in sorted(glob.glob(pat, recursive=True)):
            try:
                out += open(p, encoding="utf-8", errors="ignore").read() + "\n\n"
            except OSError:
                continue
            if len(out) >= chars:
                return out[:chars]
    return (out + "filler text " * 20000)[:chars]


def last_prompt_line(start):
    try:
        with open(LOG, errors="replace") as f:
            f.seek(start)
            lines = f.read().splitlines()
    except OSError:
        return None
    for l in reversed(lines):
        if l.startswith("maya ds4 serve: prompt"):
            return l
    return None


def ask(messages, max_tokens):
    start = os.path.getsize(LOG) if os.path.exists(LOG) else 0
    body = json.dumps({"model": MODEL, "messages": messages, "max_tokens": max_tokens,
                       "temperature": 0}).encode()
    t0 = time.time()
    try:
        req = urllib.request.Request(URL + "/v1/chat/completions", body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3600) as f:
            out = json.load(f)
        return out, time.time() - t0, last_prompt_line(start)
    except Exception as e:                        # noqa: BLE001 - the failure is the result here
        return None, time.time() - t0, last_prompt_line(start), e


def main():
    want = int(sys.argv[1]) if len(sys.argv) > 1 else 34000
    doc = corpus(want * 4)
    print(f"prompt: {len(doc)} chars (~{len(doc)//4} tokens), aiming past 32,768")
    msgs = [{"role": "user", "content": doc + "\n\nIn one sentence, what is this text about?"}]
    res = ask(msgs, 24)
    if len(res) == 4:
        out, wall, line, err = res
        print(f"FAILED after {wall:.0f}s: {type(err).__name__}: {str(err)[:160]}")
        if line:
            print("  last engine line:", line[:160])
        return 1
    out, wall, line = res
    txt = (out["choices"][0]["message"].get("content") or "").strip()
    print(f"first turn ok in {wall:.0f}s; answer: {txt[:90]!r}")
    if line:
        print("  engine:", line[:150])
    # the conversation is now past the limit; a second turn is the case the bug hit on decode
    msgs += [{"role": "assistant", "content": txt},
             {"role": "user", "content": "Now give me three keywords from it, comma separated."}]
    res = ask(msgs, 24)
    if len(res) == 4:
        out, wall, line, err = res
        print(f"SECOND TURN FAILED after {wall:.0f}s: {type(err).__name__}: {str(err)[:160]}")
        return 1
    out, wall, line = res
    txt = (out["choices"][0]["message"].get("content") or "").strip()
    print(f"second turn ok in {wall:.0f}s; answer: {txt[:90]!r}")
    if line:
        print("  engine:", line[:150])
    print("PASS: the conversation went past 32,768 tokens")
    return 0


if __name__ == "__main__":
    sys.exit(main())
