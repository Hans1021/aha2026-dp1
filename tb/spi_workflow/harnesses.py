"""copy maintained scoreboards and probes into each candidate source bundle."""

# keep local harness provenance separate from model-returned artifacts.
from pathlib import Path
import shutil

from .common import file_hash


HARNESS_FILES = ("tb_driver.sv", "baseline_timing_tb.sv", "spi_harness_controller.sv")


# copy exact maintained sources without replacing existing candidate evidence.
def copy_fixed_harnesses(root, output_dir):
    root, output_dir = Path(root), Path(output_dir)
    sources = {name: root / "tb/harnesses" / name for name in HARNESS_FILES}
    for source in sources.values():
        if not source.is_file():
            raise FileNotFoundError(f"Missing maintained harness: {source}")
    for name in sources:
        if (output_dir / name).exists():
            raise FileExistsError(f"Harness destination already exists: {output_dir / name}")
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, source in sources.items():
        destination = output_dir / name
        shutil.copyfile(source, destination)
        if file_hash(source) != file_hash(destination):
            raise RuntimeError(f"Harness copy differs from source: {name}")
        paths[name] = destination
    return paths
