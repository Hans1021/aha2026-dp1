#!/usr/bin/env python3
"""Content-neutral structural net-tap insertion.

Given a recovered netlist, a target output net, and a tap module (emitted by the
AI pipeline), this reroutes the net's driver through an inserted instance of the
module - a structural edit of the netlist, NOT a top-level wrapper. Yosys is used
to analyse the netlist AST (confirm the net is a 1-bit output and locate its
driver) and to validate the patched result.
"""
import glob
import os
import re
import shutil
import subprocess
from pathlib import Path


def _suite_candidates():
    cands = []
    if os.environ.get("OSS_CAD_SUITE"):
        cands.append(Path(os.environ["OSS_CAD_SUITE"]).expanduser())
    cands.append(Path("~/.local/opt/oss-cad-suite").expanduser())
    cands += [Path(p) for p in sorted(glob.glob(str(Path("~/.local/opt/oss-cad-suite*").expanduser())), reverse=True)]
    return cands


def find_tool(name):
    for c in _suite_candidates():
        exe = c / "bin" / name
        if exe.is_file():
            return str(exe)
    found = shutil.which(name)
    if not found:
        raise FileNotFoundError(f"{name} not found; install OSS CAD Suite or set OSS_CAD_SUITE")
    return found


def find_yosys():
    return find_tool("yosys")


def find_cells():
    env = os.environ.get("ICE40_CELLS_SIM")
    if env and Path(env).is_file():
        return str(Path(env).resolve())
    for c in _suite_candidates():
        p = c / "share/yosys/ice40/cells_sim.v"
        if p.is_file():
            return str(p)
    hit = sorted(glob.glob(str(Path("~/.local/opt/oss-cad-suite*/share/yosys/ice40/cells_sim.v").expanduser())), reverse=True)
    if not hit:
        raise FileNotFoundError("cells_sim.v not found; set ICE40_CELLS_SIM")
    return hit[0]


def analyze_net(netlist_path, net, top="recovered"):
    """Use Yosys to confirm `net` is a 1-bit output port of `top`. Returns the
    structural facts the splice relies on (driver kind: reg or wire)."""
    yosys = find_yosys()
    r = subprocess.run([yosys, "-q", "-p",
                        f"read_verilog -sv {netlist_path}; hierarchy -top {top}; dump o:{net}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"Yosys could not analyse {net}:\n{r.stderr[-800:]}")
    src = Path(netlist_path).read_text()
    is_output = bool(re.search(rf"\boutput\s+{re.escape(net)}\b", src))
    reg_driven = bool(re.search(rf"\breg\s+{re.escape(net)}\b", src))
    if not is_output:
        raise ValueError(f"{net} is not an output port; refusing to tap a non-output net")
    return {"net": net, "is_output": is_output, "driver": "reg" if reg_driven else "wire"}


def splice_net_tap(src, net, module_name, observe, pre_suffix="__pre"):
    """Reroute `net`'s driver through an instance of `module_name`.

    observe: {tap_input_port: existing_net} connections for the tap's observable
    inputs. The tap's `orig` input receives the original driver, `patched` output
    drives the package pin. Returns (patched_source, report).
    """
    pre = net + pre_suffix
    report = {}

    # 1. rename the net's own declaration (reg/wire) to the pre-tap net; the
    #    `output <net>;` port declaration is left intact so the pin still exists.
    patched, n_decl = re.subn(rf"\b(reg|wire)(\s+){re.escape(net)}\b", rf"\1\2{pre}", src, count=1)
    if n_decl != 1:
        raise RuntimeError(f"expected exactly one reg/wire declaration of {net}; found {n_decl}")

    # 2. rename the driver's left-hand side: '<net> <= ...' or 'assign <net> = ...'
    patched, n_nb = re.subn(rf"\b{re.escape(net)}(\s*<=)", rf"{pre}\1", patched)
    patched, n_as = re.subn(rf"(\bassign\s+){re.escape(net)}(\s*=)", rf"\1{pre}\2", patched)
    report["driver_statements_rerouted"] = n_nb + n_as
    if n_nb + n_as < 1:
        raise RuntimeError(f"no driver statement found for {net}")

    # 3. instantiate the tap before the final endmodule (structural insertion)
    conns = [f".orig({pre})"] + [f".{p}({s})" for p, s in observe.items()] + [f".patched({net})"]
    inst = (f"\n  /* AST-inserted structural net tap on {net} (not a wrapper) */\n"
            f"  {module_name} u_{module_name} (\n    " + ",\n    ".join(conns) + "\n  );\n")
    idx = patched.rstrip().rfind("endmodule")
    if idx < 0:
        raise RuntimeError("no endmodule found")
    patched = patched[:idx] + inst + patched[idx:]

    report.update(net=net, pre_net=pre, decl_renamed=n_decl)
    return patched, report


def validate(patched_path, tap_path, cells_path, top="recovered"):
    """Re-parse the patched netlist (plus the tap) with Yosys to confirm the
    structural edit produced a well-formed design."""
    yosys = find_yosys()
    r = subprocess.run([yosys, "-q", "-p",
                        f"read_verilog -sv {patched_path} {tap_path} {cells_path}; hierarchy -top {top}; check -assert"],
                       capture_output=True, text=True)
    return r.returncode == 0, (r.stdout + r.stderr)
