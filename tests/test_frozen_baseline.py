"""reject drift and accidental replacement of frozen baseline evidence."""

# isolate integrity checks from real baseline creation and installed tools.
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts import frozen_baseline as baseline
from scripts.testbench_workflow import file_hash


# exercise immutable input and evidence checks with small local fixtures.
class FrozenBaselineTests(unittest.TestCase):
    # use a minimal reference without modifying the repository's actual baseline.
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.reference = self.root / baseline.REFERENCE
        self.reference.mkdir(parents=True)
        (self.root / "source.sv").write_text("module frozen; endmodule\n")
        (self.reference / "evidence.log").write_text("PASS fixture\n")
        self.environment = {"iverilog": "fixture", "cells_sim_sha256": "model-fixture"}
        self.manifest = {
            "schema_version": 1, "reference_tag": baseline.REFERENCE_TAG,
            "seed": 4313, "random_trials": 0,
            "sources": {"source.sv": file_hash(self.root / "source.sv")},
            "environment": self.environment,
            "evidence": {"evidence.log": file_hash(self.reference / "evidence.log")},
            "acceptance": {name: True for name in baseline.ACCEPTANCE_CRITERIA},
        }
        self.write_manifest()
        # suppress Git lookup only; real filesystem digests still determine acceptance.
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(baseline, "SOURCE_PATHS", ("source.sv",)).start()
        mock.patch.object(baseline, "EVIDENCE_PATHS", ("evidence.log",)).start()
        mock.patch.object(baseline, "environment_identity", return_value=self.environment).start()
        self.git = mock.patch.object(
            baseline.subprocess, "run",
            return_value=subprocess.CompletedProcess([], 128, stdout="", stderr="no tag"),
        ).start()

    # preserve the intended fixture manifest before each independent mutation.
    def write_manifest(self):
        (self.reference / "manifest.json").write_text(json.dumps(self.manifest))

    # verification reads the reference without changing any stored evidence.
    def test_reference_verifies_without_rewriting(self):
        before = {path.name: path.read_bytes() for path in self.reference.iterdir()}
        self.assertEqual(baseline.verify_reference(self.root, self.reference), self.manifest)
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.reference.iterdir()})

    # source edits must fail rather than silently creating a new baseline.
    def test_changed_source_fails(self):
        (self.root / "source.sv").write_text("module changed; endmodule\n")
        with self.assertRaisesRegex(ValueError, "inputs changed"):
            baseline.verify_reference(self.root, self.reference)

    # log edits cannot masquerade as previously accepted simulation results.
    def test_changed_evidence_fails(self):
        (self.reference / "evidence.log").write_text("PASS forged\n")
        with self.assertRaisesRegex(ValueError, "evidence changed"):
            baseline.verify_reference(self.root, self.reference)

    # reject differing model identities and omitted pinned inputs.
    def test_changed_environment_or_manifest_fails(self):
        self.manifest["environment"] = {"iverilog": "different"}
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "identity changed"):
            baseline.verify_reference(self.root, self.reference)
        self.manifest["sources"] = {}
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "Invalid"):
            baseline.verify_reference(self.root, self.reference)

    # a present Git tag anchors the manifest even if local hashes were replaced.
    def test_tagged_manifest_difference_fails(self):
        self.git.return_value = subprocess.CompletedProcess([], 0, stdout="{}", stderr="")
        with self.assertRaisesRegex(ValueError, "Git tag"):
            baseline.verify_reference(self.root, self.reference)

    # absent logs or malformed hashes must fail even before a Git tag exists.
    def test_omitted_evidence_or_invalid_digest_fails(self):
        self.manifest["evidence"] = {}
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "Invalid"):
            baseline.verify_reference(self.root, self.reference)
        self.manifest["evidence"] = {"evidence.log": "not-a-digest"}
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            baseline.verify_reference(self.root, self.reference)

    # capture must never replace a baseline directory, even before running tools.
    def test_second_capture_is_rejected(self):
        with self.assertRaisesRegex(FileExistsError, "never overwrite"):
            baseline.run_baseline(self.root, self.root / "new-run", record=True)
        self.assertFalse((self.root / "new-run").exists())


# guard the actual reference on later changes, even without simulator execution.
class CommittedBaselineTests(unittest.TestCase):
    # pinned source and recorded evidence changes must fail the normal test suite.
    def test_committed_baseline_has_no_source_or_evidence_drift(self):
        root = Path(__file__).resolve().parents[1]
        reference = root / baseline.REFERENCE
        manifest = json.loads((reference / "manifest.json").read_text())
        self.assertEqual(manifest["sources"], baseline.source_hashes(root))
        self.assertEqual(set(manifest["evidence"]), set(baseline.EVIDENCE_PATHS))
        # verify every frozen log and result without rewriting its contents.
        for name, digest in manifest["evidence"].items():
            with self.subTest(evidence=name):
                self.assertEqual(file_hash(reference / name), digest)
        self.assertEqual(manifest["acceptance"], {name: True for name in baseline.ACCEPTANCE_CRITERIA})


# allow unittest discovery and explicit execution.
if __name__ == "__main__":
    unittest.main()
