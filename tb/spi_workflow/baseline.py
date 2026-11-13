"""baseline helpers for the SPI notebook workflow."""

# import only the dependencies used by this module.
from datetime import datetime
from datetime import timezone
from pathlib import Path
import argparse
import json
import subprocess
import sys
import tempfile
import uuid
from .common import find_repo
from .reference import REFERENCE, REFERENCE_COMMIT, REFERENCE_TAG, _environment, materialize_reference


# execute the original acceptance checks; preserve all run artifacts outside the archive.
def run_baseline(root, output_dir, *, record=False, timeout_s=30):
    if record:
        raise ValueError("Historical baseline capture is disabled; never overwrite recovered-v1")
    if not 0 < timeout_s <= 300:
        raise ValueError("Use a timeout greater than zero and no more than 300 seconds")
    root, output_dir = Path(root).resolve(), Path(output_dir).expanduser().resolve()
    if output_dir.exists():
        raise FileExistsError("Choose a fresh output directory for baseline reproduction")
    if output_dir.is_relative_to(root / REFERENCE):
        raise ValueError("Baseline outputs must not enter the frozen reference directory")
    # only temporary extracted files are removed when reproduction ends.
    with tempfile.TemporaryDirectory(prefix="aha-frozen-baseline-") as temporary:
        checkout = materialize_reference(root, Path(temporary) / "checkout")
        command = [sys.executable, "-m", "scripts.frozen_baseline", "--output-dir",
                   str(output_dir), "--timeout", str(timeout_s)]
        try:
            completed = subprocess.run(command, cwd=checkout, env=_environment(),
                                       capture_output=True, text=True, check=False,
                                       timeout=4 * timeout_s + 60)
        except subprocess.TimeoutExpired as error:
            output_dir.mkdir(parents=True, exist_ok=True)
            # subprocess timeout output may be bytes even in text mode.
            output = error.stdout or b""
            if isinstance(output, bytes):
                output = output.decode("utf-8", errors="replace")
            (output_dir / "launcher.log").write_text(output + "\nBASELINE FAIL: launcher timeout\n")
            raise RuntimeError("Historical baseline reproduction timed out") from error
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "launcher.log").write_text(completed.stdout + completed.stderr)
        if completed.returncode:
            raise RuntimeError(f"Historical baseline failed; see {output_dir / 'launcher.log'}")
        result = json.loads((output_dir / "result.json").read_text())
        if result.get("passed") is not True or result.get("reference_tag") != REFERENCE_TAG:
            raise ValueError("Historical baseline did not produce an explicit accepted result")
    return result


# expose reproduction only; the accepted manifest and tag are never rewritten.
def run_baseline_cli(argv=None):
    parser = argparse.ArgumentParser(description="Reproduce the immutable historical baseline")
    parser.add_argument("--record", action="store_true", help="rejected: the baseline is immutable")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args(argv)
    root = find_repo()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    output = args.output_dir or root / "results/simulation/baseline" / run_id
    try:
        run_baseline(root, output, record=args.record, timeout_s=args.timeout)
    except Exception as error:
        print(f"BASELINE FAIL: {error}")
        return 1
    print("BASELINE PASS: historical SPI, encryption, decryption, reset, BUSY, and read availability")
    print("Reference:", REFERENCE_TAG, REFERENCE_COMMIT)
    print("Artifacts:", output)
    return 0
