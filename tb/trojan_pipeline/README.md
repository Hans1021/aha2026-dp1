# AI-driven structural (AST) Trojan insertion

Instead of wrapping the recovered core in a new top module, the AI emits a single
**net-tap module** and a script splices it **structurally** into the recovered
netlist. The target net's driver is rerouted through an inserted instance — a
structural edit of module `recovered`, not a wrapper.

Pipeline (driven by [`notebooks/trojans/ast_insertion.ipynb`](../../notebooks/trojans/ast_insertion.ipynb)):

```
AI emits net_tap  →  Yosys analyses the net  →  script splices the driver
                 →  Yosys re-validates       →  Icarus compiles
```

## The net tap

For a target output net `N` (e.g. `pin_31`, MISO), the splice:
1. renames `N`'s own `reg`/`wire` declaration to `N__pre` (the `output N;` port stays);
2. reroutes `N`'s driver (`N <= …` / `assign N = …`) to drive `N__pre`;
3. inserts one `net_tap` instance whose `orig` input is `N__pre` and whose
   `patched` output drives `N`, with the observable SPI signals wired in.

So the core's original value reaches the tap as `orig`, and the tap drives the
pin. When the tap is transparent (`patched = orig`) behaviour is unchanged.

## Files

| File | Purpose |
| --- | --- |
| `insert.py` | content-neutral splice: `analyze_net` (Yosys), `splice_net_tap`, `validate` (Yosys), toolchain discovery |
| `prompts.py` | the fixed `net_tap` interface + prompt **template** (the trigger/payload is a caller-supplied `spec`, not in this code) + `extract_module` (validates the AI's module against the interface) |
| `net_tap_passthrough.sv` | transparent identity tap used to validate the mechanism (not an attack) |
| `selftest.py` | offline check: splice the pass-through tap onto MISO, Yosys-validate, then confirm encrypt/decrypt still pass |

Separation of concerns: this package is **behaviour-agnostic** — it applies
whatever `net_tap` module the AI returns. The trigger/payload logic lives only in
the AI-emitted module (`rtl/trojans/ast/net_tap.sv`) with its transcript under
`ai_logs/trojan_pipeline/`.

## Verify the mechanism (no AI, no network)

```sh
python3 tb/trojan_pipeline/selftest.py      # -> SELFTEST: PASS
```
