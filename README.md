# DeepSeek-V4.1-Flash Maya-XS on a Ryzen AI Max (Strix Halo, gfx1151)

A working, measured configuration for running **DeepSeek-V4.1-Flash-1017 (552B MoE)** locally on a single
128 GB Ryzen AI Max APU, on top of [Project Maya](https://github.com/mw00/project-maya) (MIT) — plus the patches,
measurement tools and findings that got it there.

Everything here was measured on one machine. Where a setting is a rule rather than a copy-paste value, the
reason is written down, because the right value differs per box.

## The machine

| | |
|---|---|
| CPU/GPU | AMD Ryzen AI Max+ 395, Radeon 8060S iGPU, `gfx1151` (RDNA 3.5), 125 GB usable RAM |
| OS / stack | Linux (kernel 6.18), ROCm 7.14, CMake 3.31, Ninja |
| Engine | Project Maya `v1.1.24` + the patches in `patches/` |
| Model | **Maya-XS** — IQ1_S experts (115.4 GB model in memory), Q6_K Engram tables (161 GB, read from SSD) |
| Drafting | DSpark, IQ1_S requant (3.40 GB) |

## Measured results

Same box, same harness, clean-box guarded (see `scripts/bench_decode.py`):

| probe | Maya-S (IQ2_XXS/IQ3_XXS) | **Maya-XS (IQ1_S)** |
|---|---:|---:|
| prefill, 8K-token unique document | ~350 tok/s | **~455 tok/s** |
| prefill, 16K-token unique document | ~295 tok/s | **~428 tok/s** |
| decode, prose (200 tokens, warm) | 14.2 tok/s | **18.4–19.3 tok/s** |
| decode, 35K-token conversation | — | 20.3–20.9 tok/s |
| experts resident in GPU memory | 7,644 of 15,744 (48%) | **14,349 of 15,744 (91%)** |
| second turn of a 35K conversation | full re-read | **reused 35,520 tokens in 819 ms** |
| quality, mean NLL over 512 held-out token positions | **0.374** | 0.448 |
| quality, top-1 agreement | **94.1%** | 86.5% |

Read that last pair carefully: **Maya-XS is faster and measurably dumber** (1.6 bits versus ~2.3). On nine
one-shot tasks with mechanically checkable answers (math, code executed against tests, document recall, a needle
in 4K tokens of filler) both quants scored 9/9 — the trade shows up in token-level likelihood, not on tasks of
that difficulty. `docs/FINDINGS.md` has the details.

## The rules that matter more than the values

1. **Headroom beats residency on an APU.** The GPU pool and the RAM tier are the same physical memory. A pool of
   100 GB with ~5 GB left beat 106 GB and 110 GB; at 110 GB the engine loads 99.8% of experts and then cannot
   finish a request. Start at "experts that fit minus 5 GB free" and verify `/health` plus a real prompt.
2. **Verification is nearly free; drafting is not.** Decode here is memory-bandwidth-bound, so extra verified rows
   ride the same weight reads — bigger spec windows cost nothing measurable, and the drafting knobs are already at
   their optimum (all six settings tested fell inside 1%).
3. **A conversation only gets reused if the client echoes `reasoning_content`.** DeepSeek's reference encoder drops
   past reasoning by default, which rewrites the history every turn and silently defeats prefix reuse and kept
   conversations: a 4,215-token follow-up then re-reads all of it (19.3 s instead of 2.7 s). The `KEEP_THINKING`
   patch in `patches/` keeps it; without it, multi-turn speed is ~7x worse than it should be.
4. **Prefill's run size follows what is resident.** With experts streaming from SSD, cap runs at ~4096 tokens; with
   91% resident, 8192 is **+16%** and the cap is only protecting nothing.
5. **Long context: use v1.1.23 or newer.** v1.1.22 stops on AMD once a conversation passes 32,768 tokens
   (`ds4 gguf mv: invalid argument`). Verified here with a 35,520-token conversation.

## Contents

```
patches/    9 commits on top of upstream v1.1.24 (git format-patch)
config/     ds4-config for Maya-XS and Maya-S, and the systemd user unit
scripts/    the measurement harness (see docs/TUNING.md for what each one is for)
docs/       SETUP.md (do this), FINDINGS.md (what was measured and what was rejected)
```

## Quick start

`docs/SETUP.md` walks it end to end: build the engine with the patches, download the packs, build the pack
manifest, size the pool for your RAM, start it, and verify with the same probes used above.

## Credit

The engine, the quants and the whole idea are [Project Maya](https://github.com/mw00/project-maya)'s
(Peasant Smith and contributors, MIT); the model weights are `peasantsmith`'s on Hugging Face and are **not**
redistributed here. This repository contributes a measured configuration, a small patch set (prompt-prefix reuse,
pool sizing for unified-memory APUs, a run-size fix, a keep-thinking switch for the reference encoder) and the
harness used to compare them. MIT, see `LICENSE`.
