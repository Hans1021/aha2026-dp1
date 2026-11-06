"""validate recovered compilation and evidence acceptance without changing the DUT."""

# keep all mutated fixtures and simulation products outside the checkout.
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from scripts import testbench_workflow as workflow


# resolve the actual AI-generated harness and recovered source.
ROOT = Path(__file__).resolve().parents[1]


# test real model linkage and reject misleading completion or stale evidence.
class BaselineBuildTests(unittest.TestCase):
    # skip only when the external simulator or actual cell models are unavailable.
    @classmethod
    def setUpClass(cls):
        try:
            cls.tools = workflow.discover_tools()
        except FileNotFoundError as error:
            raise unittest.SkipTest(str(error)) from error

    # copy the minimal maintained source set for independent mutation tests.
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.root = self.directory / "repo"
        (self.root / "tb").mkdir(parents=True)
        (self.root / "recovery").mkdir()
        # preserve real inputs while allowing deliberate fixture defects.
        for name in ("crypto_dut.sv", "crypto_tb.sv", "spi_operations.svh", "recovered.f"):
            shutil.copy2(ROOT / "tb" / name, self.root / "tb" / name)
        shutil.copy2(ROOT / "recovery/recovered_sim.v", self.root / "recovery/recovered_sim.v")

    # run the exact 180-pulse application without unrelated random trials.
    def simulate(self, label="run"):
        return workflow.run_simulation(self.root, self.root / "tb", self.directory / label,
                                       random_trials=0, timeout_s=15)

    # require explicit language, top, sources, actual RAM models, and known checks.
    def test_real_baseline_links_required_cells_and_records_evidence(self):
        result = self.simulate()
        command = json.loads((self.directory / "run/commands.json").read_text())["compile"]
        self.assertEqual(command[command.index("-s") + 1], "crypto_tb")
        self.assertIn("-g2012", command)
        self.assertIn("-DNO_ICE40_DEFAULT_ASSIGNMENTS", command)
        self.assertIn(self.tools["cells"], command)
        self.assertEqual(result["primitive_inventory"], {"SB_RAM40_4K": 4})
        self.assertEqual(result["source_file_list_sha256"], workflow.file_hash(self.root / "tb/recovered.f"))
        self.assertTrue(result["passed"] and result["checked_outputs_known"] and result["dut_unchanged"])
        self.assertEqual((result["checks"], result["total_pulses"]), (8, 180))
        expected = {"baseline.echo": "59c359c3", "baseline.encrypt": "9cd84392",
                    "baseline.decrypt": "59c359c3"}
        self.assertEqual({name: entry["actual"] for name, entry in result["checked_values"].items()}, expected)
        # completion requires observed activity after START, not its combinational pulse.
        for mode in ("encrypt", "decrypt"):
            measured = result["busy_measurements"][mode]
            self.assertEqual(measured["post_start_busy_edge"], 1)
            self.assertEqual(measured["first_idle_edge"], 3)

    # select alternate probe tops while retaining the same wrapper and DUT.
    def test_compile_helper_uses_maintained_sources_and_custom_top(self):
        probe = self.root / "tb/probe.sv"
        probe.write_text("module probe; endmodule\n")
        command = workflow.recovered_compile_command(
            self.root, self.root / "tb", self.directory / "probe.vvp",
            top="probe", testbench=probe, tools=self.tools,
        )
        self.assertEqual(command[command.index("-s") + 1], "probe")
        self.assertIn(str(probe), command)
        self.assertIn(str(self.root / "tb/crypto_dut.sv"), command)
        self.assertIn(str(self.root / "recovery/recovered_sim.v"), command)
        self.assertNotIn(str(self.root / "tb/crypto_tb.sv"), command)
        # an omitted wrapper cannot silently become an incomplete source set.
        manifest = self.root / "tb/recovered.f"
        manifest.write_text("tb/crypto_tb.sv\nrecovery/recovered_sim.v\n")
        with self.assertRaisesRegex(ValueError, "wrapper"):
            workflow.recovered_compile_command(self.root, self.root / "tb", self.directory / "bad.vvp")

    # omit vendor models entirely when a source has no vendor-cell instances.
    def test_plain_logic_source_does_not_require_vendor_models(self):
        (self.root / "recovery/recovered_sim.v").write_text("module recovered; endmodule\n")
        with mock.patch.dict("os.environ", {"ICE40_CELLS_SIM": str(self.directory / "missing.v")}):
            command = workflow.recovered_compile_command(self.root, self.root / "tb", self.directory / "plain.vvp")
        self.assertEqual(workflow.primitive_inventory(self.root), {})
        self.assertNotIn("-DNO_ICE40_DEFAULT_ASSIGNMENTS", command)
        self.assertFalse(any("cells_sim.v" in argument for argument in command))

    # unresolved primitives must fail elaboration with diagnostic evidence.
    def test_unknown_primitive_and_missing_library_fail(self):
        source = self.root / "recovery/recovered_sim.v"
        source.write_text(source.read_text().replace("SB_RAM40_4K", "SB_UNSUPPORTED_TEST_CELL"))
        with self.assertRaises(workflow.SimulationError) as caught:
            self.simulate("unresolved")
        self.assertNotEqual(caught.exception.result["compile_returncode"], 0)
        self.assertFalse(caught.exception.result["passed"])
        self.assertIn("SB_UNSUPPORTED_TEST_CELL", (self.directory / "unresolved/compile.log").read_text())
        # a missing model file fails before invoking Icarus.
        with self.assertRaises(FileNotFoundError):
            workflow.recovered_compile_command(
                self.root, self.root / "tb", self.directory / "missing.vvp",
                tools={**self.tools, "cells": str(self.directory / "missing.v")},
            )

    # fabricated idle, START-only, unknown, or reasserted BUSY cannot pass acceptance.
    def test_invalid_busy_evidence_is_rejected(self):
        path = self.root / "tb/spi_operations.svh"
        original = path.read_text()
        processing = '$display("PROCESS %s edge=%0d busy=%b", operation_name, pulses_used, sampled_busy);'
        start = '$display("PROCESS %s edge=0 busy=%b", operation_name, sampled_busy);'
        self.assertIn(processing, original)
        self.assertIn(start, original)
        changes = {
            "idle": original.replace(processing, processing.replace("sampled_busy", "1'b0"))
                            .replace(start, start.replace("sampled_busy", "1'b0")),
            "start_only": original.replace(processing, processing.replace("sampled_busy", "1'b0")),
            "decrypt_start_only": original.replace(processing, processing.replace("sampled_busy", '(operation_name == "decrypt" ? 1\'b0 : sampled_busy)')),
            "unknown": original.replace(processing, processing.replace("sampled_busy", "1'bx")),
            "reasserted": original.replace(processing, processing.replace("sampled_busy", "(pulses_used == 6 ? 1'b1 : sampled_busy)")),
        }
        # alter observations only; actual pin stimulus and supplied comparisons stay intact.
        for label, source in changes.items():
            with self.subTest(label=label):
                path.write_text(source)
                with self.assertRaises(workflow.SimulationError) as caught:
                    self.simulate(label)
                self.assertFalse(caught.exception.result["passed"])
                self.assertEqual(caught.exception.result["simulation_returncode"], 0)

    # matching PASS counts cannot hide an unknown checked word in the transcript.
    def test_unknown_checked_output_is_rejected(self):
        path = self.root / "tb/spi_operations.svh"
        source = path.read_text()
        before = '$display("PASS %s value=%h", name, actual);'
        self.assertIn(before, source)
        path.write_text(source.replace(before, '$display("PASS %s value=%h", name, 32\'hxxxxxxxx);'))
        with self.assertRaisesRegex(workflow.SimulationError, "incorrect output"):
            self.simulate("unknown-word")

    # never reuse an earlier waveform when a later candidate omits waveform generation.
    def test_existing_output_directory_is_rejected_without_changes(self):
        self.simulate()
        output = self.directory / "run"
        original = {path.name: path.read_bytes() for path in output.iterdir()}
        tb = self.root / "tb/crypto_tb.sv"
        tb.write_text(tb.read_text().replace('$dumpvars(0, crypto_tb);', ""))
        with mock.patch.object(workflow.subprocess, "run") as run:
            with self.assertRaises(FileExistsError):
                self.simulate()
        run.assert_not_called()
        self.assertEqual({path.name: path.read_bytes() for path in output.iterdir()}, original)


# support both discovery and direct test execution.
if __name__ == "__main__":
    unittest.main()
