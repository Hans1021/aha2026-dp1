# Code comments

- Use lowercase prose; preserve names, acronyms, identifiers, and literal values.
- Keep comments concise while retaining necessary behavior and timing details.
- Prefer standalone comment blocks before each logical block, including modules,
  functions, control flow, sequential processes, and groups of related operations.
- Use brief inline comments for local details such as modes and cell coordinates.
- Explain relevant functions, clock/reset behavior, and interrupts when present.
- Preserve executable behavior when changing comments in supplied or generated code.

# Simulation notebook

Keep the workflow notebook at `tb/llm_aided_spi_tb_generation.ipynb`.
Store AI logs under `ai_logs/<source-directory-name>/`; the `tb/` notebook uses
`ai_logs/tb/`.

Keep mock peripheral validation in the `tb_driver` target. Label its results as
driver tests; they do not validate the recovered cryptographic circuit.

# Frozen recovered baseline

`baseline-recovered-v1` identifies the accepted baseline commit. Keep that tag,
`baselines/recovered-v1/`, and every input pinned by its manifest unchanged.
Use separate sources for experiments; reproduce the baseline from its tagged
checkout with `make baseline`. Never overwrite reference evidence to make a
failing comparison pass. An intentional new baseline requires a new version.
