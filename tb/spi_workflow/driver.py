"""driver helpers for the SPI notebook workflow."""

# import only the dependencies used by this module.
from pathlib import Path
import json
import re
import subprocess
from .common import file_hash
from .controllers import synthesis_command, validate_controller, validate_controller_synthesis
from .mock import validate_mock, validate_mock_synthesis
from .tools import discover_driver_tools
from .waveforms import SimulationError, count_sck_edges


# derive expected serial words and complete PASS records independently of generated HDL.
def validate_driver_records(output, seed):
    words = [0x01234567, 0x00000000, 0xFFFFFFFF, 0x80000000, 0x00000001, 0x96A50FC3]
    state = seed
    # advance the specified LCG once for each random frame.
    for _ in range(8):
        state = (1664525 * state + 1013904223) & 0xFFFFFFFF
        words.append(state)
    expected = [("driver.reset", "busy=0")]
    # require all five named comparisons for every directed or random frame.
    for index, word in enumerate(words):
        response = 0xD2963A5C ^ ((0x01020408 * index) & 0xFFFFFFFF)
        prefix = f"driver.frame.{index}"
        for suffix, value in (("rx", response), ("tx", word), ("wire", word),
                              ("frames", index + 1), ("bits", 32)):
            expected.append((prefix + "." + suffix, f"value={value:08x}"))
    expected.extend([
        ("driver.deselected.rx", f"value={words[-1]:08x}"),
        ("driver.deselected.frames", "value=0000000e"),
        ("driver.mock_compute.started", "busy=1"),
        ("driver.mock_compute.complete", "busy=0"),
    ])
    cases = re.findall(r"^PASS\s+(\S+)\s+(.+)$", output, re.M)
    if len(re.findall(r"^PASS(?:\s|$)", output, re.M)) != len(cases):
        raise ValueError("Malformed PASS record")
    # duplicates, reordered checks, unknowns, or incorrect values cannot satisfy acceptance.
    if cases != expected:
        raise ValueError("Driver PASS names or values differ from the independent expected sequence")
    wire_frames = re.findall(r"^DRIVER FRAME number=(\d+) bits=(\d+) mosi=([0-9a-f]{8})$", output, re.M)
    if len(re.findall(r"^DRIVER FRAME(?:\s|$)", output, re.M)) != len(wire_frames):
        raise ValueError("Malformed DRIVER FRAME record")
    expected_frames = [(str(index + 1), "32", f"{word:08x}") for index, word in enumerate(words)]
    if wire_frames != expected_frames:
        raise ValueError("Driver MOSI frames differ from the independent directed/LCG sequence")
    return cases


# compile the maintained harness, generated operations, and selected mock.
def run_driver(root, output_dir, seed=4313, timeout_s=30, *, source_dir, extra_plusargs=()):
    output_dir, source_dir = Path(output_dir).resolve(), Path(source_dir).resolve()
    # bound host processes and reject malformed seeds before tool invocation.
    if not isinstance(seed, int) or not 0 <= seed <= 0xFFFFFFFF or not 0 < timeout_s <= 300:
        raise ValueError("Use a 32-bit seed and a timeout in (0, 300] seconds")
    tools = discover_driver_tools()
    # require every driver source from this notebook attempt.
    for filename in ("tb_driver.sv", "spi_operations.svh", "spi_mock_peripheral.sv",
                     "spi_harness_controller.sv"):
        if not (source_dir / filename).is_file():
            raise FileNotFoundError(f"Missing driver bundle source: {source_dir / filename}")
    mock_path = source_dir / "spi_mock_peripheral.sv"
    validate_mock({"mock_sv": mock_path.read_text()})
    controller_path = source_dir / "spi_harness_controller.sv"
    validate_controller({"source_sv": controller_path.read_text()}, "harness")
    # keep prior evidence immutable and prevent stale waveforms from passing.
    output_dir.mkdir(parents=True, exist_ok=False)
    result = {"target": "tb_driver", "test_kind": "driver", "passed": False,
              "validates_cryptography": False, "seed": seed, "random_trials": 8,
              "mock_sha256": file_hash(mock_path), "controller_sha256": file_hash(controller_path),
              "tools": tools, "output_dir": str(output_dir)}
    executable = output_dir / "tb_driver.vvp"
    # check synthesizability and state-free structure before simulating the harness.
    synthesis_script = ("read_verilog -sv " + json.dumps(str(mock_path))
                        + "; synth -top spi_mock_peripheral -noabc; check -assert; "
                        + "write_json mock_synth.json")
    commands = {
        "synthesis": [tools["yosys"], "-Q", "-T", "-p", synthesis_script],
        "synthesis_harness": synthesis_command(tools, controller_path, "spi_harness_controller",
                                                "harness_controller_synth.json"),
        "compile": [tools["iverilog"], "-g2012", "-s", "tb_driver", "-I", str(source_dir),
                    "-o", str(executable), str(source_dir / "tb_driver.sv"), str(mock_path),
                    str(controller_path)],
        "simulation": [tools["vvp"], str(executable), f"+seed={seed}", *extra_plusargs],
    }
    (output_dir / "commands.json").write_text(json.dumps(commands, indent=2) + "\n")
    # retain failure evidence and a machine-readable result for every tool run.
    try:
        for phase, command in commands.items():
            try:
                completed = subprocess.run(command, cwd=output_dir, capture_output=True,
                                           text=True, timeout=timeout_s, check=False)
                output = completed.stdout + completed.stderr
            except subprocess.TimeoutExpired as error:
                chunks = [part.decode(errors="replace") if isinstance(part, bytes) else part
                          for part in (error.stdout, error.stderr) if part]
                (output_dir / f"{phase}.log").write_text("".join(chunks) + "\nDRIVER FAIL wall-clock timeout\n")
                raise RuntimeError(f"Driver {phase} exceeded {timeout_s} seconds") from error
            (output_dir / f"{phase}.log").write_text(output)
            result[f"{phase}_returncode"] = completed.returncode
            if completed.returncode != 0:
                raise RuntimeError(f"Driver {phase} failed with exit {completed.returncode}; see {phase}.log")
            # reject latches, flip-flops, memories, and an incorrect module interface.
            if phase == "synthesis":
                result["mock_synthesis"] = validate_mock_synthesis(output_dir / "mock_synth.json")
            elif phase == "synthesis_harness":
                result["controller_synthesis"] = {"harness": validate_controller_synthesis(
                    output_dir / "harness_controller_synth.json", "harness")}
        summaries = re.findall(r"^DRIVER SUMMARY PASS checks=(\d+) frames=(\d+) total_pulses=(\d+) seed=(\d+)$", output, re.M)
        # reject forged, duplicated, or failure-bearing successful exits.
        if len(summaries) != 1 or re.search(r"\b(?:FAIL|FATAL)\b", output):
            raise ValueError("Missing driver summary or explicit failure diagnostic")
        checks, frames, pulses, observed_seed = map(int, summaries[0])
        cases = validate_driver_records(output, seed)
        if (checks, frames, pulses, observed_seed) != (75, 14, 457, seed) or len(cases) != checks:
            raise ValueError("Unexpected driver check, frame, pulse, or seed count")
        waveform = output_dir / "waveforms.vcd"
        if not waveform.is_file() or count_sck_edges(waveform, top="tb_driver") != pulses:
            raise ValueError("Missing driver waveform or incorrect clock count")
        result.update(passed=True, checks=checks, frames=frames, total_pulses=pulses,
                      waveform=str(waveform), cases=[{"name": name, "status": "pass", "detail": detail}
                                                     for name, detail in cases])
    except Exception as error:
        result["error"] = str(error)
        raise SimulationError(str(error), result) from error
    finally:
        result["controller_unchanged"] = (controller_path.is_file()
                                            and file_hash(controller_path) == result["controller_sha256"])
        # reject source changes even after successful checks and synthesis.
        if not result["controller_unchanged"]:
            result.update(passed=False, error="Harness controller changed during driver validation")
        (output_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    if not result["controller_unchanged"]:
        raise SimulationError(result["error"], result)
    return result
