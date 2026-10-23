#!/usr/bin/env python3
"""task_bench.py - task-level quality, which the logit panel cannot see.

`score_qa.py` drives the engine with `--ids`, bypassing the chat template: it measures sample NLL and is blind to
sampling and thinking settings. This measures what a user gets instead - answers to questions with checkable
answers - so the same model can be compared across:

    greedy          temperature 0 (what every measurement in OVERNIGHT.md used)
    sampled         temperature 1.0, top_p 1.0 (DeepSeek's recommended sampling)
    effort default  the server's thinking budget as configured
    effort high     reasoning_effort = 100 (DeepSeek's own 1..100 budget)

Grading is automatic and mechanical: a number that must appear, a code block executed against real tests, a string
that must be recalled from a passage. No model judges another model.

    ./task_bench.py                 # 5 tasks x 4 settings, strict-quiet guarded
    ./task_bench.py --tasks 3 --max-tokens 500
"""
import argparse, json, os, re, subprocess, sys, tempfile, time, urllib.request

URL = "http://127.0.0.1:17866"
MODEL = "deepseek-v4.1-flash-1017-maya-xs"

PASSAGE = (
    "The Kestrel ferry leaves Ostend dock 3 at 06:40 every weekday, and dock 7 at 07:15 on Sundays. "
    "The crossing to Brightling takes 1 hour 25 minutes. Freight crates under 40 kilograms travel free; "
    "heavier crates cost 12 euros per crate. The harbour office's tariff number is HT-4471-B, and the "
    "dockmaster is Mirela Ostrowski. Storage beyond six hours is charged by the hour."
)

TASKS = [
    dict(name="math-multistep", kind="number",
         prompt="One consolidated crate weighs 45 kg and travels with a second crate of 11 kg. Crates over 40 kg cost "
                "12 euros each; lighter crates travel free. How many euros is charged in total? "
                "Answer with the number only.",
         expect="12", note="the 45 kg crate pays 12; the 11 kg one is free (a trap: summing to 56 charges 24)"),
    dict(name="math-order", kind="number",
         prompt="Compute 18 + 6 * (7 - 3) ^ 2 - 25 / 5. Answer with the number only.",
         expect="109", note="18 + 96 - 5"),
    dict(name="code-second-largest", kind="code",
         prompt="Write a Python function `second_largest(nums)` that returns the second largest DISTINCT value in a "
                "list of integers, or None if there is no such value. Return the code in one ```python block. "
                "No explanation.",
         tests=["assert second_largest([3,1,4,1,5]) == 4",
                "assert second_largest([5,5,5]) is None",
                "assert second_largest([2,1]) == 1",
                "assert second_largest([]) is None"],
         note="distinct values, None when absent"),
    dict(name="code-fizzbuzz-13", kind="code",
         prompt="Write a Python function `fizz(n)` that returns a list of strings for 1..n: 'Fizz' when divisible by 3, "
                "'Buzz' when divisible by 5, 'FizzBuzz' when both, else the number as a string. Return the code in one "
                "```python block. No explanation.",
         tests=["assert fizz(5) == ['1','2','Fizz','4','Buzz']",
                "assert fizz(15)[-1] == 'FizzBuzz'",
                "assert fizz(3)[2] == 'Fizz'"],
         note="classic spec adherence"),
    dict(name="math-hard", kind="number",
         prompt="Crates cost 3 euros per kg. A tariff gives 15% off every crate of 20 kg or more, per crate, and a "
                "flat 30 euro handling fee is added once at the end. Two crates of 24 kg and one of 19 kg are "
                "shipped. What is the total in euros? Answer with the number only.",
         expect="209.4", note="72*0.85=61.2 twice = 122.4, + 57 = 179.4, + 30 = 209.4 (traps: discounting the 19 kg "
                             "crate, or applying the fee before the discount)"),
    dict(name="code-intervals", kind="code",
         prompt="Write a Python function `merge(intervals)` that takes a list of (start, end) tuples and returns the list "
                "with overlapping or touching intervals merged, sorted by start. Return the code in one ```python "
                "block, no explanation.",
         tests=["assert merge([(1,3),(2,6),(8,10),(9,12)]) == [(1,6),(8,12)]",
                "assert merge([(1,2),(2,3)]) == [(1,3)]",
                "assert merge([]) == []",
                "assert merge([(5,6)]) == [(5,6)]"],
         note="touching intervals must merge; the classic slip is < vs <="),
    dict(name="recall-synthesis", kind="number",
         prompt=f"Use only this passage.\n\n{PASSAGE}\n\nQuestion: two crates travel, one of 45 kg and one of 30 kg, "
                f"and both are collected after five hours. What is the total charge in euros? Answer with the number "
                f"only.",
         expect="12", note="45 kg pays 12; 30 kg is free; under six hours is free - a trap for charging per kg or "
                           "adding an hourly fee the passage never states"),
    dict(name="needle-long", kind="recall",
         prompt=("Read the log and answer the question at the end.\n\n"
                 + ("Routine line: the valve was checked and the pressure held steady at nominal range. " * 220)
                 + "\nIMPORTANT: the only crate that failed inspection carried serial ZX-9917.\n"
                 + ("Routine line: the pump cycled twice and the temperature stayed inside limits. " * 220)
                 + "\nQuestion: which crate serial failed inspection? Answer with the serial only."),
         expect=["ZX-9917"], note="a needle in ~4K tokens of filler"),
    dict(name="recall-details", kind="recall",
         prompt=f"Read this and answer from it only.\n\n{PASSAGE}\n\nQuestion: which dock does the Kestrel ferry leave "
                f"from on a Sunday, and what is the harbour office tariff number? Answer in one short line.",
         expect=["dock 7", "HT-4471-B"],
         note="needs both the Sunday dock and the tariff (the time is not asked for - the first version graded it anyway)"),
]

SETTINGS = [
    dict(name="greedy", body={"temperature": 0}),
    dict(name="sampled", body={"temperature": 1.0, "top_p": 1.0, "seed": 1234}),
    dict(name="greedy+effort100", body={"temperature": 0, "reasoning_effort": 100}),
    dict(name="sampled+effort100", body={"temperature": 1.0, "top_p": 1.0, "seed": 1234, "reasoning_effort": 100}),
]


def ask(prompt, setting, max_tokens):
    body = {"model": MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens}
    body.update(setting["body"])
    req = urllib.request.Request(URL + "/v1/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=3600) as f:
        out = json.load(f)
    m = out["choices"][0]["message"]
    content = (m.get("content") or "").strip()
    reasoning = (m.get("reasoning_content") or "")
    u = dict(out.get("usage") or {})
    u["_finish"] = out["choices"][0].get("finish_reason")
    return content, reasoning, time.time() - t0, u


def grade(task, content):
    """(passed, detail) - mechanical, no judge model."""
    low = content.lower()
    if task["kind"] == "number":
        nums = re.findall(r"-?\d+(?:\.\d+)?", content.replace(",", ""))
        return (task["expect"] in nums), f"expected {task['expect']}, saw {nums[:6]}"
    if task["kind"] == "recall":
        missing = [e for e in task["expect"] if e.lower() not in low]
        return (not missing), ("all recalled" if not missing else f"missing {missing}")
    if task["kind"] == "code":
        blocks = re.findall(r"```(?:python)?\s*(.*?)```", content, re.S)
        if not blocks:
            return False, "no python block"
        src = max(blocks, key=len)
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write(src + "\n" + "\n".join(task["tests"]) + "\nprint('OK')\n")
            path = f.name
        try:
            p = subprocess.run([sys.executable, path], capture_output=True, text=True, timeout=20)
            ok = p.returncode == 0 and "OK" in p.stdout
            return ok, ("tests pass" if ok else (p.stderr.strip().splitlines() or ["failed"])[-1][:120])
        except subprocess.TimeoutExpired:
            return False, "test timeout"
        finally:
            os.unlink(path)
    return False, "unknown kind"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", type=int, default=len(TASKS))
    ap.add_argument("--only", default="", help="run only tasks whose name contains this")
    ap.add_argument("--max-tokens", type=int, default=2600)
    ap.add_argument("--max-load", type=float, default=2.0)
    ap.add_argument("--allow-dirty", action="store_true")
    a = ap.parse_args()

    tasks = TASKS
    if a.only:
        tasks = [t for t in TASKS if a.only in t["name"]]
        if not tasks:
            print("no task matches --only", a.only); return 2
    # (the guard must WAIT, not refuse: a restart's own read-ahead lifts the load for a minute, and the
    #  per-task settle below cannot help if the process exits here first - which is how two runs died)
    l1 = float(open("/proc/loadavg").read().split()[0])
    if l1 > a.max_load and not a.allow_dirty:
        print(f"box busy (load {l1:.1f}); waiting up to 15 min for it to settle ...", flush=True)

    def wait_quiet(max_wait=600):
        """A benchmark's own answers lift the load past the guard; wait it out instead of refusing mid-run."""
        deadline = time.time() + max_wait
        while time.time() < deadline:
            l1 = float(open("/proc/loadavg").read().split()[0])
            if l1 <= a.max_load or a.allow_dirty:
                return True
            time.sleep(10)
        return False

    score = {s["name"]: 0 for s in SETTINGS}
    print(f"{'task':<22}{'setting':<20}{'pass':<6}{'out tok':>8}{'wall':>7}  detail")
    for task in tasks[:a.tasks]:
        if not wait_quiet():
            print(f"{task['name']:<22}(skipped: the box never went quiet)", flush=True)
            continue
        for s in SETTINGS:
            try:
                content, reasoning, wall, usage = ask(task["prompt"], s, a.max_tokens)
                ok, detail = grade(task, content)
            except Exception as e:                       # noqa: BLE001
                content, reasoning, wall, usage = "", "", 0.0, {}
                ok, detail = False, f"request failed: {type(e).__name__}"
            truncated = usage.get("_finish") == "length"
            score[s["name"]] += int(ok)
            toks = usage.get("completion_tokens", 0)
            verdict = ("PASS" if ok else ("TRUNC" if truncated else "fail"))   # length-cut thinking is not a quality result
            print(f"{task['name']:<22}{s['name']:<20}{verdict:<6}{toks:>8}{wall:>6.0f}s  {detail[:80]}", flush=True)
    n = min(a.tasks, len(tasks))
    print(f"\n=== accuracy over {n} tasks")
    for s in SETTINGS:
        print(f"  {s['name']:<20} {score[s['name']]}/{n}  ({100*score[s['name']]/n:.0f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
