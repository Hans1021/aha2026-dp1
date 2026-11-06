# verify actual serial-driver behavior without any recovered design dependency.
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts import driver_workflow as driver
from scripts.testbench_workflow import AILogger, SimulationError


# resolve fixtures from the checkout rather than the test runner's directory.
ROOT = Path(__file__).resolve().parents[1]


# exercise generation provenance and the restricted mock schema without network access.
class MockGenerationTests(unittest.TestCase):
    # copy recorded artifacts into a temporary log root for each test.
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "tb").mkdir()
        shutil.copytree(ROOT / "ai_logs/tb/driver_bootstrap", self.root / "ai_logs/tb/driver_bootstrap")
        self.logger = AILogger(self.root, source_dir="tb")

    # replay exact AI-authored source without invoking a provider.
    def test_recorded_mock_matches_source_and_logs_both_sides(self):
        artifacts = driver.request_mock(driver.build_driver_prompt(ROOT), self.logger)
        self.assertEqual(artifacts["mock_sv"], (ROOT / "tb/spi_mock_peripheral.sv").read_text())
        paths = driver.write_mock(artifacts, self.root / "generated")
        self.assertEqual(set(paths), {"mock_sv", "scheduling_explanation"})
        events = [json.loads(line) for line in self.logger.events_path.read_text().splitlines()]
        self.assertEqual([event["kind"] for event in events], ["prompt", "output"])
        self.assertFalse(events[1]["live_api_call"])
        self.assertEqual(events[1]["target"], "tb_driver")

    # refuse stale replay after a source or prompt change and retain the failed request.
    def test_changed_replay_prompt_fails(self):
        with self.assertRaisesRegex(ValueError, "prompt changed"):
            driver.request_mock("changed interface", self.logger)
        events = [json.loads(line) for line in self.logger.events_path.read_text().splitlines()]
        self.assertEqual([event["kind"] for event in events], ["prompt", "error"])

    # missing credentials cannot silently switch live mode to replay.
    def test_live_without_key_logs_failure(self):
        with mock.patch.dict("os.environ", {"PORTKEY_API_KEY": ""}):
            with self.assertRaisesRegex(RuntimeError, "PORTKEY_API_KEY"):
                driver.request_mock("generate the mock", self.logger, mode="live")
        events = [json.loads(line) for line in self.logger.events_path.read_text().splitlines()]
        self.assertEqual([event["kind"] for event in events], ["prompt", "error"])

    # reject external effects or another clock before any generated source is compiled.
    def test_unsafe_mock_variants_fail_validation(self):
        artifacts = json.loads((ROOT / "ai_logs/tb/driver_bootstrap/response.json").read_text())
        additions = ('$system("forbidden");', '$fopen("outside", "w");',
                     '`include "other.sv"', "SCK = 1'b1;", "force BUSY = 1'b0;",
                     "module fake; endmodule", "always #10 BUSY = ~BUSY;")
        # check each prohibited construct independently.
        for addition in additions:
            with self.subTest(addition=addition), self.assertRaises(ValueError):
                driver.validate_mock({**artifacts, "mock_sv": artifacts["mock_sv"] + "\n" + addition})


# compile the real shared driver and mock using bounded Icarus processes.
class DriverSimulationTests(unittest.TestCase):
    # skip only when Icarus itself is unavailable, regardless of vendor models.
    @classmethod
    def setUpClass(cls):
        try:
            driver.discover_driver_tools()
        except FileNotFoundError as error:
            raise unittest.SkipTest(str(error)) from error

    # isolate artifacts and construct a checkout containing no recovered DUT.
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.root = self.directory / "checkout"
        (self.root / "tb").mkdir(parents=True)
        # the target needs exactly these three simulation sources.
        for name in ("tb_driver.sv", "spi_operations.svh", "spi_mock_peripheral.sv"):
            shutil.copy2(ROOT / "tb" / name, self.root / "tb" / name)

    # report named evidence from actual simulation, with a clear driver-only scope.
    def test_driver_runs_without_recovery_or_vendor_cells(self):
        with mock.patch.dict("os.environ", {"ICE40_CELLS_SIM": "/nonexistent/cells_sim.v"}):
            result = driver.run_driver(self.root, self.directory / "normal")
        self.assertTrue(result["passed"])
        self.assertEqual(result["test_kind"], "driver")
        self.assertEqual(result["target"], "tb_driver")
        self.assertFalse(result["validates_cryptography"])
        self.assertEqual((result["checks"], result["frames"], result["total_pulses"]), (75, 14, 457))
        self.assertEqual(len(result["cases"]), 75)
        self.assertTrue(Path(result["waveform"]).is_file())
        commands = json.loads((self.directory / "normal/commands.json").read_text())
        self.assertNotIn("recovered", json.dumps(commands))
        self.assertNotIn("cells_sim", json.dumps(commands))
        output = (self.directory / "normal/simulation.log").read_text()
        self.assertIn("DRIVER TEST tb_driver", output)
        self.assertIn("PASS driver.frame.0.rx value=d2963a5c", output)
        self.assertIn("PASS driver.frame.0.tx value=01234567", output)
        self.assertIn("PASS driver.frame.5.tx value=96a50fc3", output)
        self.assertIn("PASS driver.deselected.rx", output)

    # make each specific fault fail before a successful summary or watchdog fallback.
    def test_order_framing_scoreboard_unknown_and_timeout_faults(self):
        faults = {
            "reverse_tx": "FAIL driver.frame.0.tx",
            "reverse_rx": "FAIL driver.frame.0.rx",
            "mismatch": "FAIL driver.negative.mismatch",
            "unknown_x": "FAIL driver.negative.unknown_x",
            "unknown_z": "FAIL driver.negative.unknown_z",
            "unknown_miso_x": "unknown MISO",
            "unknown_miso_z": "unknown MISO",
            "short_frame": r"(?:mock|monitor)\.frame_(?:end|length)",
            "long_frame": r"(?:mock|monitor)\.frame_(?:length|overflow)",
            "split_frame": r"(?:mock|monitor)\.frame_(?:end|length)",
            "timeout": "FAIL driver.negative.timeout timeout",
        }
        # run each defect as an independent nonzero-exit simulation.
        for scenario, diagnostic in faults.items():
            with self.subTest(scenario=scenario):
                output_dir = self.directory / scenario
                with self.assertRaises(SimulationError) as caught:
                    driver.run_driver(self.root, output_dir, extra_plusargs=(f"+scenario={scenario}",))
                self.assertFalse(caught.exception.result["passed"])
                self.assertNotEqual(caught.exception.result["simulation_returncode"], 0)
                output = (output_dir / "simulation.log").read_text()
                self.assertRegex(output, diagnostic)
                self.assertNotIn("DRIVER SUMMARY PASS", output)
                self.assertNotIn("watchdog", output)
                self.assertNotIn("escaped mock", output)

    # equal seeds reproduce arbitrary MOSI words while changed seeds alter them.
    def test_seeds_reproduce_stimulus(self):
        words = []
        # include the zero seed to exercise the full deterministic recurrence.
        for index, seed in enumerate((0, 0, 4294967295)):
            output_dir = self.directory / f"seed-{index}"
            result = driver.run_driver(self.root, output_dir, seed=seed)
            self.assertEqual(result["seed"], seed)
            output = (output_dir / "simulation.log").read_text()
            words.append(re.findall(r"DRIVER TEST driver.frame.\d+ tx=([0-9a-f]{8})", output)[6:])
        self.assertEqual(len(words[0]), 8)
        self.assertEqual(words[0], words[1])
        self.assertNotEqual(words[0], words[2])

    # keep partial process output and a failed result when the host timeout expires.
    def test_host_timeout_retains_evidence(self):
        expired = subprocess.TimeoutExpired(["iverilog"], 0.1, output=b"partial compile output")
        output_dir = self.directory / "timeout-host"
        with mock.patch.object(driver.subprocess, "run", side_effect=expired):
            with self.assertRaises(SimulationError):
                driver.run_driver(self.root, output_dir, timeout_s=0.1)
        self.assertIn("partial compile output", (output_dir / "compile.log").read_text())
        self.assertFalse(json.loads((output_dir / "result.json").read_text())["passed"])

    # reject reused output directories before stale waveform evidence can be accepted.
    def test_existing_run_evidence_cannot_be_reused_or_overwritten(self):
        output_dir = self.directory / "existing-run"
        first = driver.run_driver(self.root, output_dir)
        self.assertTrue(first["passed"])
        original = {path.name: path.read_bytes() for path in output_dir.iterdir()}
        testbench = self.root / "tb/tb_driver.sv"
        testbench.write_text(testbench.read_text().replace('$dumpvars(0, tb_driver);', ""))
        # a defective second run must fail before compiling or altering prior evidence.
        with mock.patch.object(driver.subprocess, "run") as run:
            with self.assertRaises(FileExistsError):
                driver.run_driver(self.root, output_dir)
        run.assert_not_called()
        self.assertEqual(original, {path.name: path.read_bytes() for path in output_dir.iterdir()})

    # the waveform and its counted edges remain mandatory even with a passing log.
    def test_missing_waveform_fails(self):
        testbench = self.root / "tb/tb_driver.sv"
        testbench.write_text(testbench.read_text().replace('$dumpvars(0, tb_driver);', ""))
        with self.assertRaisesRegex(SimulationError, "waveform"):
            driver.run_driver(self.root, self.directory / "no-waveform")


# support both unittest discovery and direct invocation.
if __name__ == "__main__":
    unittest.main()
