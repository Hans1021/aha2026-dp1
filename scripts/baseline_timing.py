"""measure real-DUT reset, completion, and serial read boundaries."""

# share the maintained source list, primitive models, and four-state testbench.
import json
from pathlib import Path
import re
import subprocess

from scripts.testbench_workflow import (
    SimulationError, count_sck_edges, discover_tools, file_hash, recovered_compile_command,
)


# require complete measured observations before deriving timing conclusions.
def parse_measurements(output):
    start = [{"phase": phase, "busy": int(busy)} for phase, busy in
             re.findall(r"^TIMING START phase=(\w+) busy=([01])$", output, re.M)]
    reset_active = [{"phase": phase, "busy": int(busy)} for phase, busy in
                    re.findall(r"^TIMING RESET_ACTIVE phase=(\w+) busy=([01])$", output, re.M)]
    reset_data = [{"phase": phase, field: int(value) if field == "miso" else value}
                  for phase, field, value in
                  re.findall(r"^TIMING RESET_DATA phase=(\w+) (word|miso)=([0-9a-f]+)$", output, re.M)]
    # reject missing or duplicated reset observations, including unknown samples.
    if start != [{"phase": "assert_no_clock", "busy": 1}, {"phase": "release_no_clock", "busy": 0}]:
        raise ValueError("START no-clock observations differ from the measured contract")
    if reset_active != [{"phase": "assert_no_clock", "busy": 0},
                        {"phase": "release_no_clock", "busy": 1},
                        {"phase": "release_after_reset_clock", "busy": 0}]:
        raise ValueError("Missing or invalid active-reset observations")
    if reset_data != [{"phase": "unclocked_reset", "word": "ffffffff"},
                      {"phase": "reset_edge", "miso": 1},
                      {"phase": "release_edge", "miso": 0},
                      {"phase": "clocked_reset", "word": "00000000"}]:
        raise ValueError("Missing or invalid reset-storage observations")
    measurements = {"start_without_clock": start, "reset_active": reset_active,
                    "reset_data": reset_data, "busy": {}, "read_boundaries": {},
                    "first_completed_edge": {}, "first_matching_read_boundary": {}}
    busy_rows = re.findall(r"^TIMING BUSY mode=(encrypt|decrypt) edge=(-?\d+) busy=([01])$", output, re.M)
    read_rows = re.findall(r"^TIMING AVAIL mode=(encrypt|decrypt) after_edge=(\d+) busy_before=([01]) "
                           r"word=([0-9a-f]{8}) expected=([0-9a-f]{8}) match=([01])$", output, re.M)
    # require all observations from each separate operation and fresh-read trial.
    if len(busy_rows) != 18 or len(read_rows) != 16:
        raise ValueError("Incomplete BUSY or read-boundary observations")
    for mode, input_word, expected_word in (("encrypt", "59c359c3", "9cd84392"),
                                            ("decrypt", "9cd84392", "59c359c3")):
        busy = [{"edge": int(edge), "busy": int(value)} for name, edge, value in busy_rows if name == mode]
        reads = [{"after_edge": int(edge), "busy_before": int(value), "word": word,
                  "expected": expected, "match": bool(int(match))}
                 for name, edge, value, word, expected, match in read_rows if name == mode]
        expected_busy = [{"edge": edge, "busy": int(0 <= edge < 3)} for edge in range(-1, 8)]
        expected_reads = [{"after_edge": edge, "busy_before": int(edge < 3),
                           "word": input_word if edge < 4 else expected_word,
                           "expected": expected_word, "match": edge >= 4} for edge in range(8)]
        # idle-before-start cannot satisfy completion; require accepted high first.
        if busy != expected_busy or reads != expected_reads:
            raise ValueError(f"{mode} timing differs from the measured contract")
        measurements["busy"][mode] = busy
        measurements["read_boundaries"][mode] = reads
        measurements["first_completed_edge"][mode] = next(row["edge"] for row in busy if row["edge"] > 0 and row["busy"] == 0)
        measurements["first_matching_read_boundary"][mode] = next(row["after_edge"] for row in reads if row["match"])
    return measurements


# compile the real timing probe into fresh evidence with bounded tool processes.
def run_timing(root, output_dir, timeout_s=30):
    root, output_dir = Path(root).resolve(), Path(output_dir).resolve()
    # bound both compilation and simulation before touching output artifacts.
    if not 0 < timeout_s <= 300:
        raise ValueError("Use a timeout in (0, 300] seconds")
    tools = discover_tools()
    dut = root / "recovery/recovered_sim.v"
    original_hash = file_hash(dut)
    output_dir.mkdir(parents=True, exist_ok=False)
    executable = output_dir / "baseline_timing.vvp"
    commands = {
        "compile": recovered_compile_command(root, root / "tb", executable,
                    top="baseline_timing_tb", testbench=root / "tb/baseline_timing_tb.sv", tools=tools),
        "simulation": [tools["vvp"], str(executable)],
    }
    (output_dir / "commands.json").write_text(json.dumps(commands, indent=2) + "\n")
    result = {"test_kind": "recovered_dut", "target": "baseline_timing_tb", "passed": False,
              "checked_outputs_known": False, "dut_sha256": original_hash,
              "output_dir": str(output_dir), "tools": tools}
    # retain failures and partial output rather than presenting missing data as pass.
    try:
        for phase, command in commands.items():
            try:
                completed = subprocess.run(command, cwd=output_dir, capture_output=True,
                                           text=True, check=False, timeout=timeout_s)
                output = completed.stdout + completed.stderr
            except subprocess.TimeoutExpired as error:
                chunks = [chunk.decode(errors="replace") if isinstance(chunk, bytes) else chunk
                          for chunk in (error.stdout, error.stderr) if chunk]
                (output_dir / f"{phase}.log").write_text("".join(chunks) + "\nFAIL timing wall-clock timeout\n")
                raise RuntimeError(f"Timing {phase} exceeded {timeout_s} seconds") from error
            (output_dir / f"{phase}.log").write_text(output)
            result[f"{phase}_returncode"] = completed.returncode
            # unresolved modules and simulator failures must terminate acceptance.
            if completed.returncode != 0:
                raise RuntimeError(f"Timing {phase} failed with exit {completed.returncode}; see {phase}.log")
        summaries = re.findall(r"^TIMING SUMMARY PASS checks=(\d+) total_pulses=(\d+)$", output, re.M)
        # a printed summary alone cannot replace checks, observations, or a waveform.
        if summaries != [("105", "1443")] or re.search(r"\b(?:FAIL|FATAL)\b", output):
            raise ValueError("Missing/invalid timing summary or failure diagnostic")
        if len(re.findall(r"^PASS ", output, re.M)) != 105:
            raise ValueError("Timing PASS count differs from its summary")
        waveform = output_dir / "waveforms.vcd"
        if not waveform.is_file() or count_sck_edges(waveform, top="baseline_timing_tb") != 1443:
            raise ValueError("Missing timing waveform or incorrect SCK edge count")
        result.update(measurements=parse_measurements(output), checks=105, total_pulses=1443,
                      waveform=str(waveform), checked_outputs_known=True, passed=True)
    except Exception as error:
        result["error"] = str(error)
        raise SimulationError(str(error), result) from error
    finally:
        result["dut_unchanged"] = file_hash(dut) == original_hash
        # source integrity is part of acceptance even after successful observations.
        if not result["dut_unchanged"]:
            result.update(passed=False, checked_outputs_known=False, error="Recovered DUT changed during timing validation")
        (output_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    if not result["passed"]:
        raise SimulationError(result["error"], result)
    return result
