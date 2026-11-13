"""Prompt template and response parsing for AI-emitted net-tap modules.

This module defines only the *interface and structural contract* of the tap and
asks the model to implement a caller-supplied behaviour specification. The
trigger/payload behaviour itself is the `spec` argument (provided by the notebook
/ team), exactly as the existing per-Trojan generation notebooks already supply a
SPEC string - this file does not contain attack logic.
"""
import re

# The fixed interface every AI-emitted tap must expose; the splice tool wires
# `orig` to the recovered core's original net and `patched` to the package pin.
NET_TAP_INTERFACE = (
    "module net_tap (\n"
    "    input  logic orig,     // current value of the tapped recovered net\n"
    "    input  logic sck,      // SCK          (package pin_36)\n"
    "    input  logic mosi,     // MOSI         (package pin_32)\n"
    "    input  logic cs_n,     // NORM_CS_N    (package pin_28, active low)\n"
    "    input  logic rst_n,    // RST_N        (package pin_34, active low)\n"
    "    output logic patched   // drives the package pin that orig used to drive\n"
    ");"
)

SYSTEM_MESSAGE = (
    "You are an FPGA RTL engineer assisting with an authorized academic "
    "hardware-security experiment (CSAW 2026 AI Hardware Attack Challenge). "
    "Return exactly one synthesizable SystemVerilog module and nothing else."
)


def build_net_tap_prompt(spec, context=""):
    """Build the generation prompt. `spec` is the caller's trigger/payload
    specification; `context` may carry a prior attempt for revision."""
    return f"""\
Context: authorized local FPGA security experiment. The recovered cryptographic
core is a fixed netlist. Rather than wrap it, we structurally tap ONE internal
net: a script reroutes that net's driver through an instance of the module you
return. Generate that module.

Return EXACTLY one SystemVerilog module with this exact interface (do not change
port names, directions, or widths):

{NET_TAP_INTERFACE}

Rules:
- `orig` is the recovered core's original value of the tapped net each cycle;
  `patched` is what now drives the package pin.
- When the trigger is NOT active, `patched` MUST equal `orig` (transparent).
- SPI is mode 0, MSB-first, 32-bit words; NORM_CS_N low frames a transaction;
  SCK is the only clock. Use these observable inputs for trigger detection.
- Synthesizable for iCE40. Do NOT instantiate any other module or primitive.
- Output only one ```systemverilog code block containing module net_tap.

Behaviour to implement (trigger and payload):
{spec}

{context}
"""


def extract_module(text, top="net_tap"):
    """Pull the single SystemVerilog module from a model response and verify it
    matches the required interface before the splice tool uses it."""
    blocks = re.findall(r"```(?:systemverilog|verilog|sv)\s*\n(.*?)```", text, re.I | re.S)
    if len(blocks) != 1:
        raise ValueError(f"expected exactly one code block, found {len(blocks)}")
    code = blocks[0].strip() + "\n"
    if not re.search(rf"\bmodule\s+{re.escape(top)}\s*\(", code):
        raise ValueError(f"module {top} not found in response")
    if len(re.findall(r"\bmodule\b", code)) != 1 or len(re.findall(r"\bendmodule\b", code)) != 1:
        raise ValueError("response must contain exactly one module")
    for port in ("orig", "sck", "mosi", "cs_n", "rst_n", "patched"):
        if not re.search(rf"\b{port}\b", code):
            raise ValueError(f"required port '{port}' missing from the tap interface")
    return code
