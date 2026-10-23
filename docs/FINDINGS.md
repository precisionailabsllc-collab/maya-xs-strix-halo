# What was measured, what was rejected, and why

Two days of work on one Ryzen AI Max+ 395 (gfx1151, 125 GB). Everything below is a measurement, not a claim from a
release note. Numbers are decode/prose or prefill on an 8K-token unique document unless stated, on a quiet box
(`scripts/bench_decode.py` and `scripts/measure.py` refuse to run above load 2.0).

## The big wins

| change | effect |
|---|---|
| Project Maya v1.1.19 → **v1.1.21** (upstream: matrix cores for the prompt path) | prefill 92.8 → 232 tok/s |
| `MAYA_DS4_PROMPT_SKIP=2` (DeepSeek's own layer skip) | prefill +24–34% |
| PR #297 (`DS4_SKIP_UNPICKED`): skip copying experts that route to zero rows | +8–18% prefill **when experts stream from SSD** |
| **Maya-XS** (IQ1_S, 115.4 GB resident) instead of Maya-S (178.2 GB, 48% resident) | prefill 350 → 455, decode 14.2 → 18.4–19.3 |
| prefix reuse + kept conversations + `KEEP_THINKING` | turn 2 of a 35K conversation: 87 s → **0.8 s** |

## Rejected, with the number that rejected it

- **EAGLE-4's tree verification** — the model is not the difference, the verification structure is: their own
  benchmark beats DSpark/DFlash2 by 1.63–1.85x. Not portable here without kernel work (no mask or per-row
  positions in `forward()`); filed upstream as mw00/project-maya#402 with a patch sketch. Our measured headroom for a
  width-2 tree at depth 1 is **+6%** (chain = 4.34 tokens/round, 88% first-token acceptance over 1,384 rounds).
- **EAGLE-4/EAGLE-3 drafters, Woodcock, DFlash, MTP** — no DeepSeek head exists for any of them (Woodcock ships
  Qwen3-1.7B/4B only; the `-mtp` repos are MLX builds of whole models). Speculators cannot be borrowed across
  models: they consume the target's own hidden states.
- **DwarfStar (antirez/ds4)** — supports V4.1-Flash on Metal and CUDA only, and its own README says its V4.1 weights
  and drafter are not interchangeable, i.e. no speculative decoding: ~4x fewer tokens per round than our chain.
- **DeepSeek's DeepInfer** — SM100 / Ascend 950 only.
- **v1.1.22's headline drafting gain (+1.85x)** — verify-*compute* speedup; our decode is bandwidth-bound, measured
  13.56 vs 14.22 tok/s (no change).
- **The exact (MXFP4) DSpark drafter** — 19.05 tok/s at 82% acceptance versus 18.72 at 85% for the IQ1_S one, for
  4.6 GB more memory and 7 points less residency.
- **Knob sweeps** (each its own restart): router one-token-at-a-time −7% prefill, pool 104 GB −9% at 16K,
  `MAYA_DS4_STREAM=0` −4% decode, Engram row cache at 1 GB and 2 GB — indistinguishable from the 256 MB default,
  CPU-lane `smt` and `DS4_READERS=32` inside ±2%.
- **EXL3 for DeepSeek** — the packs are 211 GB of weights plus 203 GB of Engram and want ~230–280 GB of VRAM+RAM
  against 125 GB; a community report measures EXL3 prefill ~100 tok/s against ~500 for the GGUF path.

## Quality: what the faster quant costs

8-case panel, 512 held-out positions (24-bit-exact reference logits):

| | mean NLL | top-1 |
|---|---:|---:|
| full-precision reference (frozen) | 0.349 | — |
| **Maya-S** (IQ2_XXS/IQ3_XXS) | **0.374** | **94.1%** |
| Maya-XS (IQ1_S) | 0.448 | 86.5% |

On nine tasks with mechanically checkable answers — two arithmetic traps, three code tasks executed against real
tests, document recall, a synthesis question whose correct answer is to refuse to invent a rate, and a needle in
4K tokens of filler — **both quants scored 9/9 at default settings**. The loss is in token-level likelihood, not on
tasks of that difficulty. It presumably shows on longer-horizon work; that was not measured.

## Measurement hygiene (the most reusable lesson)

- **A busy box fakes a regression.** A run at load ~10 read 297 tok/s where the same config read 356 on a quiet box.
  Both tools now refuse above load 2.0 and wait for it instead of failing mid-run.
- **The 5-minute load average carries your own previous run.** Requiring it to settle is why a strict window (three
  consecutive clean samples) is needed rather than one instantaneous check.
- **A 16K probe straight after a restart reads 128–172 tok/s** instead of ~300: page cache cold. Take passes 2–3.
- **A benchmark is an instrument; three of my own graders were wrong** — one demanded a fact the question never
  asked, one scored a correct answer as a failure because `max_tokens` cut the thinking block, one tripped its own
  load guard. Fix the instrument before believing a difference.
