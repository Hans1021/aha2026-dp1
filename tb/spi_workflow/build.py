"""build helpers for the SPI notebook workflow."""

# import only the dependencies used by this module.
from pathlib import Path
import re
from .common import RECOVERED_SOURCES
from .tools import discover_tools
from .validation import _code_only


# count vendor-cell instances without interpreting comments as circuitry.
def primitive_inventory(root):
    source = _code_only((Path(root) / "recovery/recovered_sim.v").read_text())
    source = re.sub(r'"(?:\\.|[^"\\])*"', '""', source)
    pattern = r"\b(SB_[A-Za-z0-9_]+)\s+(?=#\s*\(|(?:\\\S+|[A-Za-z_]\w*)\s*\()"
    names = re.findall(pattern, source)
    return {name: names.count(name) for name in sorted(set(names))}


# resolve only the source bundle materialized by the notebook.
def recovered_source_files(root, source_dir, testbench=None):
    root, source_dir = Path(root).resolve(), Path(source_dir).resolve()
    entries = []
    # permit comments while keeping each source entry a single repository path.
    for line in (source_dir / "recovered.f").read_text().splitlines():
        entry = line.split("//", 1)[0].strip()
        if entry:
            entries.append(entry)
    required = set(RECOVERED_SOURCES)
    # reject omissions and duplicates before attempting elaboration.
    if not required.issubset(entries) or len(entries) != len(set(entries)):
        raise ValueError("recovered.f must contain the wrapper, testbench, controllers, and recovered DUT once")
    paths = []
    # preserve candidate source selection while retaining the real recovered DUT.
    for entry in entries:
        relative = Path(entry)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("recovered.f entries must stay inside the repository")
        if entry == "tb/crypto_tb.sv" and testbench is not None:
            path = Path(testbench).resolve()
        elif relative.parts[0] == "tb":
            path = source_dir.joinpath(*relative.parts[1:])
        else:
            path = root / relative
        if not path.is_file():
            raise FileNotFoundError(f"Missing recovered simulation source: {path}")
        paths.append(path)
    return paths


# compile SystemVerilog with an explicit top and only the needed vendor library.
def recovered_compile_command(root, source_dir, executable, *, top="crypto_tb",
                              testbench=None, tools=None):
    root, source_dir = Path(root).resolve(), Path(source_dir).resolve()
    primitives = primitive_inventory(root)
    tools = tools or discover_tools(require_cells=bool(primitives))
    sources = recovered_source_files(root, source_dir, testbench)
    command = [tools["iverilog"], "-g2012", "-s", top, "-I", str(source_dir),
               "-o", str(Path(executable).resolve())]
    # disable nonstandard port defaults in the actual iCE40 model library.
    if primitives:
        cells = Path(tools["cells"])
        if not cells.is_file():
            raise FileNotFoundError(f"Missing iCE40 simulation library: {cells}")
        command.append("-DNO_ICE40_DEFAULT_ASSIGNMENTS")
        sources.append(cells)
    return command + [str(path) for path in sources]
