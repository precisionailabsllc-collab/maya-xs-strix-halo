# SETUP — from a bare Ryzen AI Max to a served DeepSeek-V4.1-Flash Maya-XS

Target: one 128 GB Ryzen AI Max (Strix Halo, `gfx1151`), Linux, ROCm. Result: an OpenAI-compatible
endpoint serving the 552B model at ~455 tok/s prefill (8K) and ~18–19 tok/s decode, with 91% of experts in
GPU memory.

Times assume a warm page cache and a ~500 Mbit/s link. Budget 2–4 hours, most of it download.

---

## 0. What you need

| | |
|---|---|
| RAM | **128 GB** (the model needs 115.4 GB resident; Maya-XS is sized for exactly this) |
| Disk | ~450 GB free (276 GB for Maya-XS shards + 4.3 GB drafter + build tree) |
| Stack | ROCm 7.x with `hipcc`, CMake ≥ 3.28, Ninja, a C++20 compiler |
| Kernel | any recent Linux with the `amdgpu` driver exposing `gfx1151` |

Confirm the GPU is visible before starting:

```sh
rocminfo | grep -m1 gfx        # expect gfx1151
cat /sys/class/drm/card0/device/mem_info_gtt_total
```

## 1. Build the engine (upstream v1.1.24 + the patches here)

```sh
git clone https://github.com/mw00/project-maya.git ~/maya
cd ~/maya && git checkout v1.1.24
git am /path/to/patches/*.patch          # 9 patches: prefix reuse, pool sizing, run size, keep-thinking, ...
cmake -G Ninja -B build-hip -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_HIP_ARCHITECTURES=gfx1151 -DSTRATA_ENABLE_HIP=ON -DSTRATA_PREFILL_MMQ=ON
cmake --build build-hip -j8 --target maya-ds4
python3 -m venv ~/maya/.venv                     # the server's own dependencies
~/maya/.venv/bin/pip install -r requirements.txt 2>/dev/null || true
```

The unit expects the clone at `~/maya` and the server's Python at `~/maya/.venv/bin/python`; adjust the unit if
you keep it elsewhere.

Two notes from doing this on the reference machine:

- Build the **`maya-ds4` target explicitly**. On v1.1.22 a plain `ninja` failed on three *test* targets because
  the new `ds4_lt` object was not linked into them (fixed upstream in v1.1.22+; `maya-ds4` itself always built).
- If HIP is missing pieces, `docs/` of the upstream repo is the reference; nothing here depends on a fork.

## 2. Get the model files

The weights are **not** in this repository. From Hugging Face (`peasantsmith/DeepSeek-V4.1-Flash-1017-Maya-GGUF`):

```sh
hf download peasantsmith/DeepSeek-V4.1-Flash-1017-Maya-GGUF \
   --include "Maya-XS/*" --include "dspark/dspark-xs.bin" --include "dspark/dspark-xs.json" \
   --local-dir ~/models/DeepSeek-V4.1-Flash-1017-Maya-GGUF
```

That is 276.6 GB of shards (the model in files 1–3, the Engram tables in 4–5) plus a 3.40 GB drafter. The
downloader fetches the six files in parallel; on a gigabit link it takes ~35 minutes.

## 3. Build the pack manifest

The engine reads a small "pack" beside the weights (geometry, a tensor index, the tokenizer, an expert profile):

```sh
cd ~/maya
python tools/ds4/pack_gguf.py \
  --model   ~/models/DeepSeek-V4.1-Flash-1017-Maya-GGUF/Maya-XS \
  --drafter ~/models/DeepSeek-V4.1-Flash-1017-Maya-GGUF/dspark/dspark-xs.json
```

`--drafter` matters: the default would pick up `dspark.json` from the same folder, which is the *larger*
Maya-S drafter. The 3.40 GB IQ1_S one measured the same speed for 0.85 GB less; the exact MXFP4 drafter (7.97 GB)
measured 19.05 tok/s at 82% acceptance versus 18.72 at 85% — a wash, for 4.6 GB more memory.

## 4. Size the pool for *your* memory (the one knob that decides everything)

On an APU the GPU pool and the RAM tier are the same physical memory, so **the pool size is a headroom decision**,
not a residency decision. Measured on the reference machine:

| `DS4_POOL_GB` | experts in VRAM | free RAM | outcome |
|---:|---|---:|---|
| 110 | 15,713 (99.8%) | 0 GB | loads, then **cannot finish a request** |
| 106 | 15,095 (96%) | 1 GB | works, starved (decode 18.06) |
| **100** | **14,349 (91%)** | **5 GB** | **fastest: decode 19.30, prefill 455** |

Start from *total RAM minus 115.4 GB minus 5 GB*, then verify.

## 5. Configure and run

`config/ds4-config.maya-xs.json` is the measured configuration. Copy it, fix the paths, and either run the server
by hand or install the unit:

```sh
cp config/ds4-config.maya-xs.json ~/maya/ds4-config.json    # then edit the paths inside it
cd scripts && ./install.sh                                  # rewrites {{HOME}} in scripts + unit + configs
cp ../config/maya-ds4.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user start maya-ds4
curl -s localhost:17866/health
```

The settings that carry the performance (all explained in the config's env block):

```
DS4_POOL_GB=100            # see §4 - the value for a 125 GB box
DS4_PREFIX_STRIDE=8192     # run size; 4096 only if experts stream from SSD (i.e. Maya-S)
DS4_PREFIX_REUSE=1         # keep checkpoint marks so a growing conversation is not re-read
MAYA_DS4_PROMPT_SKIP=2     # DeepSeek's own layer skip: +24-34% prefill
MAYA_DS4_SLOT_KEEP=1       # keep conversations aside: turn 2 of a 35K chat costs 0.8 s
MAYA_DS4_Q8_BATCH=1        # bit-exact, free
--prefill-chunk 8192
```

**If you keep the `KEEP_THINKING` patch:** your client must send `reasoning_content` back with the assistant turn,
or the rendered prompt stops matching the generated tokens and reuse silently collapses to zero. To check: watch
the engine log's `prompt N tokens = X reused + Y read` line — `X` should be large on a conversation's second turn.

## 6. Verify (same probes the numbers came from)

```sh
cd scripts && ./install.sh          # rewrites {{HOME}} in the scripts to your $HOME
python3 measure.py --only prefill --big --repeat 2
python3 bench_decode.py --prompt prose --reps 8
python3 longctx.py 34000            # a 35K-token conversation: prefill, then a reused second turn
```

Both tools **refuse to measure on a busy box** (1-minute load above 2.0) — that guard exists because a run that
starts at the threshold reads 10–20% low and that mistake cost this project a day of wrong numbers. Expect, on a
quiet 128 GB Strix Halo: prefill 8K ≈ 455 tok/s, 16K ≈ 428, decode prose ≈ 18.4–19.3.

## 7. Common failures

| symptom | cause |
|---|---|
| engine loads 99% of experts then hangs | pool too large — free 5 GB, see §4 |
| `ds4 gguf mv: invalid argument` past 32K tokens | engine older than v1.1.23; this matters on AMD |
| every turn's `reused` is 0 | client is not echoing `reasoning_content` (§5) |
| decode ~4 tok/s, CPU lane busy | experts streaming from SSD: pool too small, or a Maya-S-size model on a 128 GB box |
| `ModuleNotFoundError: numpy` in the quality panel | run `score_qa.py` with the project venv's python |
