# AI-aided reusable SPI testbench workflow

Open [the notebook](../tb/LLM_Aided_SPI_Testbench_Generation.ipynb)
and run its cells in order. Its Markdown cells contain headings only; explanations
appear in code comments, model-response output, and saved artifacts.

## Setup and generation modes

Install the notebook dependencies in your preferred Python environment:

```bash
python -m pip install -r requirements-notebook.txt
jupyter lab tb/LLM_Aided_SPI_Testbench_Generation.ipynb
```

The workflow discovers the installed OSS CAD Suite at
`~/.local/opt/oss-cad-suite`. Set `OSS_CAD_SUITE` to another suite location if
needed. `ICE40_CELLS_SIM` can override the location of Yosys's actual iCE40 cell
models. The recovered DUT is always `recovery/recovered_sim.v`; its bytes are
checked before and after simulation.

The notebook has two explicit modes:

- **`replay` (default):** load the recorded Codex-generated response in
  `ai_logs/bao/bootstrap/response.json`, materialize its source, and run it against
  the real recovered DUT. This needs no API key and is not a simulated API call.
  The rendered prompt must match the recorded request; source/prompt edits require
  a fresh generation rather than silently reusing stale output.
- **`live`:** set `AHA_GENERATION_MODE=live` and `PORTKEY_API_KEY` in the kernel's
  environment. The workflow sends requests to the same NYU Portkey gateway used
  by the lab. `PORTKEY_BASE_URL` and `PORTKEY_MODEL` override the gateway/model.
  The retained lab default is `@vertexai/anthropic.claude-opus-5-5`; availability
  depends on the gateway account. This change does not verify a live gateway call
  because no key was configured in the development session.

The live path follows the lab's `LLMClient` interface: `generate()` sends messages
through `Portkey` from `portkey_ai`, using `client.chat.completions.create()`, and
returns the response text. `is_available()` checks whether a key is configured;
it does not test gateway access. The notebook reuses this client across generation
and repair attempts. Response JSON is validated locally before use.

If the gateway explicitly rejects the `temperature` parameter, the client retries
once without it and omits it on later requests. Other errors propagate without
this retry. SDK automatic retries are disabled so every client attempt is logged.
The dependency is pinned to the validated `portkey-ai` version 2.3.4.

Each notebook run creates fresh `results/simulation/<run-id>/attempt-NN/`
directories. Compiler commands, source files, scheduling explanations, compiler
and simulator output, result JSON, and `waveforms.vcd` remain available per
attempt. Generated build/waveform directories are ignored by Git. Open the VCD
path printed by the notebook in GTKWave.

## Prompt and response logging

The notebook's `ask_ai()` uses the shared logging wrapper and `LLMClient` in
[`scripts/testbench_workflow.py`](../scripts/testbench_workflow.py). Every attempt
appends its full system/user messages **before** requesting a response, then
appends the complete SDK-serialized response **before** extracting or validating
the generated artifacts. HTTP errors retain their response body. Requests that
fail, invalid JSON, repair attempts, and any retry without `temperature` remain in
the same transcript:

```text
ai_logs/bao/
├── bootstrap/
│   ├── initial_request.json
│   ├── initial_response.json
│   ├── request.json
│   └── response.json
└── runs/<run-id>/events.jsonl
```

Run IDs prevent overwriting earlier logs. Events record timestamps, source
fingerprints, request settings, validation results, and simulation observations.
Credentials and authorization headers are excluded; configured credential values
are redacted if an error or response echoes them. Logs are intentionally not
ignored by Git so useful experiment transcripts can be committed.

The bootstrap files record actual generation requests and structured response
artifacts from the Codex session that created this workflow. They are not Portkey
API transcripts or a complete export of the surrounding chat. The initial request
contained a proposed CS/mode mapping; real simulation corrected it. The revised
request uses the validated mapping, and the original request/response are retained.
Future notebook calls automatically capture their complete API messages and
serialized response or error outputs through the logger.

Live validation gets at most three generation attempts. Each failed attempt feeds
bounded compiler/simulator diagnostics and the previous candidate back to the
model. The loop never changes the recovered DUT or supplied expected values.
Replay has one attempt and never falls back to a live request. Each SDK call has a
120-second timeout, including the possible single retry without `temperature`.
Compile/simulate subprocesses each have a 30-second wall-clock bound, and the
generated testbench has a 100 ms simulation watchdog.

## Reusable operations and scheduling

The checked-in AI-generated files are:

- [`tb/spi_operations.svh`](../tb/spi_operations.svh): interface initialization,
  synchronous reset, 32-bit transfers, START, fixed-count or bounded completion,
  explicit full-duplex readout, and X/Z-safe checking tasks.
- [`tb/crypto_dut.sv`](../tb/crypto_dut.sv): named interface wrapper around the
  unchanged recovered design.
- [`tb/crypto_tb.sv`](../tb/crypto_tb.sv): exact application replay followed by
  optional seeded SPI echo trials.

Only `sck_pulse` changes SCK after time-zero initialization. All stimulus is
sequential. Controls and MOSI change while SCK is low, then remain stable for
10 microseconds before the rising edge. The task samples MISO/BUSY one nanosecond
after that edge, allowing the recovered registered MISO and nonblocking state
updates to settle. The remaining high phase is 9,999 ns, preserving a 20 us period.
The task returns with SCK low; no free-running clock or interrupt process competes
with it. Readout clocks still transmit the specified MOSI word.

The completed checks validate this pin mapping:

| Signal | Recovered port |
| --- | --- |
| SCK | `pin_36` |
| RST_N | `pin_34` |
| MOSI | `pin_32` |
| MISO | `pin_31` |
| NORM_CS_N | `pin_28` |
| START | `pin_27` |
| ENC_DEC | `pin_25` |
| BUSY | `pin_38` |
| ICE_LED | `pin_23` |

This mapping is supported by recovered connectivity and the passing supplied
transactions; the RP2040 GPIO numbers in the application are a different numbering
scheme. No standard AES behavior or key-loading protocol is inferred.

## Measured validation

With seed 4313 and eight extra SPI echo trials, the real recovered-core simulation
passes 24 named checks over 708 SCK pulses. The application portion remains exactly
180 pulses. Independent VCD inspection counts the actual rising edges and checks
them against the simulator's summary. Random trials use an explicit 32-bit LCG;
the same seed produces the same transmitted words. This fixes stimulus
reproducibility, not the output of future live model requests.

Both encryption and decryption observe BUSY high at `e0`, `e1`, and `e2`, then
low at `e3` through `e7`. Here `e0` is the START rising edge. The replay retains all
seven following pulses and passes the supplied plaintext/ciphertext comparisons.
The exact internal result-load edge and earliest valid readout have not yet been
measured. The earlier transaction specification's pending-measurement note describes
its original snapshot; these are the subsequent measurements from this workflow.

Run the regression suite from the repository root:

```bash
python -m unittest discover -s tests -v
```

The tests exercise actual recovered-core transactions and seeded echoes, then
check failure paths for incorrect expected ciphertext, unknown BUSY/MISO/data,
matching unknown expectations, stuck-BUSY timeout, missing waveforms, and invalid
summaries. Gateway tests use SDK fixtures to verify logging, parameter retries,
and errors without claiming successful live generation. No generated Python
oracle is executed, and no coverage percentage is claimed.

## Lab and paper references

This adapts the bounded generation, measured-feedback, and artifact-preservation
workflow from the supplied lab notebook
`Bao_Nguyen-bln7876-Enhanced_LLM_Aided_Testbench_Generation.ipynb`, the lab report
`Bao_Nguyen-CoverageGuided_TestbenchGen_report.pdf` (pages 1–2 and 4–6), and
`LLM_Aided_Testbench_Generation_and_Bug_Detection_for_FSM.pdf` (workflow figures and
sections III–IV). Their example instructions and DUT assumptions were treated as
reference material. In particular, the lab's combinational scheduling and generated
oracle approach were not used for this recovered sequential core.
