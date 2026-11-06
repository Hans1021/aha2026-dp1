"""verify and reproduce the immutable recovered-DUT baseline."""

# keep baseline capture local and separate from AI generation.
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import uuid

from scripts.testbench_workflow import discover_tools, file_hash, run_simulation


# pin all executable inputs, transaction evidence, and original AI artifacts.
REFERENCE = "baselines/recovered-v1"
REFERENCE_TAG = "baseline-recovered-v1"
EVIDENCE_PATHS = tuple(f"{phase}/{filename}" for phase in ("application", "timing")
                       for filename in ("commands.json", "compile.log", "simulation.log", "result.json"))
ACCEPTANCE_CRITERIA = (
    "spi_readback", "encryption_example", "decryption_example",
    "reset_characterized", "busy_and_read_availability_measured",
    "checked_outputs_known", "explicit_pass_exit",
)
SOURCE_PATHS = (
    "tb/crypto_tb.sv", "tb/crypto_dut.sv", "tb/spi_operations.svh",
    "tb/recovered.f", "tb/baseline_timing_tb.sv",
    "recovery/recovered_sim.v", "recovery/recovered_pins.v",
    "recovery/recovered_netlist.v", "recovery/config_explained.txt",
    "inputs/spi_ice40_crypto_ip_test.py", "inputs/ice40_cryptographic_IP.md",
    "docs/transaction_specification.md", "scripts/testbench_workflow.py",
    "scripts/baseline_timing.py", "scripts/frozen_baseline.py",
    "ai_logs/tb/bootstrap/request.json", "ai_logs/tb/bootstrap/response.json",
    "ai_logs/recovery/deterministic_evidence.json",
    "ai_logs/recovery/01_structured_analyst.json",
    "ai_logs/recovery/02_claim_validation.json",
    "ai_logs/recovery/03_evidence_reviewer.json", "ai_logs/recovery/history.jsonl",
)


# fingerprint exact bytes rather than trusting modification dates.
def source_hashes(root):
    return {name: file_hash(Path(root) / name) for name in SOURCE_PATHS}


# record the simulator identity and the exact vendor model used for acceptance.
def environment_identity():
    tools = discover_tools()
    identity = {"cells_sim_sha256": file_hash(tools["cells"])}
    # version queries are local, finite, and independent of simulation output.
    for name in ("iverilog", "vvp"):
        completed = subprocess.run([tools[name], "-V"], capture_output=True,
                                   text=True, check=True, timeout=10)
        lines = (completed.stdout + completed.stderr).splitlines()
        identity[name] = next(line.strip() for line in lines if "version" in line.lower())
        executable = Path(tools[name])
        identity[name + "_launcher_sha256"] = file_hash(executable)
        # OSS CAD Suite launchers wrap binaries and shared libraries in libexec.
        binary = executable.parent.parent / "libexec" / name
        if binary.is_file():
            identity[name + "_binary_sha256"] = file_hash(binary)
    suite_version = Path(tools["iverilog"]).parent.parent / "VERSION"
    # retain the suite date when an OSS CAD Suite installation supplies it.
    if suite_version.is_file():
        identity["oss_cad_suite_version"] = suite_version.read_text().strip()
        # pin the actual compiler, preprocessor, target backend, and runtime modules.
        suite = suite_version.parent
        for name in ("libexec/ivl", "libexec/ivlpp", "lib/ivl/vvp.tgt", "lib/ivl/system.vpi"):
            identity[name + "_sha256"] = file_hash(suite / name)
    return identity


# reject changed source, models, tools, or committed evidence before simulation.
def verify_reference(root, reference):
    root, reference = Path(root).resolve(), Path(reference).resolve()
    manifest = json.loads((reference / "manifest.json").read_text())
    # validate the fixed baseline contract before trusting any stored hash.
    if (manifest.get("schema_version") != 1 or manifest.get("reference_tag") != REFERENCE_TAG
            or set(manifest.get("sources", {})) != set(SOURCE_PATHS)
            or set(manifest.get("evidence", {})) != set(EVIDENCE_PATHS)
            or manifest.get("acceptance") != {name: True for name in ACCEPTANCE_CRITERIA}
            or manifest.get("seed") != 4313 or manifest.get("random_trials") != 0):
        raise ValueError("Invalid recovered-v1 baseline manifest")
    # require real SHA-256 digests for every pinned input and evidence file.
    for digest in (*manifest["sources"].values(), *manifest["evidence"].values()):
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid baseline SHA-256 digest")
    # report each changed input without updating the reference.
    actual = source_hashes(root)
    changed = [name for name, digest in manifest["sources"].items() if actual[name] != digest]
    if changed:
        raise ValueError("Frozen baseline inputs changed: " + ", ".join(changed))
    if environment_identity() != manifest["environment"]:
        raise ValueError("Frozen simulator or iCE40 model identity changed")
    # evidence paths must remain inside the immutable reference directory.
    for name, digest in manifest["evidence"].items():
        path = (reference / name).resolve()
        if not path.is_relative_to(reference) or file_hash(path) != digest:
            raise ValueError(f"Frozen baseline evidence changed: {name}")
    # an available tag anchors the manifest itself against accidental replacement.
    tagged = subprocess.run(
        ["git", "show", f"{REFERENCE_TAG}:{REFERENCE}/manifest.json"], cwd=root,
        capture_output=True, text=True, timeout=10,
    )
    if tagged.returncode == 0 and json.loads(tagged.stdout) != manifest:
        raise ValueError("Working manifest differs from the frozen Git tag")
    return manifest


# compare deterministic measurements while excluding timestamps and file paths.
def measured_signature(application, timing):
    # require explicit evidence for every acceptance item, not only a process exit.
    if (application.get("passed") is not True or timing.get("passed") is not True
            or application.get("dut_unchanged") is not True
            or application.get("checked_outputs_known") is not True
            or timing.get("checked_outputs_known") is not True
            or application.get("checks") != 8 or application.get("total_pulses") != 180):
        raise ValueError("Baseline acceptance evidence is incomplete")
    signature = {
        "application_checks": application["checks"],
        "application_pulses": application["total_pulses"],
        "checked_values": application["checked_values"],
        "busy_measurements": application["busy_measurements"],
        "timing": timing["measurements"],
    }
    # JSON round-tripping makes tuple observations comparable with saved arrays.
    return json.loads(json.dumps(signature))


# retain compact evidence with portable path labels and separate waveform hashes.
def capture_reference(root, reference, output_dir, inputs, environment, signature):
    root, reference, output_dir = map(Path, (root, reference, output_dir))
    reference.mkdir(parents=True, exist_ok=False)
    tools = discover_tools()
    replacements = [(str(output_dir), "$RUN"), (str(root), "$REPO"),
                    (str(Path(tools["iverilog"]).parent.parent), "$OSS_CAD_SUITE")]
    evidence = {}
    waveforms = {}
    # preserve measured commands, diagnostics, and results; keep large VCDs in run output.
    for phase in ("application", "timing"):
        destination = reference / phase
        destination.mkdir()
        for filename in ("commands.json", "compile.log", "simulation.log", "result.json"):
            content = (output_dir / phase / filename).read_text()
            for original, label in replacements:
                content = content.replace(original, label)
            path = destination / filename
            path.write_text(content)
            evidence[str(path.relative_to(reference))] = file_hash(path)
        waveforms[phase] = file_hash(output_dir / phase / "waveforms.vcd")
    manifest = {
        "schema_version": 1, "reference_tag": REFERENCE_TAG,
        "seed": 4313, "random_trials": 0,
        "sources": inputs, "environment": environment,
        "evidence": evidence, "capture_waveform_sha256": waveforms,
        "measurements": signature,
        "acceptance": {name: True for name in ACCEPTANCE_CRITERIA},
        "scope": "supplied SPI/encryption/decryption cases and measured reset/timing only",
    }
    (reference / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


# run the original application and separate probes without changing frozen sources.
def run_baseline(root, output_dir, *, record=False, timeout_s=30):
    from scripts.baseline_timing import run_timing

    root, output_dir = Path(root).resolve(), Path(output_dir).resolve()
    reference = root / REFERENCE
    # capture is a one-time operation; verification never rewrites reference files.
    if record and reference.exists():
        raise FileExistsError("The baseline already exists; never overwrite recovered-v1")
    manifest = None if record else verify_reference(root, reference)
    inputs, environment = source_hashes(root), environment_identity()
    output_dir.mkdir(parents=True, exist_ok=False)
    result = {"passed": False, "test_kind": "frozen_baseline", "reference_tag": REFERENCE_TAG}
    # preserve a failing aggregate result when any compile or acceptance check fails.
    try:
        application = run_simulation(root, root / "tb", output_dir / "application",
                                     seed=4313, random_trials=0, timeout_s=timeout_s)
        timing = run_timing(root, output_dir / "timing", timeout_s=timeout_s)
        signature = measured_signature(application, timing)
        if source_hashes(root) != inputs or environment_identity() != environment:
            raise ValueError("Baseline inputs changed during validation")
        if manifest is not None and signature != manifest["measurements"]:
            raise ValueError("Measured behavior differs from the frozen baseline")
        # write a reference only after all acceptance checks and integrity checks pass.
        if record:
            capture_reference(root, reference, output_dir, inputs, environment, signature)
        result.update(passed=True, measurements=signature)
    except Exception as error:
        result["error"] = str(error)
        raise
    finally:
        (output_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


# expose a fixed baseline workload and an explicit write-once capture operation.
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", action="store_true", help="capture once after passing all checks")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    output = args.output_dir or root / "results/simulation/baseline" / run_id
    try:
        result = run_baseline(root, output, record=args.record, timeout_s=args.timeout)
    except Exception as error:
        print(f"BASELINE FAIL: {error}")
        print("Artifacts:", output)
        return 1
    print("BASELINE PASS: SPI readback, encryption, decryption, reset, BUSY, and read availability")
    print("Reference:", root / REFERENCE)
    print("Artifacts:", output)
    return 0 if result["passed"] else 1


# propagate an explicit pass/fail status to make and automation.
if __name__ == "__main__":
    raise SystemExit(main())
