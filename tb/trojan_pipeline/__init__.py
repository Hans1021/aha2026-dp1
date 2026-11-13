"""AI-driven structural (AST) Trojan insertion for the recovered accelerator.

Instead of hand-wrapping the recovered core in a new top module, the AI pipeline
emits a single tap module and this package splices it structurally into the
recovered netlist: the target net's driver is rerouted through an inserted
instance, validated with Yosys. The splice tooling is content-neutral - it
applies whatever tap module the AI emits; the trigger/payload behaviour lives in
the AI-generated module, not in this code.
"""
from .insert import analyze_net, splice_net_tap, validate, find_yosys, find_tool, find_cells
from .prompts import NET_TAP_INTERFACE, SYSTEM_MESSAGE, build_net_tap_prompt, extract_module

__all__ = [
    "analyze_net", "splice_net_tap", "validate", "find_yosys", "find_tool", "find_cells",
    "NET_TAP_INTERFACE", "SYSTEM_MESSAGE", "build_net_tap_prompt", "extract_module",
]
