# The scripts

| script | what it answers |
|---|---|
| `measure.py` | prefill and decode on fixed probes (2.5K repetitive/unique, 8K/16K unique document, decode prose/code/numbers); parses the engine's own accounting; refuses a busy box |
| `bench_decode.py` | decode only, interleaved variants via request-level `strata_tune` (sampling, spec window, confidence cuts); the low-noise way to compare drafting settings |
| `task_bench.py` | task-level quality with mechanical grading: exact-answer math, code blocks executed against real tests in a subprocess, recall strings; 4 settings (greedy/sampled × default/effort 100) |
| `longctx.py` | a ~35K-token conversation: prefill speed, then a second turn, to exercise the kept-conversation path and the v1.1.23 long-context fix |
| `agent_slots.py` | two interleaved conversations (A/B/A/B) to measure kept-conversation reuse through the server |
| `learn_hot_set.py` | walks local documents/code through the engine so the expert profile's hot set covers what you actually read |
| `knob-sweep.sh` | runs a list of engine settings one restart at a time and logs the comparison |
| `measure-stacked.sh` | the unattended version: waits for a quiet box, measures, writes a report |
| `upstream-watch.sh` | polls the upstream repo/releases and the HF orgs, logs changes, drops a CHANGED flag |
| `restart.sh` | restarts the engine, proves `/health`, and waits for GTT to drain |

All of them assume the engine on `127.0.0.1:17866` and its log at `~/.local/state/maya-ds4/engine.log`.
`install.sh` rewrites the `{{HOME}}` placeholders to your own home directory.
