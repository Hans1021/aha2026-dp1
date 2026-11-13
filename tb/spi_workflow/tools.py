"""tools helpers for the SPI notebook workflow."""

# import only the dependencies used by this module.
from pathlib import Path
import os
import shutil


# discover installed tools without downloading or modifying the toolchain.
def discover_tools(require_cells=True):
    suite = Path(os.environ.get("OSS_CAD_SUITE", "~/.local/opt/oss-cad-suite")).expanduser().resolve()
    tools = {}
    # prefer the explicitly selected or locally installed suite.
    for name in ("iverilog", "vvp", "yosys"):
        path = suite / "bin" / name
        resolved = str(path) if path.is_file() else shutil.which(name)
        # show an actionable failure when a required executable is missing.
        if not resolved:
            raise FileNotFoundError(f"Install OSS CAD Suite or set PATH for {name}")
        tools[name] = resolved
    cells = Path(os.environ.get("ICE40_CELLS_SIM", str(suite / "share/yosys/ice40/cells_sim.v"))).expanduser().resolve()
    # require vendor models only when the recovered source instantiates primitives.
    if require_cells:
        if not cells.is_file():
            raise FileNotFoundError("Set ICE40_CELLS_SIM to Yosys share/yosys/ice40/cells_sim.v")
        tools["cells"] = str(cells)
    return tools


# discover mock synthesis and simulation tools without recovered models.
def discover_driver_tools():
    suite = Path(os.environ.get("OSS_CAD_SUITE", "~/.local/opt/oss-cad-suite")).expanduser().resolve()
    tools = {}
    # permit either the local suite or executables already on PATH.
    for name in ("iverilog", "vvp", "yosys"):
        path = suite / "bin" / name
        resolved = str(path) if path.is_file() else shutil.which(name)
        if not resolved:
            raise FileNotFoundError(f"Install OSS CAD Suite for {name}")
        tools[name] = resolved
    return tools
