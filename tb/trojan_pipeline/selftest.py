#!/usr/bin/env python3
"""Self-test for the structural net-tap mechanism (no AI, no network).

Splices the transparent pass-through tap (net_tap_passthrough.sv) onto the MISO
net of the recovered core and checks that (a) Yosys accepts the patched netlist
and (b) normal encrypt/decrypt still pass. This proves the structural insertion
is correct and behaviour-preserving; the AI pipeline supplies a non-transparent
tap through the exact same path.

Run: python3 tb/trojan_pipeline/selftest.py
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # tb/
from trojan_pipeline import analyze_net, splice_net_tap, validate, find_tool, find_cells  # noqa: E402


def repo_root():
    for p in HERE.parents:
        if (p / "recovery/recovered_sim.v").is_file():
            return p
    raise FileNotFoundError("repository root not found")


def main():
    root = repo_root()
    build = root / "build/trojan_pipeline"
    build.mkdir(parents=True, exist_ok=True)
    tap = HERE / "net_tap_passthrough.sv"
    tbdir = root / "tb/trojan_verification"
    cells = find_cells()

    # 1. analyse the target net with Yosys, then splice structurally.
    facts = analyze_net(root / "recovery/recovered_sim.v", "pin_31")
    print("analyze pin_31:", facts)
    src = (root / "recovery/recovered_sim.v").read_text()
    patched, report = splice_net_tap(src, "pin_31", "net_tap",
                                     {"sck": "pin_36", "mosi": "pin_32",
                                      "cs_n": "pin_28", "rst_n": "pin_34"})
    print("splice report:", report)
    patched_path = build / "recovered_pin31_tapped.v"
    patched_path.write_text(patched)

    # 2. re-parse the patched netlist with Yosys.
    ok, log = validate(patched_path, tap, cells)
    if not ok:
        print("YOSYS VALIDATION FAILED\n" + log[-800:])
        return 1
    print("yosys validate: OK")

    # 3. compile + simulate: the transparent tap must preserve encrypt/decrypt.
    vvp_out = build / "selftest.vvp"
    comp = subprocess.run([find_tool("iverilog"), "-g2012", "-DNO_ICE40_DEFAULT_ASSIGNMENTS",
                           "-s", "baseline_tb", "-I", str(tbdir), "-o", str(vvp_out),
                           str(tbdir / "crypto_dut.sv"), str(patched_path), str(tap),
                           cells, str(tbdir / "baseline_tb.sv")],
                          capture_output=True, text=True, cwd=build)
    if comp.returncode:
        print("COMPILE FAILED\n" + (comp.stdout + comp.stderr)[-800:])
        return 1
    sim = subprocess.run([find_tool("vvp"), str(vvp_out)], capture_output=True, text=True, cwd=build)
    text = sim.stdout + sim.stderr
    passed = "BASELINE SUMMARY PASS" in text and "FAIL" not in text
    for line in text.splitlines():
        if any(k in line for k in ("SUMMARY", "enc.result", "dec.result", "FAIL")):
            print("   " + line)
    print("\nSELFTEST:", "PASS - structural net tap is correct and transparent" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
