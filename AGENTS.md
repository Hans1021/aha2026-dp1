# Project

CSAW 2026 AI Hardware Attack Challenge.

## Objective

Recover and understand the cryptographic FPGA circuit, then use AI to generate a hardware Trojan and demonstrate its operation in simulation.

## Rules

- All Trojan RTL and exploit hardware must be AI-generated.
- Preserve complete AI prompts and responses.
- Do not modify the original recovered RTL in place.
- Use separate files under rtl/ for experimental designs.
- Verify normal and triggered operation.

## Simulation

Run the original recovered design with:

make sim
