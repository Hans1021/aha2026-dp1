# CSAW 2026 AI Hardware Attack Challenge

Team: The Bitstream Bandits
Members: Hans Yang and Bao Nguyen

## Objective

Use generative AI to create a hardware Trojan in a
cryptographic FPGA circuit recovered from a bitstream.

The Trojan must preserve normal operation and activate
a malicious payload under a specific trigger condition.

## Workflow

1. Recover structural Verilog from the FPGA bitstream.
2. Ask AI to rewrite it into readable, higher-level RTL.
3. Simulate and compare original and rewritten versions.
4. Use AI to generate and insert a hardware Trojan.
5. Simulate normal operation and triggered behavior.
6. Submit modified RTL, exploit testbench, AI logs, and documentation.

The readable RTL rewrite is optional. If equivalence cannot
be established, use a copy of the recovered RTL instead.

## Progress

### Completed

- Bitstream recovered using Project IceStorm.
- Structural and simulation-compatible Verilog generated.
- FPGA external interface mapped.
- Normal encryption/decryption examples pass simulation.
- Preliminary AI architecture analysis performed.

### Remaining

- AI-readable RTL generation.
- Behavioral equivalence testing.
- AI-generated Trojan implementation.
- Exploit testbench and simulation.
- Final documentation and submission.

## Important Files

- inputs/ice40_bitstream.bin — original bitstream
- recovery/recovered_pins.v — compact structural Verilog
- recovery/recovered_sim.v — working simulation reference
- rtl/ — future AI-generated RTL
- tb/crypto_tb.sv — normal-operation testbench
- tb/crypto_dut.sv — recovered FPGA wrapper
- tb/spi_operations.svh — reusable SPI tasks
- docs/transaction_specification.md — interface specification
- ai_logs/ — AI generation records

## Simulation

Run:

    make sim

Expected supplied example:

    Encrypt: 59C359C3 -> 9CD84392
    Decrypt: 9CD84392 -> 59C359C3

## AI Requirements

All Trojan hardware and exploit generation must be performed
using AI, with complete prompts and responses preserved.

Human-written scripts and ordinary reverse-engineering tools
may be used for supporting analysis and verification.

## Next Step

Generate a readable RTL candidate with AI and compare its
behavior against the recovered reference using Bao's testbench.
