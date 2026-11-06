"""validate real reset and read-boundary measurements and their failure paths."""

# run isolated copies so negative probes cannot modify the frozen sources.
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts import baseline_timing as timing
from scripts.testbench_workflow import SimulationError, discover_tools


# locate committed fixtures independently of the process working directory.
ROOT = Path(__file__).resolve().parents[1]


# exercise actual recovered circuitry, with bounded host and simulation waits.
class BaselineTimingTests(unittest.TestCase):
    # skip only when the external simulator or real cell models are unavailable.
    @classmethod
    def setUpClass(cls):
        try:
            discover_tools()
        except FileNotFoundError as error:
            raise unittest.SkipTest(str(error)) from error

    # copy the exact maintained inputs before each deliberate source mutation.
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "checkout"
        files = ("tb/recovered.f", "tb/crypto_dut.sv", "tb/crypto_tb.sv",
                 "tb/spi_operations.svh", "tb/baseline_timing_tb.sv", "recovery/recovered_sim.v")
        # keep all source edits inside the isolated checkout.
        for relative in files:
            destination = self.root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, destination)

    # distinguish BUSY completion from the later safe serial-read boundary.
    def test_real_reset_busy_and_result_availability(self):
        result = timing.run_timing(self.root, self.root / "output")
        self.assertTrue(result["passed"])
        self.assertTrue(result["checked_outputs_known"])
        self.assertTrue(result["dut_unchanged"])
        self.assertEqual((result["checks"], result["total_pulses"]), (105, 1443))
        observations = result["measurements"]
        self.assertEqual(observations["first_completed_edge"], {"encrypt": 3, "decrypt": 3})
        self.assertEqual(observations["first_matching_read_boundary"], {"encrypt": 4, "decrypt": 4})
        self.assertEqual(observations["reset_active"][1], {"phase": "release_no_clock", "busy": 1})
        self.assertEqual(observations["reset_data"][0], {"phase": "unclocked_reset", "word": "ffffffff"})
        # each mode must be idle before start, latched busy, then completed.
        for mode in ("encrypt", "decrypt"):
            trace = observations["busy"][mode]
            self.assertEqual([row["busy"] for row in trace], [0, 1, 1, 1, 0, 0, 0, 0, 0])
            reads = observations["read_boundaries"][mode]
            self.assertEqual([row["match"] for row in reads], [False] * 4 + [True] * 4)

    # a combinational START pulse without a clock must not count as accepted work.
    def test_unclocked_start_cannot_pass_completion(self):
        path = self.root / "tb/spi_operations.svh"
        source = path.read_text()
        operation = re.search(r"task automatic pulse_start\b.*?endtask", source, re.S).group()
        broken = operation.replace("sck_pulse(unused_miso, sampled_busy);", "#(2 * HALF_PERIOD_NS);")
        self.assertNotEqual(operation, broken)
        path.write_text(source.replace(operation, broken))
        with self.assertRaises(SimulationError) as caught:
            timing.run_timing(self.root, self.root / "output")
        self.assertFalse(caught.exception.result["checked_outputs_known"])
        output = (self.root / "output/simulation.log").read_text()
        self.assertIn("FAIL timing.reset_busy.accepted", output)
        self.assertNotIn("TIMING SUMMARY PASS", output)

    # moving the expected read boundary to completion must expose stale data at e3.
    def test_busy_low_is_too_early_for_result_read(self):
        path = self.root / "tb/baseline_timing_tb.sv"
        source = path.read_text()
        before = "cutoff < 4 ? input_word : expected_word"
        self.assertIn(before, source)
        path.write_text(source.replace(before, "cutoff < 3 ? input_word : expected_word"))
        with self.assertRaises(SimulationError):
            timing.run_timing(self.root, self.root / "output")
        output = (self.root / "output/simulation.log").read_text()
        self.assertIn("FAIL timing.encrypt.after_e3", output)
        self.assertNotIn("TIMING SUMMARY PASS", output)

    # a successful simulator summary cannot replace missing boundary observations.
    def test_missing_observations_fail_acceptance(self):
        path = self.root / "tb/baseline_timing_tb.sv"
        source = path.read_text()
        changed, count = re.subn(r'\$display\("TIMING AVAIL.*?\);', "", source, flags=re.S)
        self.assertEqual(count, 1)
        path.write_text(changed)
        with self.assertRaisesRegex(SimulationError, "Incomplete BUSY or read-boundary"):
            timing.run_timing(self.root, self.root / "output")
        self.assertIn("TIMING SUMMARY PASS", (self.root / "output/simulation.log").read_text())

    # require a fresh directory so an old waveform cannot satisfy a new probe.
    def test_existing_output_is_preserved(self):
        output = self.root / "output"
        output.mkdir()
        evidence = output / "waveforms.vcd"
        evidence.write_text("prior evidence\n")
        with mock.patch.object(timing.subprocess, "run") as run:
            with self.assertRaises(FileExistsError):
                timing.run_timing(self.root, output)
        run.assert_not_called()
        self.assertEqual(evidence.read_text(), "prior evidence\n")

    # preserve partial diagnostics and an explicit failed record on host timeout.
    def test_timeout_cannot_pass(self):
        expired = subprocess.TimeoutExpired(["iverilog"], 0.1, output=b"partial output")
        output = self.root / "output"
        with mock.patch.object(timing.subprocess, "run", side_effect=expired):
            with self.assertRaises(SimulationError):
                timing.run_timing(self.root, output, timeout_s=0.1)
        result = json.loads((output / "result.json").read_text())
        self.assertFalse(result["passed"])
        self.assertFalse(result["checked_outputs_known"])
        self.assertIn("partial output", (output / "compile.log").read_text())


# expose the probes through the repository's normal unittest discovery.
if __name__ == "__main__":
    unittest.main()
