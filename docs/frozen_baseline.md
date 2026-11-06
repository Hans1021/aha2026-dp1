# Frozen recovered baseline

The immutable reference is the Git tag `baseline-recovered-v1`. Its
[`manifest.json`](../baselines/recovered-v1/manifest.json) pins source bytes,
simulator identities, the iCE40 model, and compact acceptance evidence. The tag
identifies the commit containing those files; the manifest avoids a self-referential
commit hash. This reference covers the supplied examples and observed interface
behavior. It does not establish standard AES, a key-loading protocol, or complete
cryptographic correctness.

## Reproduce the baseline

From this tagged checkout, run:

```bash
make baseline
```

This verifies the manifest, runs the unchanged AI-generated `tb/crypto_tb.sv`
with seed 4313 and zero extra random trials, then runs the separate real-DUT
timing probe. Each execution writes fresh commands, logs, results, and VCD files
under `results/simulation/baseline/`. A failure returns a nonzero exit and an
explicit `BASELINE FAIL`; success prints `BASELINE PASS`.

If later development changes pinned files, reproduce from a detached worktree:

```bash
git fetch origin --tags
git worktree add --detach ../aha2026-baseline baseline-recovered-v1
cd ../aha2026-baseline
make baseline
```

Use the recorded OSS CAD Suite version and model bytes. `OSS_CAD_SUITE` selects
the installed suite, and `ICE40_CELLS_SIM` can select the recorded library at a
different location. Paths may differ; pinned hashes and version identities must
match. The manifest records tool launchers/binaries, the compiler/preprocessor,
the VVP backend/runtime module, and the cell model; it is not a complete hermetic
operating-system image.

Never move the reference tag, edit its manifest/evidence, or overwrite pinned
inputs to accommodate a new result. Use separate experimental files or a new
explicitly accepted baseline version. The capture command refuses an existing
reference directory, and ordinary verification never rewrites reference evidence.

## Build integration

[`tb/recovered.f`](../tb/recovered.f) maintains the wrapper, AI-generated
testbench, and lowered recovered DUT. `spi_operations.svh` is included through
the testbench include directory. The runner expands this list, preserving a
generated candidate's testbench directory when used by the notebook.

The actual compile command uses `-g2012 -s crypto_tb`, both required include
directories, and the listed source files. The lowered DUT instantiates exactly
four `SB_RAM40_4K` cells. Its only additional simulation model is the installed
`share/yosys/ice40/cells_sim.v`, compiled with
`-DNO_ICE40_DEFAULT_ASSIGNMENTS`. The timing probe uses the same build helper
with top `baseline_timing_tb`. No mock, stub RAM, or ignored-module option is
used. Both accepted compilations exit zero with empty compiler diagnostics.

This follows [YosysHQ's iCE40 simulation build](https://github.com/YosysHQ/picorv32/blob/main/picosoc/Makefile).
[Icarus's documented flags](https://steveicarus.github.io/iverilog/usage/command_line_flags.html)
define the SystemVerilog language level and explicit root selection. Regression
tests also show that a missing library or unsupported primitive fails acceptance.

## Acceptance evidence

| Requirement | Recorded result |
| --- | --- |
| SPI readback | `59c359c3` transmitted and read back |
| Encryption example | `59c359c3` → `9cd84392` |
| Decryption example | `9cd84392` → `59c359c3` |
| Reset understood | unclocked assertion preserves serial state; clocked reset clears it; MISO has one-edge lag |
| BUSY and output timing | accepted BUSY after START releases; completion at e3; earliest passing tested read start after e4 |
| Known checked outputs | every checked MISO bit, word, and BUSY sample rejects X/Z |
| Explicit termination | application: 8 checks/180 pulses; timing: 105 checks/1,443 pulses; both PASS and zero exit |

The committed evidence contains compile commands, compiler output, simulator
output, and parsed results for both runs. Source hashes are checked again after
execution. VCD rising-edge counts independently match the summaries. The
manifest retains capture-waveform hashes; large VCD files remain in the run
directory and are regenerated during reproduction. VCD header timestamps mean
fresh waveform byte hashes are not used as behavioral equivalence checks.

## Reset and completion measurements

All state updates in the lowered circuit occur on rising SCK. Some output logic
is combinational: START asserted without a clock makes BUSY high, and releasing
START without a clock returns it low. That does not start an operation. The
probe therefore checks BUSY again after a clocked START pulse has ended and
combinational signals have settled. The application runner also requires BUSY
high on the first post-START processing edge before accepting a later low.

During an active operation, asserting reset without SCK can mask BUSY low.
Releasing reset without SCK reveals BUSY high again. A clocked reset clears the
stored state. A separate serial test loads `ffffffff`: an unclocked reset leaves
that word intact, while a clocked reset makes the following word read `00000000`.
MISO itself has no reset assignment: at the reset edge it still registers the old
shift-register bit, then becomes zero on the next edge. This explains why the
application clocks reset while asserted and clocks once after release. No initial
or unused internal unknown is used as a passing checked value.

For both supplied examples, `e0` is the clocked START edge. With CS inactive,
BUSY is high at e0–e2 and low at e3–e7. Fresh reset/load/start trials begin full
32-bit readouts after each cutoff e0 through e7:

| Read begins after | BUSY before read | Encryption read | Decryption read |
| --- | --- | --- | --- |
| e0–e2 | high | `59c359c3` (old input) | `9cd84392` (old input) |
| e3 | low | `59c359c3` (old input) | `9cd84392` (old input) |
| e4–e7 | low | `9cd84392` (expected) | `59c359c3` (expected) |

Thus e4 is the earliest tested safe **read-start boundary** for these cases.
BUSY-low at e3 is insufficient: supply another deselected SCK before starting
readout. Early read clocks themselves advance the core and alter CS framing;
these trials do not claim a passive observation of an internal result-load net.
The original application sequence remains one START pulse plus seven further
pulses and is not shortened.

## Recovery evidence and failure isolation

The prior [`ai_logs/recovery/`](../ai_logs/recovery/) transcripts identify four
initialized RAM/ROM blocks and direct aliasing of the BUSY/LED output pins. Their
source hash `b7f0d52…` matches `d654e87:recovery/recovered_pins.v`. The current
file differs in comments and whitespace; stripped logic is unchanged. The new
manifest records current exact bytes while leaving historical transcripts intact.

The recovery report calls pin 23 BUSY and pin 38 LED; the established wrapper
uses pin 38 BUSY and pin 23 LED. Since `pin_23` directly aliases `pin_38`, both
carry the same value. The remaining wrapper mapping is SCK=36, RST_N=34,
MOSI=32, MISO=31, CS=28, START=27, and mode=25. The passing application and timing
tests support this mapping without importing speculative algorithm claims.

If a future experiment fails, isolate pin mapping, reset, bit/byte ordering,
CS framing, START/processing clocks, MISO sampling/result timing, and finally
recovered logic or unsupported primitives, in that order. Keep the frozen
reference unchanged while diagnosing the experimental copy.
