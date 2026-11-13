"""recovered helpers for the SPI notebook workflow."""

# import only the dependencies used by this module.
from pathlib import Path
import json
import re
import subprocess
from .build import primitive_inventory, recovered_compile_command
from .common import file_hash
from .controllers import synthesis_command, validate_controller, validate_controller_synthesis
from .tools import discover_tools
from .waveforms import SimulationError, count_sck_edges


# validate ordered application results independently of generated controllers and runners.
def validate_application_records(output, seed, random_trials):
    expected = [
        ("baseline.reset", "busy=0"),
        ("baseline.echo", "value=59c359c3"),
        ("encrypt.started", "busy=1"),
        ("encrypt.complete", "busy=0"),
        ("baseline.encrypt", "value=9cd84392"),
        ("baseline.decrypt_reset", "busy=0"),
        ("decrypt.complete", "busy=0"),
        ("baseline.decrypt", "value=59c359c3"),
    ]
    state = seed
    # every random echo must match its independently reconstructed LCG word.
    for index in range(random_trials):
        state = (1664525 * state + 1013904223) & 0xFFFFFFFF
        expected.extend([(f"random_echo.{index}.reset", "busy=0"),
                         (f"random_echo.{index}", f"value={state:08x}")])
    actual = re.findall(r"^PASS\s+(\S+)\s+(.+)$", output, re.M)
    if len(re.findall(r"^PASS(?:\s|$)", output, re.M)) != len(actual):
        raise ValueError("Malformed PASS record")
    if actual != expected:
        raise ValueError("Application PASS names or values differ from the independent expected sequence")
    return actual


# compile and simulate the recovered core with bounded subprocesses.
def run_simulation(root, generated_dir, output_dir, seed=4313, random_trials=8,
                   timeout_s=30, extra_plusargs=()):
    root, generated_dir, output_dir = map(lambda p: Path(p).resolve(), (root, generated_dir, output_dir))
    # reject invalid run parameters before invoking the toolchain.
    if not 0 <= seed <= 0xFFFFFFFF or not 0 <= random_trials <= 64 or not 0 < timeout_s <= 300:
        raise ValueError("Use a 32-bit seed, 0..64 random trials, and a bounded timeout")
    primitives = primitive_inventory(root)
    tools = discover_tools(require_cells=bool(primitives))
    controller_path = generated_dir / "spi_testbench_controller.sv"
    validate_controller({"source_sv": controller_path.read_text()}, "testbench")
    # prevent old waveforms or diagnostics from being accepted as new evidence.
    output_dir.mkdir(parents=True, exist_ok=False)
    dut = root / "recovery/recovered_sim.v"
    original_hash = file_hash(dut)
    result = {"test_kind": "recovered_dut", "target": "crypto_tb",
              "passed": False, "seed": seed, "random_trials": random_trials,
              "dut_sha256": original_hash, "controller_sha256": file_hash(controller_path),
              "tools": tools, "output_dir": str(output_dir),
              "primitive_inventory": primitives,
              "source_file_list_sha256": file_hash(generated_dir / "recovered.f")}
    executable = output_dir / "simulation.vvp"
    commands = {
        "synthesis_testbench": synthesis_command(tools, controller_path, "spi_testbench_controller",
                                                  "testbench_controller_synth.json"),
        "compile": recovered_compile_command(root, generated_dir, executable, tools=tools),
        "simulation": [tools["vvp"], str(executable), f"+seed={seed}", f"+random_trials={random_trials}", *extra_plusargs],
    }
    (output_dir / "commands.json").write_text(json.dumps(commands, indent=2) + "\n")
    # log output even when compilation, simulation, or the wall-clock bound fails.
    try:
        # never pass model text to a shell.
        for phase, command in commands.items():
            try:
                completed = subprocess.run(command, cwd=output_dir, capture_output=True,
                                           text=True, timeout=timeout_s, check=False)
                output = completed.stdout + completed.stderr
            except subprocess.TimeoutExpired as error:
                output = ""
                # preserve partial output from the timed-out subprocess.
                for chunk in (error.stdout, error.stderr):
                    if chunk:
                        output += chunk.decode(errors="replace") if isinstance(chunk, bytes) else chunk
                (output_dir / f"{phase}.log").write_text(output + "\nFAIL: wall-clock timeout\n")
                raise RuntimeError(f"{phase} exceeded {timeout_s} seconds") from error
            (output_dir / f"{phase}.log").write_text(output)
            result[f"{phase}_returncode"] = completed.returncode
            # a diagnostic or nonzero exit cannot pass through the notebook.
            if completed.returncode != 0:
                raise RuntimeError(f"{phase} failed with exit {completed.returncode}; see {phase}.log")
            # reject storage or interface drift before application compilation.
            if phase == "synthesis_testbench":
                result["controller_synthesis"] = {"testbench": validate_controller_synthesis(
                    output_dir / "testbench_controller_synth.json", "testbench")}
        matches = re.findall(r"^SUMMARY PASS checks=(\d+) baseline_pulses=(\d+) total_pulses=(\d+) seed=(\d+)$", output, re.M)
        # require one complete summary and the exact expected check count.
        if len(matches) != 1 or re.search(r"\b(?:FAIL|FATAL)\b", output):
            raise ValueError("Missing/duplicate summary or failure diagnostic")
        checks, baseline, total, observed_seed = map(int, matches[0])
        # validate both reported checks and their corresponding PASS records.
        if (checks, baseline, total, observed_seed) != (8 + 2 * random_trials, 180, 180 + 66 * random_trials, seed):
            raise ValueError("Summary differs from the requested transaction/check counts")
        cases = validate_application_records(output, seed, random_trials)
        if len(cases) != checks:
            raise ValueError("PASS records do not match the summary check count")
        waveform = output_dir / "waveforms.vcd"
        # validate actual SCK activity, not just the model-generated summary.
        if not waveform.is_file() or count_sck_edges(waveform) != total:
            raise ValueError("Missing waveform or incorrect SCK edge count")
        trace = re.findall(r"^PROCESS (encrypt|decrypt) edge=(\d+) busy=([01])$", output, re.M)
        expected_trace = [(mode, str(edge)) for mode in ("encrypt", "decrypt") for edge in range(8)]
        # require a complete known-valued processing trace in both modes.
        if [(mode, edge) for mode, edge, busy in trace] != expected_trace:
            raise ValueError("Missing or unknown processing-edge observations")
        busy_measurements = {}
        # require BUSY after START drops; START alone can assert this recovered output.
        for mode in ("encrypt", "decrypt"):
            samples = [(int(edge), int(busy)) for name, edge, busy in trace if name == mode]
            idle_edges = [edge for edge, busy in samples if busy == 0]
            if samples[0][1] != 1 or samples[1][1] != 1 or not idle_edges:
                raise ValueError(f"{mode}: BUSY must remain asserted after START before completing")
            completion = idle_edges[0]
            if any(busy != int(edge < completion) for edge, busy in samples):
                raise ValueError(f"{mode}: BUSY reasserted after completion")
            busy_measurements[mode] = {"start_edge": 0, "post_start_busy_edge": 1,
                                       "first_idle_edge": completion,
                                       "busy_by_edge": samples}
        expected_values = {"baseline.echo": "59c359c3", "baseline.encrypt": "9cd84392",
                           "baseline.decrypt": "59c359c3"}
        checked_values = {}
        # require each known-valued supplied comparison exactly once.
        for name, expected in expected_values.items():
            values = re.findall(r"^PASS " + re.escape(name) + r" value=([0-9a-f]{8})$", output, re.M)
            if values != [expected]:
                raise ValueError(f"Missing, unknown, duplicated, or incorrect output: {name}")
            checked_values[name] = {"actual": values[0], "expected": expected, "passed": True}
        # record reset success separately from later idle completion observations.
        for name in ("baseline.reset", "baseline.decrypt_reset"):
            if len(re.findall(r"^PASS " + re.escape(name) + r" busy=0$", output, re.M)) != 1:
                raise ValueError(f"Missing or duplicated reset result: {name}")
        result.update(passed=True, checks=checks, baseline_pulses=baseline, total_pulses=total,
                      waveform=str(waveform), processing_trace=trace,
                      checked_values=checked_values, checked_outputs_known=True,
                      busy_measurements=busy_measurements,
                      cases=[{"name": name, "status": "pass", "detail": detail}
                             for name, detail in cases])
    except Exception as error:
        result["error"] = str(error)
        raise SimulationError(str(error), result) from error
    finally:
        result["dut_unchanged"] = file_hash(dut) == original_hash
        result["controller_unchanged"] = (controller_path.is_file()
                                            and file_hash(controller_path) == result["controller_sha256"])
        if not result["controller_unchanged"]:
            result.update(passed=False, error="Testbench controller changed during validation")
        # retain a failing result if source provenance changes during execution.
        if not result["dut_unchanged"]:
            result.update(passed=False, error="Recovered DUT changed during validation")
        (output_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    # protect source provenance even if a candidate somehow modified the input.
    if not result["dut_unchanged"] or not result["controller_unchanged"]:
        raise SimulationError(result["error"], result)
    return result
