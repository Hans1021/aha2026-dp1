# exercise logging, generation validation, and the recovered-core simulation flow.
import importlib.util
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock


# import the repository workflow without requiring a package installation.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import testbench_workflow as workflow


# provide complete schema fixtures without claiming executable RTL behavior.
def artifact_fields():
    return {
        "operations_sv": "operations fixture",
        "testbench_sv": "testbench fixture",
        "wrapper_sv": "wrapper fixture",
        "scheduling_explanation": "drive low; sample after the rising edge settles",
    }


# load the generated sources without substituting the recovered DUT or its outputs.
def source_artifacts():
    return {
        "operations_sv": (ROOT / "tb/spi_operations.svh").read_text(),
        "testbench_sv": (ROOT / "tb/crypto_tb.sv").read_text(),
        "wrapper_sv": (ROOT / "tb/crypto_dut.sv").read_text(),
        "scheduling_explanation": "drive while SCK is low; sample registered MISO after NBA settling",
    }


# model the Portkey SDK response and its raw JSON audit representation.
def model_response(content):
    payload = {"choices": [{"message": {"content": content}}]}
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content))],
        model_dump_json=lambda: json.dumps(payload),
    )


# expose provider diagnostics without importing the optional SDK during tests.
def provider_error(message, status):
    error = RuntimeError(message)
    error.status_code = status
    error.response = types.SimpleNamespace(status_code=status, text=message)
    return error


# inject a lazy SDK factory while retaining each completion call for assertions.
def sdk_fixture(*, response=None, side_effect=None):
    completion = mock.Mock(return_value=response, side_effect=side_effect)
    sdk = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=completion)),
        openai_client=types.SimpleNamespace(max_retries=1),
    )
    return mock.Mock(return_value=sdk), completion


# verify audit events and API failures without contacting a model provider.
class GenerationTests(unittest.TestCase):
    # isolate every generated log from tracked source and other test cases.
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source_dir = self.root / "tb"
        self.source_dir.mkdir()
        self.logger = workflow.AILogger(
            self.root, run_id="test-run", source_dir=self.source_dir
        )

    # read the append-only log after the operation being checked.
    def events(self):
        path = self.logger.run_dir / "events.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()]

    # retain a literal replay input so malformed responses remain inspectable.
    def replay_file(self, text):
        path = self.root / "response.json"
        path.write_text(text)
        return path

    # require all four strings and reject unknown, absent, or mistyped fields.
    def test_response_schema_is_strict(self):
        expected = artifact_fields()
        self.assertEqual(workflow.parse_response(json.dumps(expected)), expected)
        invalid = ["not JSON", "[]", json.dumps({**expected, "extra": "field"})]
        # check each missing or non-string field independently.
        for key in expected:
            missing = dict(expected)
            missing.pop(key)
            invalid.append(json.dumps(missing))
            invalid.append(json.dumps({**expected, key: None}))
            invalid.append(json.dumps({**expected, key: ""}))
        # reject every malformed response without repairing or inventing content.
        for text in invalid:
            with self.subTest(text=text), self.assertRaises(ValueError):
                workflow.parse_response(text)

    # confirm replay records the supplied prompt and source response without the SDK.
    def test_replay_logs_prompt_and_output(self):
        expected = artifact_fields()
        path = self.replay_file(json.dumps(expected))
        client = mock.Mock()
        client.generate.side_effect = AssertionError("replay called the model")
        actual = workflow.request_generation(
            "replay prompt", self.logger, replay_path=path, client=client
        )
        self.assertEqual(actual, expected)
        client.generate.assert_not_called()
        events = self.events()
        kinds = [event["kind"] for event in events]
        self.assertLess(kinds.index("prompt"), kinds.index("output"))
        serialized = json.dumps(events)
        self.assertIn("replay prompt", serialized)
        self.assertIn("operations fixture", serialized)
        self.assertIn("replay", serialized)

    # keep a malformed replay response before reporting its validation failure.
    def test_malformed_replay_is_logged_before_validation(self):
        path = self.replay_file("invalid model output")
        with self.assertRaises(ValueError):
            workflow.request_generation("bad replay", self.logger, replay_path=path)
        events = self.events()
        kinds = [event["kind"] for event in events]
        self.assertLess(kinds.index("prompt"), kinds.index("output"))
        self.assertLess(kinds.index("output"), kinds.index("error"))
        self.assertIn("invalid model output", json.dumps(events))

    # preserve the prompt and error when the selected replay artifact is missing.
    def test_missing_replay_does_not_fall_back(self):
        client = mock.Mock()
        client.generate.side_effect = AssertionError("missing replay called the model")
        with self.assertRaises(FileNotFoundError):
            workflow.request_generation(
                "missing replay", self.logger, replay_path=self.root / "absent.json",
                client=client,
            )
        client.generate.assert_not_called()
        kinds = [event["kind"] for event in self.events()]
        self.assertIn("prompt", kinds)
        self.assertIn("error", kinds)
        self.assertNotIn("output", kinds)

    # retain the raw SDK envelope before rejecting an absent completion choice.
    def test_malformed_sdk_response_is_logged_before_validation(self):
        response = types.SimpleNamespace(
            choices=[], model_dump_json=lambda: '{"choices": [], "id": "malformed-envelope"}'
        )
        factory, completion = sdk_fixture(response=response)
        client = workflow.LLMClient(
            api_key="unit-test-secret", logger=self.logger, sdk_factory=factory
        )
        with self.assertRaises((ValueError, RuntimeError, IndexError)):
            workflow.request_generation("bad envelope", self.logger, mode="live", client=client)
        completion.assert_called_once()
        events = self.events()
        kinds = [event["kind"] for event in events]
        self.assertLess(kinds.index("output"), kinds.index("error"))
        self.assertIn("malformed-envelope", json.dumps(events))

    # report absent credentials rather than silently falling back to replay.
    def test_live_requires_credentials_and_logs_the_failure(self):
        factory = mock.Mock(side_effect=AssertionError("unauthorized SDK initialization"))
        with mock.patch.dict(os.environ, {}, clear=True):
            client = workflow.LLMClient(logger=self.logger, sdk_factory=factory)
            self.assertFalse(client.is_available())
            with self.assertRaises((ValueError, RuntimeError)):
                workflow.request_generation(
                    "live prompt", self.logger, mode="live", client=client
                )
        factory.assert_not_called()
        kinds = [event["kind"] for event in self.events()]
        self.assertIn("prompt", kinds)
        self.assertIn("error", kinds)
        self.assertNotIn("output", kinds)

    # initialize the SDK lazily with bounded requests and reuse the lab-style client.
    def test_live_success_logs_output_and_configures_sdk(self):
        secret = "unit-test-only-portkey-secret"
        expected = artifact_fields()
        response = model_response(json.dumps(expected))
        factory, completion = sdk_fixture(response=response)
        environment = {
            "PORTKEY_API_KEY": secret, "PORTKEY_MODEL": "test-model",
            "PORTKEY_BASE_URL": "https://gateway.example.invalid/v1",
        }
        with mock.patch.dict(os.environ, environment):
            client = workflow.LLMClient(logger=self.logger, timeout_s=9, sdk_factory=factory)
            self.assertTrue(client.is_available())
            factory.assert_not_called()
            actual = workflow.request_generation(
                "live prompt", self.logger, mode="live", seed=4313,
                client=client,
            )
        self.assertEqual(actual, expected)
        factory.assert_called_once_with(
            base_url=environment["PORTKEY_BASE_URL"], api_key=secret, timeout=9, max_retries=0
        )
        completion.assert_called_once()
        arguments = completion.call_args.kwargs
        self.assertEqual(arguments["model"], "test-model")
        self.assertEqual(arguments["temperature"], 0.0)
        self.assertEqual(arguments["max_tokens"], 16000)
        self.assertEqual(arguments["timeout"], 9)
        self.assertEqual(client.client.openai_client.max_retries, 0)
        self.assertEqual(arguments["messages"], [
            {"role": "system", "content": workflow.SYSTEM_MESSAGE},
            {"role": "user", "content": "live prompt"},
        ])
        serialized = json.dumps(self.events())
        self.assertIn("live prompt", serialized)
        self.assertIn("operations fixture", serialized)
        self.assertNotIn(secret, serialized)

    # log both raw output and the schema error when an API response is malformed.
    def test_invalid_live_content_is_not_silently_repaired(self):
        factory, _ = sdk_fixture(response=model_response("not an artifact JSON object"))
        client = workflow.LLMClient(
            api_key="unit-test-secret", logger=self.logger, sdk_factory=factory
        )
        with self.assertRaises(ValueError):
            workflow.request_generation(
                "invalid live response", self.logger, mode="live", client=client
            )
        kinds = [event["kind"] for event in self.events()]
        self.assertLess(kinds.index("prompt"), kinds.index("output"))
        self.assertLess(kinds.index("output"), kinds.index("error"))
        self.assertIn("not an artifact JSON object", json.dumps(self.events()))

    # redact an explicit key even when it was never placed in the environment.
    def test_transport_failure_is_logged_and_redacted(self):
        secret = "unit-test-secret-echoed-by-transport"
        factory, completion = sdk_fixture(side_effect=TimeoutError(f"timeout for {secret}"))
        client = workflow.LLMClient(api_key=secret, logger=self.logger, sdk_factory=factory)
        with self.assertRaises((TimeoutError, RuntimeError)):
            workflow.request_generation(
                "timeout prompt", self.logger, mode="live", client=client
            )
        completion.assert_called_once()
        serialized = json.dumps(self.events())
        self.assertIn("timeout prompt", serialized)
        self.assertIn("error", serialized)
        self.assertNotIn(secret, serialized)

    # retain HTTP error bodies and redact credentials even in provider diagnostics.
    def test_http_error_body_is_logged_without_secrets(self):
        secret = "unit-test-secret-in-provider-error"
        factory, completion = sdk_fixture(
            side_effect=provider_error(f"unavailable: {secret}", 503)
        )
        client = workflow.LLMClient(api_key=secret, logger=self.logger, sdk_factory=factory)
        with self.assertRaises(RuntimeError):
            workflow.request_generation("HTTP failure", self.logger, mode="live", client=client)
        completion.assert_called_once()
        events = self.events()
        kinds = [event["kind"] for event in events]
        self.assertLess(kinds.index("output"), kinds.index("error"))
        self.assertIn("unavailable", json.dumps(events))
        self.assertNotIn(secret, json.dumps(events))

    # preserve explicit generation controls and redact keys in prompt and output.
    def test_direct_generate_uses_lab_interface_and_redacts_explicit_key(self):
        secret = "explicit-key-not-in-environment"
        factory, completion = sdk_fixture(response=model_response(f"answer echoed {secret}"))
        with mock.patch.dict(os.environ, {}, clear=True):
            client = workflow.LLMClient(
                api_key=secret, model="explicit-model", logger=self.logger, sdk_factory=factory
            )
            output = client.generate(
                f"prompt containing {secret}", system_prompt="system fixture",
                temperature=0.25, max_tokens=2345,
            )
        self.assertEqual(output, f"answer echoed {secret}")
        factory.assert_called_once_with(
            base_url=workflow.DEFAULT_BASE_URL, api_key=secret, timeout=120, max_retries=0
        )
        self.assertEqual(completion.call_args.kwargs, {
            "model": "explicit-model", "max_tokens": 2345, "temperature": 0.25,
            "stream": False, "timeout": 120,
            "messages": [
                {"role": "system", "content": "system fixture"},
                {"role": "user", "content": f"prompt containing {secret}"},
            ],
        })
        self.assertNotIn(secret, json.dumps(self.events()))

    # log the rejected request and one compatibility retry, then reuse that decision.
    def test_unsupported_temperature_retries_once_and_persists(self):
        factory, completion = sdk_fixture(side_effect=[
            provider_error("temperature is not supported for this model", 400),
            model_response("first answer"), model_response("second answer"),
        ])
        client = workflow.LLMClient(
            api_key="unit-test-secret", logger=self.logger, sdk_factory=factory
        )
        self.assertEqual(client.generate("first prompt"), "first answer")
        self.assertEqual(client.generate("second prompt"), "second answer")
        factory.assert_called_once()
        self.assertEqual(completion.call_count, 3)
        self.assertIn("temperature", completion.call_args_list[0].kwargs)
        self.assertNotIn("temperature", completion.call_args_list[1].kwargs)
        self.assertNotIn("temperature", completion.call_args_list[2].kwargs)
        self.assertFalse(client.use_temperature)
        events = self.events()
        kinds = [event["kind"] for event in events]
        self.assertEqual(kinds.count("prompt"), 3)
        self.assertEqual(kinds.count("output"), 3)
        self.assertEqual(kinds.count("error"), 1)
        self.assertLess(kinds.index("output"), kinds.index("error"))
        # each response or failure must reference the request that produced it.
        request_ids = {event["request_id"] for event in events if event["kind"] == "prompt"}
        self.assertEqual(len(request_ids), 3)
        # ensure no retry response is attributed to another request.
        for event in events:
            # inspect audit records associated with SDK outcomes.
            if event["kind"] in {"output", "error"}:
                self.assertIn(event["request_id"], request_ids)

    # retry an explicitly unsupported SDK keyword without adding a system message.
    def test_unsupported_temperature_keyword_retries_once(self):
        factory, completion = sdk_fixture(side_effect=[
            TypeError("create() got an unexpected keyword argument 'temperature'"),
            model_response("answer"),
        ])
        with mock.patch.dict(os.environ, {}, clear=True):
            client = workflow.LLMClient(
                api_key="unit-test-secret", logger=self.logger, sdk_factory=factory
            )
            self.assertEqual(client.generate("plain prompt"), "answer")
        self.assertEqual(completion.call_count, 2)
        self.assertEqual(completion.call_args.kwargs["model"], workflow.DEFAULT_MODEL)
        self.assertEqual(completion.call_args.kwargs["messages"], [
            {"role": "user", "content": "plain prompt"}
        ])
        self.assertNotIn("temperature", completion.call_args.kwargs)

    # a failed compatibility retry must propagate without issuing a third request.
    def test_temperature_retry_is_bounded(self):
        factory, completion = sdk_fixture(side_effect=[
            provider_error("temperature is not supported", 422),
            provider_error("temperature is not supported", 422),
        ])
        client = workflow.LLMClient(
            api_key="unit-test-secret", logger=self.logger, sdk_factory=factory
        )
        with self.assertRaises(RuntimeError):
            client.generate("retry limit")
        self.assertEqual(completion.call_count, 2)
        kinds = [event["kind"] for event in self.events()]
        self.assertEqual(kinds.count("prompt"), 2)
        self.assertEqual(kinds.count("error"), 2)

    # mentioning temperature alone must not retry transport, auth, or server faults.
    def test_unrelated_errors_never_retry(self):
        errors = [
            TimeoutError("temperature request timed out"),
            provider_error("temperature is not supported: unauthorized", 401),
            provider_error("temperature is not supported: unavailable", 503),
            provider_error("temperature request has invalid credentials", 400),
        ]
        # exercise each failure independently with a fresh compatibility decision.
        for error in errors:
            with self.subTest(error=str(error)):
                factory, completion = sdk_fixture(side_effect=error)
                client = workflow.LLMClient(
                    api_key="unit-test-secret", logger=self.logger, sdk_factory=factory
                )
                with self.assertRaises((TimeoutError, RuntimeError)):
                    client.generate("no automatic retry")
                completion.assert_called_once()
                self.assertTrue(client.use_temperature)

    # reject unsupported providers and unbounded timeouts before touching the SDK.
    def test_invalid_client_configuration_is_rejected(self):
        cases = [{"provider": "other"}, {"timeout_s": 0}, {"timeout_s": -1},
                 {"timeout_s": float("inf")}, {"timeout_s": float("nan")}]
        # reject each invalid setting before any request can be issued.
        for options in cases:
            with self.subTest(options=options), self.assertRaises(ValueError):
                workflow.LLMClient(api_key="unit-test-secret", logger=self.logger, **options)

    # prevent explicitly named runs from overwriting previous audit records.
    def test_existing_run_id_cannot_overwrite_a_log(self):
        self.logger.log_event("validation", message="keep this event")
        original = (self.logger.run_dir / "events.jsonl").read_bytes()
        with self.assertRaises((FileExistsError, ValueError)):
            workflow.AILogger(self.root, run_id="test-run", source_dir=self.source_dir)
        self.assertEqual((self.logger.run_dir / "events.jsonl").read_bytes(), original)


# route each workflow's transcripts and replay inputs by its source directory.
class LogRoutingTests(unittest.TestCase):
    # isolate source folders and external paths from the repository.
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "repo"
        self.root.mkdir()
        # give independent workflows distinct source folders.
        for name in ("tb", "scripts"):
            (self.root / name).mkdir()

    # require a source directory rather than silently selecting a shared folder.
    def test_source_directory_is_required(self):
        with self.assertRaises(TypeError):
            workflow.AILogger(self.root)
        self.assertFalse((self.root / "ai_logs").exists())

    # accept repository-relative and absolute paths regardless of the process CWD.
    def test_log_directory_follows_source_from_each_working_directory(self):
        previous = Path.cwd()
        try:
            # exercise callers launched at either the checkout or notebook directory.
            for cwd in (self.root, self.root / "tb"):
                os.chdir(cwd)
                # preserve the source folder name for each supported path form.
                for name in ("tb", "scripts"):
                    for source in (Path(name), self.root / name):
                        with self.subTest(cwd=cwd, source=source):
                            logger = workflow.AILogger(self.root, source_dir=source)
                            expected = self.root / "ai_logs" / name
                            self.assertEqual(logger.source_dir, self.root / name)
                            self.assertEqual(logger.log_root, expected)
                            self.assertEqual(logger.run_dir.parent, expected / "runs")
                            logger.log_event("validation", message=name)
                            self.assertTrue(logger.events_path.is_file())
        finally:
            os.chdir(previous)
        self.assertFalse((self.root / "ai_logs/bao").exists())

    # reject external folders, traversal, missing paths, files, and symlink escapes.
    def test_invalid_source_directories_cannot_create_logs(self):
        outside = self.root.parent / "outside"
        outside.mkdir()
        source_file = self.root / "source.py"
        source_file.write_text("pass\n")
        escaped = self.root / "escaped"
        escaped.symlink_to(outside, target_is_directory=True)
        invalid = (outside, Path("../outside"), Path("missing"), source_file, escaped)
        # reject invalid locations before allocating any transcript directory.
        for source in invalid:
            with self.subTest(source=source), self.assertRaises(
                (ValueError, FileNotFoundError, NotADirectoryError)
            ):
                workflow.AILogger(self.root, source_dir=source)
        self.assertFalse((self.root / "ai_logs").exists())
        self.assertFalse((outside / "ai_logs").exists())

    # choose replay artifacts from the same folder that receives this workflow's logs.
    def test_default_replay_is_scoped_to_source_directory(self):
        client = mock.Mock()
        client.generate.side_effect = AssertionError("replay called the model")
        # distinguish source-specific replay results to expose cross-folder lookup.
        for name in ("tb", "scripts"):
            with self.subTest(source=name):
                logger = workflow.AILogger(self.root, source_dir=name)
                path = self.root / "ai_logs" / name / "bootstrap/response.json"
                path.parent.mkdir(parents=True)
                expected = {**artifact_fields(), "scheduling_explanation": f"fixture from {name}"}
                path.write_text(json.dumps(expected))
                actual = workflow.request_generation("replay prompt", logger, client=client)
                self.assertEqual(actual, expected)
                events = [json.loads(line) for line in logger.events_path.read_text().splitlines()]
                output = next(event for event in events if event["kind"] == "output")
                self.assertEqual(Path(output["replay_path"]), path)
        client.generate.assert_not_called()
        self.assertFalse((self.root / "ai_logs/bao").exists())

    # a missing source transcript must not use another workflow or the old shared folder.
    def test_missing_source_replay_does_not_use_other_folders(self):
        logger = workflow.AILogger(self.root, source_dir="scripts")
        # seed only unrelated locations to ensure both remain ineligible for replay.
        for name in ("tb", "bao"):
            path = self.root / "ai_logs" / name / "bootstrap/response.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(artifact_fields()))
        with self.assertRaises(FileNotFoundError):
            workflow.request_generation("replay prompt", logger)
        events = [json.loads(line) for line in logger.events_path.read_text().splitlines()]
        self.assertEqual([event["kind"] for event in events], ["prompt", "error"])

    # retain migrated bootstrap and run history without leaving the old folder behind.
    def test_repository_logs_have_moved_to_tb(self):
        self.assertFalse((ROOT / "ai_logs/bao").exists())
        self.assertTrue((ROOT / "ai_logs/tb/bootstrap/response.json").is_file())
        self.assertTrue(any((ROOT / "ai_logs/tb/runs").glob("*/events.jsonl")))


# exercise real SDK transport wiring without making network requests.
@unittest.skipUnless(
    importlib.util.find_spec("portkey_ai") and importlib.util.find_spec("httpx"),
    "optional Portkey SDK dependencies are not installed",
)
class SDKTransportTests(unittest.TestCase):
    # isolate request audit records from tracked repository files.
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        (root / "tb").mkdir()
        self.logger = workflow.AILogger(root, run_id="sdk-transport", source_dir="tb")

    # supply a real SDK over an entirely local HTTP transport.
    def client_for(self, handler, timeout_s):
        import httpx
        from portkey_ai import Portkey
        transport = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(transport.close)

        # pass the test transport through the SDK's public constructor.
        def factory(**kwargs):
            return Portkey(http_client=transport, **kwargs)

        return workflow.LLMClient(
            api_key="synthetic-test-key", model="sdk-test-model", logger=self.logger,
            timeout_s=timeout_s, sdk_factory=factory,
        )

    # prove hidden nested retries are disabled and all HTTP waits are bounded.
    def test_server_error_makes_one_request_with_configured_timeouts(self):
        import httpx
        requests = []

        # fail locally even when the provider's diagnostic mentions temperature.
        def handler(request):
            requests.append(request)
            return httpx.Response(503, json={"error": {
                "message": "temperature is not supported: gateway unavailable",
                "type": "server_error",
            }})

        client = self.client_for(handler, timeout_s=7)
        with self.assertRaises(RuntimeError):
            client.generate("offline server failure")
        self.assertEqual(len(requests), 1)
        self.assertEqual(client.client.openai_client.max_retries, 0)
        self.assertEqual(requests[0].extensions["timeout"], {
            "connect": 7, "read": 7, "write": 7, "pool": 7,
        })
        events = [json.loads(line) for line in self.logger.events_path.read_text().splitlines()]
        self.assertEqual([event["kind"] for event in events], ["prompt", "output", "error"])
        self.assertEqual(events[1]["status_code"], 503)

    # retain the real SDK's error and typed completion around one parameter retry.
    def test_temperature_retry_logs_both_real_sdk_responses(self):
        import httpx
        requests = []

        # reject temperature once, then return a typed chat completion.
        def handler(request):
            requests.append(request)
            # model an explicit unsupported-parameter response from the gateway.
            if len(requests) == 1:
                return httpx.Response(400, json={"error": {
                    "message": "temperature is not supported for this model",
                    "type": "invalid_request_error", "param": "temperature",
                }})
            return httpx.Response(200, json={
                "id": "offline-completion", "object": "chat.completion", "created": 0,
                "model": "sdk-test-model", "choices": [{
                    "index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "offline answer"},
                }],
            })

        client = self.client_for(handler, timeout_s=5)
        self.assertEqual(client.generate("offline compatibility"), "offline answer")
        self.assertEqual(len(requests), 2)
        self.assertIn("temperature", json.loads(requests[0].content))
        self.assertNotIn("temperature", json.loads(requests[1].content))
        # assert that the retry retains every timeout rather than relying on SDK defaults.
        for request in requests:
            self.assertEqual(request.extensions["timeout"], {
                "connect": 5, "read": 5, "write": 5, "pool": 5,
            })
        events = [json.loads(line) for line in self.logger.events_path.read_text().splitlines()]
        self.assertEqual([event["kind"] for event in events], [
            "prompt", "output", "error", "prompt", "output",
        ])
        self.assertEqual(events[1]["status_code"], 400)
        self.assertIn("offline-completion", events[-1]["raw"])
        self.assertIn("offline answer", events[-1]["raw"])


# validate notebook presentation and the source context supplied to generation.
class NotebookTests(unittest.TestCase):
    # locate the project from a nested working directory and reject unrelated roots.
    def test_repository_discovery(self):
        self.assertEqual(workflow.find_repo(ROOT / "inputs"), ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises((FileNotFoundError, ValueError, RuntimeError)):
                workflow.find_repo(Path(temporary))

    # preserve the application and contract in the rendered generation prompt.
    def test_prompt_contains_sources_and_safety_requirements(self):
        prompt = workflow.build_prompt(ROOT)
        self.assertIn((ROOT / "docs/transaction_specification.md").read_text(), prompt)
        self.assertIn((ROOT / "inputs/spi_ice40_crypto_ip_test.py").read_text(), prompt)
        # require the requested timing, failure, waveform, and reproducibility contract.
        for phrase in ("SCK", "MOSI", "X/Z", "seed", "timeout", "waveform", "scheduling"):
            with self.subTest(requirement=phrase):
                self.assertIn(phrase.lower(), prompt.lower())

    # a failed later request must not reuse an earlier attempt's model response.
    def test_bounded_repair_uses_only_current_attempt_feedback(self):
        notebook_path = ROOT / "tb/LLM_Aided_SPI_Testbench_Generation.ipynb"
        notebook = json.loads(notebook_path.read_text())
        source = next(
            "".join(cell["source"]) for cell in notebook["cells"]
            if cell["cell_type"] == "code" and "for attempt in range" in "".join(cell["source"])
        )
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "tb").mkdir()
            logger = workflow.AILogger(directory, source_dir="tb")
            factory, completion = sdk_fixture(side_effect=[
                model_response(json.dumps(artifact_fields())),
                TimeoutError("second attempt has no response"),
                TimeoutError("third attempt has no response"),
            ])
            client = workflow.LLMClient(
                api_key="unit-test-secret", logger=logger, sdk_factory=factory
            )
            # use real request/error logging while suppressing all network access.
            def ask_ai(prompt):
                return workflow.request_generation(
                    prompt, logger, mode="live", client=client
                )
            repair = mock.Mock(return_value="bounded repair prompt")
            namespace = {
                "generation_prompt": "initial generation prompt", "MAX_ATTEMPTS": 3,
                "RUN_DIR": directory / "attempts", "logger": logger, "json": json,
                "ask_ai": ask_ai, "validate_artifacts": workflow.validate_artifacts,
                "write_artifacts": workflow.write_artifacts, "repair_prompt": repair,
            }
            # the first invalid source and two request failures must exhaust exactly three attempts.
            with self.assertRaises(RuntimeError):
                exec(compile(source, str(notebook_path), "exec"), namespace)
            self.assertEqual(completion.call_count, 3)
            factory.assert_called_once()
            self.assertEqual(repair.call_count, 2)
            self.assertEqual(repair.call_args_list[0].args[1], artifact_fields())
            current_feedback = repair.call_args_list[1].args[1]
            self.assertIn("no response", json.dumps(current_feedback).lower())
            self.assertNotIn("operations fixture", json.dumps(current_feedback))

    # allow heading-only Markdown cells and executable code cells in every notebook.
    def test_notebook_markdown_contains_only_headings(self):
        notebooks = sorted(ROOT.rglob("*.ipynb"))
        self.assertIn(ROOT / "tb/LLM_Aided_SPI_Testbench_Generation.ipynb", notebooks)
        self.assertFalse((ROOT / "notebooks/Bao_Nguyen-LLM_Aided_SPI_Testbench_Generation.ipynb").exists())
        # inspect every saved cell; explanatory prose belongs in code comments or outputs.
        for path in notebooks:
            notebook = json.loads(path.read_text())
            self.assertEqual(notebook["nbformat"], 4)
            self.assertTrue(any(cell["cell_type"] == "code" for cell in notebook["cells"]))
            # reject text paragraphs, list items, and raw text cells.
            for cell in notebook["cells"]:
                self.assertIn(cell["cell_type"], ("code", "markdown"))
                if cell["cell_type"] == "markdown":
                    source = "".join(cell["source"])
                    for line in source.splitlines():
                        if line.strip():
                            self.assertRegex(line, r"^#{1,4} \S")


# reject malformed generated code before compiling the recovered core.
class ArtifactValidationTests(unittest.TestCase):
    # accept the generated sources before checking deliberate invalid variants.
    def setUp(self):
        self.artifacts = source_artifacts()
        workflow.validate_artifacts(self.artifacts)

    # reject simulator commands that could run programs or read external files.
    def test_unsupported_simulator_actions_are_rejected(self):
        variants = [
            '$system("echo forbidden");',
            '$fopen("external.txt", "r");',
            '`include "external.svh"',
            "force BUSY = 1'b0;",
        ]
        # apply each invalid action to an otherwise valid source bundle.
        for addition in variants:
            with self.subTest(addition=addition):
                artifacts = dict(self.artifacts)
                artifacts["testbench_sv"] += "\n" + addition + "\n"
                with self.assertRaises(ValueError):
                    workflow.validate_artifacts(artifacts)

    # strings containing comment markers must not hide executable system tasks.
    def test_comment_markers_in_strings_do_not_hide_system_tasks(self):
        artifacts = dict(self.artifacts)
        artifacts["testbench_sv"] += '\ninitial begin $display("//"); $system("forbidden"); end\n'
        with self.assertRaises(ValueError):
            workflow.validate_artifacts(artifacts)

    # limit model-generated waveform output to the current attempt directory.
    def test_external_waveform_paths_are_rejected(self):
        cases = ['"../outside.vcd"', '"/tmp/outside.vcd"', "dynamic_filename"]
        # reject absolute, parent-relative, and computed waveform destinations.
        for destination in cases:
            with self.subTest(destination=destination):
                artifacts = dict(self.artifacts)
                artifacts["testbench_sv"] = artifacts["testbench_sv"].replace(
                    '$dumpfile("waveforms.vcd");', f"$dumpfile({destination});", 1
                )
                with self.assertRaises(ValueError):
                    workflow.validate_artifacts(artifacts)

    # initialization may set SCK low once but must not emit any clock pulse.
    def test_initialization_cannot_emit_an_extra_sck_pulse(self):
        artifacts = dict(self.artifacts)
        operation = re.search(
            r"task automatic initialize_interface\b.*?endtask",
            artifacts["operations_sv"], re.S,
        )
        self.assertIsNotNone(operation)
        before = operation.group(0)
        after = before.replace("SCK = 1'b0;", "SCK = 1'b0; #1 SCK = 1'b1; #1 SCK = 1'b0;", 1)
        self.assertNotEqual(before, after)
        artifacts["operations_sv"] = artifacts["operations_sv"].replace(before, after, 1)
        with self.assertRaises(ValueError):
            workflow.validate_artifacts(artifacts)

    # require every reusable operation and prevent a second clock writer.
    def test_missing_operation_and_second_sck_owner_are_rejected(self):
        missing = dict(self.artifacts)
        missing["operations_sv"] = missing["operations_sv"].replace(
            "task automatic transfer_word32(", "task automatic missing_transfer("
        )
        self.assertNotEqual(missing, self.artifacts)
        with self.assertRaises(ValueError):
            workflow.validate_artifacts(missing)
        second_clock = dict(self.artifacts)
        second_clock["testbench_sv"] += "\nalways #10000 SCK = ~SCK;\n"
        with self.assertRaises(ValueError):
            workflow.validate_artifacts(second_clock)


# run the real recovered design and independently inspect saved validation evidence.
class RecoveredSimulationTests(unittest.TestCase):
    # skip only when the external simulator/model dependency is unavailable.
    @classmethod
    def setUpClass(cls):
        try:
            workflow.discover_tools()
        except FileNotFoundError as error:
            raise unittest.SkipTest(str(error)) from error

    # keep generated candidates and waveforms outside the repository.
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    # materialize a candidate and run it with finite process timeouts.
    def simulate(self, label, artifacts=None, seed=4313, random_trials=0):
        artifacts = artifacts or source_artifacts()
        generated = self.directory / label / "generated"
        output = self.directory / label / "output"
        workflow.write_artifacts(artifacts, generated)
        result = workflow.run_simulation(
            ROOT, generated, output, seed=seed, random_trials=random_trials,
            timeout_s=15,
        )
        return result, output

    # verify known echo/encryption/decryption values against the actual recovered core.
    def test_real_baseline_and_seeded_echoes(self):
        result, output = self.simulate("baseline", random_trials=2)
        self.assertTrue(result["passed"])
        self.assertTrue(result["dut_unchanged"])
        self.assertEqual(result["checks"], 12)
        self.assertEqual(result["baseline_pulses"], 180)
        self.assertEqual(result["total_pulses"], 312)
        self.assertEqual(result["seed"], 4313)
        self.assertEqual(workflow.count_sck_edges(output / "waveforms.vcd"), 312)
        transcript = (output / "simulation.log").read_text()
        self.assertRegex(transcript, r"PASS baseline\.encrypt value=9cd84392")
        self.assertRegex(transcript, r"PASS baseline\.decrypt value=59c359c3")
        self.assertEqual(len(result["processing_trace"]), 16)

    # equal seeds reproduce stimulus while different seeds change the extra echo words.
    def test_seeded_words_are_reproducible(self):
        words = []
        # compare actual simulator output from three independent runs.
        for label, seed in (("first", 4313), ("repeat", 4313), ("different", 4314)):
            result, output = self.simulate(label, seed=seed, random_trials=3)
            self.assertTrue(result["passed"])
            transcript = (output / "simulation.log").read_text()
            words.append(re.findall(r"^TEST random_echo\.\d+ word=([0-9a-f]{8})$", transcript, re.M))
        self.assertEqual(len(words[0]), 3)
        self.assertEqual(words[0], words[1])
        self.assertNotEqual(words[0], words[2])

    # preserve partial stdout/stderr when the host enforces its wall-clock timeout.
    def test_subprocess_timeout_preserves_failure_evidence(self):
        generated = self.directory / "timeout/generated"
        output = self.directory / "timeout/output"
        workflow.write_artifacts(source_artifacts(), generated)
        compiled = subprocess.CompletedProcess([], 0, stdout="compiled\n", stderr="")
        expired = subprocess.TimeoutExpired(
            ["vvp"], 0.1, output=b"partial simulator output\n", stderr=b"partial stderr\n"
        )
        # mock a process timeout only; no simulated circuit success is fabricated.
        with mock.patch.object(workflow.subprocess, "run", side_effect=[compiled, expired]) as run:
            with self.assertRaises(workflow.SimulationError) as caught:
                workflow.run_simulation(ROOT, generated, output, random_trials=0, timeout_s=0.1)
        self.assertFalse(caught.exception.result["passed"])
        self.assertTrue(caught.exception.result["dut_unchanged"])
        self.assertEqual(run.call_count, 2)
        self.assertTrue(all(call.kwargs["timeout"] == 0.1 for call in run.call_args_list))
        transcript = (output / "simulation.log").read_text()
        self.assertIn("partial simulator output", transcript)
        self.assertIn("partial stderr", transcript)
        self.assertIn("wall-clock timeout", transcript)
        self.assertFalse(json.loads((output / "result.json").read_text())["passed"])

    # altering only an expected result must fail the real simulation with a named diagnostic.
    def test_wrong_expected_ciphertext_fails(self):
        artifacts = source_artifacts()
        before = 'check_word32("baseline.encrypt", received, CIPHERTEXT);'
        after = 'check_word32("baseline.encrypt", received, 32\'h9cd84393);'
        self.assertIn(before, artifacts["testbench_sv"])
        artifacts["testbench_sv"] = artifacts["testbench_sv"].replace(before, after, 1)
        with self.assertRaises(workflow.SimulationError) as caught:
            self.simulate("wrong_ciphertext", artifacts)
        self.assertFalse(caught.exception.result["passed"])
        self.assertTrue(caught.exception.result["dut_unchanged"])
        transcript = (self.directory / "wrong_ciphertext/output/simulation.log").read_text()
        self.assertIn("FAIL baseline.encrypt", transcript)
        self.assertNotIn("SUMMARY PASS", transcript)

    # a fabricated duplicate summary cannot turn successful simulator exit into validation success.
    def test_duplicate_summary_is_rejected(self):
        artifacts = source_artifacts()
        duplicate = '$display("SUMMARY PASS checks=8 baseline_pulses=180 total_pulses=180 seed=4313");'
        artifacts["testbench_sv"] = artifacts["testbench_sv"].replace(
            "$finish;", duplicate + "\n        $finish;", 1
        )
        with self.assertRaises(workflow.SimulationError):
            self.simulate("duplicate_summary", artifacts)
        transcript = (self.directory / "duplicate_summary/output/simulation.log").read_text()
        self.assertEqual(len(re.findall(r"^SUMMARY PASS", transcript, re.M)), 2)

    # require the waveform artifact even when the simulator prints a passing summary.
    def test_missing_waveform_is_rejected(self):
        artifacts = source_artifacts()
        self.assertIn("$dumpvars(0, crypto_tb);", artifacts["testbench_sv"])
        artifacts["testbench_sv"] = artifacts["testbench_sv"].replace(
            "$dumpvars(0, crypto_tb);", "", 1
        )
        with self.assertRaises(workflow.SimulationError):
            self.simulate("missing_waveform", artifacts)
        transcript = (self.directory / "missing_waveform/output/simulation.log").read_text()
        self.assertIn("SUMMARY PASS", transcript)


# compile operation-level failure probes with a bounded simulator process.
class OperationFailureTests(unittest.TestCase):
    # locate Icarus in PATH or the user installation used by the notebook.
    @classmethod
    def setUpClass(cls):
        suite_bin = Path.home() / ".local/opt/oss-cad-suite/bin"
        cls.iverilog = shutil.which("iverilog") or str(suite_bin / "iverilog")
        cls.vvp = shutil.which("vvp") or str(suite_bin / "vvp")
        if not Path(cls.iverilog).is_file() or not Path(cls.vvp).is_file():
            raise unittest.SkipTest("OSS CAD Suite/Icarus is required for simulation checks")

    # run only checker/control operations; these probes do not model a crypto oracle.
    def assert_operation_fails(self, statements, diagnostic):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            shutil.copy2(ROOT / "tb/spi_operations.svh", directory / "spi_operations.svh")
            source = r"""`timescale 1ns/1ps
/* exercise one reusable operation's failure path. */
module crypto_tb;
    /* provide interface signals without a substitute cryptographic model. */
    reg SCK, RST_N, MOSI, NORM_CS_N, START, ENC_DEC;
    reg busy_driver = 1'b0;
    reg miso_driver = 1'b0;
    wire BUSY = busy_driver;
    wire MISO = miso_driver;
    wire ICE_LED = busy_driver;
    integer pulses_used;
    reg sampled_miso, sampled_busy;
    reg [31:0] received;
    `include "spi_operations.svh"
    /* require the chosen operation to terminate with an explicit failure. */
    initial begin
        initialize_interface();
        STATEMENTS
        $display("UNEXPECTED_SUCCESS");
        $finish;
    end
    /* prevent a broken operation from hanging the test indefinitely. */
    initial begin
        #1000000;
        $fatal(1, "PROBE_WATCHDOG");
    end
endmodule
""".replace("STATEMENTS", statements)
            testbench = directory / "probe.sv"
            testbench.write_text(source)
            binary = directory / "probe.vvp"
            compiled = subprocess.run(
                [self.iverilog, "-g2012", "-I", str(directory), "-s", "crypto_tb",
                 "-o", str(binary), str(testbench)],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            executed = subprocess.run(
                [self.vvp, str(binary)], cwd=directory, capture_output=True,
                text=True, timeout=10,
            )
            output = executed.stdout + executed.stderr
            self.assertNotEqual(executed.returncode, 0, output)
            self.assertNotIn("UNEXPECTED_SUCCESS", output)
            self.assertNotIn("PROBE_WATCHDOG", output)
            self.assertRegex(output.lower(), diagnostic.lower())

    # ensure a wrong known value cannot pass the data checker.
    def test_word_mismatch_fails(self):
        self.assert_operation_fails(
            'check_word32("negative_mismatch", 32\'h12345678, 32\'h12345679);',
            "negative_mismatch",
        )

    # reject X/Z on either side of a comparison, even when both sides agree.
    def test_unknown_data_or_expectation_fails(self):
        cases = [
            ("32'hxxxxxxxx", "32'h00000000"),
            ("32'hzzzzzzzz", "32'h00000000"),
            ("32'h00000000", "32'hxxxxxxxx"),
            ("32'hxxxxxxxx", "32'hxxxxxxxx"),
            ("32'hzzzzzzzz", "32'hzzzzzzzz"),
        ]
        # check each four-state input separately to expose accidental equality passes.
        for actual, expected in cases:
            with self.subTest(actual=actual, expected=expected):
                self.assert_operation_fails(
                    f'check_word32("negative_unknown", {actual}, {expected});',
                    "negative_unknown",
                )

    # ensure unknown completion status fails instead of becoming a false completion.
    def test_unknown_busy_fails(self):
        self.assert_operation_fails(
            "busy_driver = 1'bx; sck_pulse(sampled_miso, sampled_busy);", "busy"
        )

    # prove bounded completion actively clocks and fails when BUSY stays asserted.
    def test_stuck_busy_times_out(self):
        self.assert_operation_fails(
            'busy_driver = 1\'b1; clock_until_complete("negative_timeout", 3, 1\'b0, pulses_used);',
            "timeout",
        )

    # reject unknown serial input rather than silently assembling a known result.
    def test_unknown_miso_fails(self):
        self.assert_operation_fails(
            'miso_driver = 1\'bz; transfer_word32("negative_miso", 32\'h59c359c3, received);',
            "miso|unknown|negative_miso",
        )


# run the stdlib suite from either unittest discovery or direct invocation.
if __name__ == "__main__":
    unittest.main()
