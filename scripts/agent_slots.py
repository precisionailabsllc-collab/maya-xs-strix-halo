#!/usr/bin/env python3
"""agent_slots.py - does keeping conversations aside help an agent's interleaved traffic?

The case MAYA_DS4_SLOT_KEEP is for: several long conversations on one model, switching between them. Without
it, every switch re-reads the whole prompt from token 0; with it, the conversation comes back and only the
new tokens are read.

    ./agent_slots.py                 # prints one row per request: prompt, reused, read, wall

Two conversations (A and B) are built from local docs (~4k tokens each, real text). The sequence is
A1, B1, A2, B2 - so A2 arrives after B1 has taken the model, which is exactly the interleave.

Each row's `reused` comes from the engine's own line for that request (the log offset is taken before the
request, so a neighbouring request cannot be counted), and wall time is measured here.
"""
import glob, json, os, sys, time, urllib.request

URL = "http://127.0.0.1:17866"
LOG = "{{HOME}}/.local/state/maya-ds4/engine.log"
MODEL = "deepseek-v4.1-flash-1017-maya-xs"


def doc(pattern, limit_chars):
    out = ""
    for p in sorted(glob.glob(pattern, recursive=True)):
        try:
            out += open(p, encoding="utf-8", errors="ignore").read() + "\n\n"
        except OSError:
            continue
        if len(out) >= limit_chars:
            break
    return out[:limit_chars]


def figures(start):
    """(prompt, reused, read, prefill_ms) from this request's own log line."""
    try:
        with open(LOG, errors="replace") as f:
            f.seek(start)
            lines = f.read().splitlines()
    except OSError:
        return None
    for l in reversed(lines):
        if l.startswith("maya ds4 serve: prompt"):
            import re
            m = re.search(r"prompt (\d+) tokens = (\d+) reused \+ (\d+) read in (\d+) ms", l)
            if m:
                return tuple(int(x) for x in m.groups())
    return None


def ask(messages, max_tokens=48):
    start = os.path.getsize(LOG) if os.path.exists(LOG) else 0
    body = json.dumps({"model": MODEL, "messages": messages, "max_tokens": max_tokens,
                       "temperature": 0}).encode()
    t0 = time.time()
    req = urllib.request.Request(URL + "/v1/chat/completions", body,
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=1800) as f:
        out = json.load(f)
    wall = time.time() - t0
    time.sleep(0.3)
    m = out["choices"][0]["message"]
    # The server returns the thinking in reasoning_content and the answer in content, and the encoder
    # renders an assistant turn as "{reasoning}{content}". Echoing BOTH back is what makes this turn's
    # rendered prompt contain the tokens the model actually generated - without it the conversation
    # does not continue, prefix reuse finds nothing, and every turn re-reads from token 0.
    return (m.get("content") or "", m.get("reasoning_content") or "", wall, figures(start))


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 16000      # ~4k tokens
    docA = doc("{{HOME}}/.config/opencode/**/*.md", limit)
    docB = doc("{{HOME}}/Documents/Default Project/**/*.md", limit)
    if len(docA) < limit // 2 or len(docB) < limit // 2:
        print("not enough local text to build two conversations", file=sys.stderr)

    turns = {}
    print(f"{'request':<8} {'prompt':>7} {'reused':>7} {'read':>7} {'ms':>7} {'wall':>7}")
    total_reused = total_read = 0
    for name, d in (("A", docA), ("B", docB)):
        turns[name] = [{"role": "user", "content": d + "\n\nSummarise the text above in one sentence."}]
    for step in range(2):
        for name, d in (("A", docA), ("B", docB)):
            if step == 1:      # the conversation's next turn: it continues what its own answer left
                turns[name].append({"role": "user",
                                    "content": "Now list three keywords from the text above, comma separated."})
            answer, reasoning, wall, fig = ask(turns[name])
            label = f"{name}{step + 1}"
            if fig:
                prompt, reused, read, ms = fig
                total_reused += reused
                total_read += read
                print(f"{label:<8} {prompt:>7} {reused:>7} {read:>7} {ms:>7} {wall:>7.1f}")
            else:
                print(f"{label:<8} {'?':>7} {'?':>7} {'?':>7} {'?':>7} {wall:>7.1f}   (no log line)")
            if step == 0:
                turns[name].append({"role": "assistant", "content": answer,
                                    "reasoning_content": reasoning})
    print(f"\ntotal: {total_reused} tokens reused, {total_read} read "
          f"({100.0 * total_reused / max(1, total_reused + total_read):.0f}% reuse)")


if __name__ == "__main__":
    main()
