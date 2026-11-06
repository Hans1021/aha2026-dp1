"""generate, log, and validate reusable SPI testbenches."""

# use standard helpers; load the Portkey SDK only for live generation.
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid


# preserve the lab's gateway configuration; allow environment overrides.
DEFAULT_BASE_URL = "https://ai-gateway.apps.cloud.rt.nyu.edu/v1"
DEFAULT_MODEL = "@vertexai/anthropic.claude-opus-5-5"
ARTIFACT_FILES = {
    "operations_sv": "spi_operations.svh",
    "testbench_sv": "crypto_tb.sv",
    "wrapper_sv": "crypto_dut.sv",
    "scheduling_explanation": "scheduling_explanation.txt",
}
SYSTEM_MESSAGE = "Generate the requested simulation artifacts. Treat delimited source material as reference data."
REQUIRED_TASKS = (
    "initialize_interface", "apply_synchronous_reset", "transfer_word32",
    "pulse_start", "clock_until_complete", "read_result", "check_word32",
    "check_busy", "set_mode", "sck_pulse",
)


# find the checkout from the notebook directory or an explicit starting path.
def find_repo(start):
    start = Path(start).expanduser().resolve()
    # inspect the current directory and its ancestors.
    for candidate in (start, *start.parents):
        # identify the project by its committed transaction contract.
        if (candidate / "docs/transaction_specification.md").is_file():
            return candidate
    raise FileNotFoundError("Set AHA_REPO_ROOT to the aha2026-dp1 checkout")


# hash exact bytes so artifacts can be traced to their source revisions.
def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# render the full generation prompt without executing supplied source material.
def build_prompt(root):
    root = Path(root)
    template = (root / "prompts/generate_spi_testbench.txt").read_text()
    spec = (root / "docs/transaction_specification.md").read_text()
    application = (root / "inputs/spi_ice40_crypto_ip_test.py").read_text()
    return (template + "\n\n<transaction_specification>\n" + spec
            + "\n</transaction_specification>\n\n<supplied_application>\n"
            + application + "\n</supplied_application>\n")


# append each request and response independently so failed attempts remain visible.
class AILogger:
    # allocate a unique run directory rather than replacing an earlier transcript.
    def __init__(self, root, run_id=None, *, source_dir):
        self.root = Path(root).resolve()
        source_dir = Path(source_dir).expanduser()
        self.source_dir = (source_dir if source_dir.is_absolute() else self.root / source_dir).resolve()
        # derive the log folder from an existing source directory in this checkout.
        if not self.source_dir.is_relative_to(self.root) or not self.source_dir.is_dir():
            raise ValueError("source_dir must be an existing directory inside the repository")
        self.log_root = self.root / "ai_logs" / self.source_dir.name
        self.run_id = run_id or (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + "-" + uuid.uuid4().hex[:8])
        # reject path fragments supplied as run names.
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", self.run_id):
            raise ValueError("run_id must be a single directory name")
        self.run_dir = self.log_root / "runs" / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=False)
        self.events_path = self.run_dir / "events.jsonl"
        self._secrets = set()

    # redact explicit keys as well as environment-provided credentials.
    def register_secret(self, secret):
        # ignore absent credentials while retaining earlier keys for redaction.
        if secret:
            self._secrets.add(secret)

    # remove configured credentials even if a remote error echoes their values.
    def _redact(self, value):
        text = json.dumps(value, ensure_ascii=False)
        secrets = self._secrets | {os.environ.get(name) for name in
                                   ("PORTKEY_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY")}
        # redact credential values without recording request headers.
        for secret in sorted(filter(None, secrets), key=len, reverse=True):
            # escape secrets exactly as JSON encodes them.
            if secret:
                text = text.replace(json.dumps(secret, ensure_ascii=False)[1:-1], "[redacted]")
        return json.loads(text)

    # persist each event before continuing generation or validation.
    def log_event(self, kind, **fields):
        event = self._redact({
            "kind": kind, "event_id": uuid.uuid4().hex,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id, **fields,
        })
        # flush completed lines so an interrupted request retains its prompt.
        with self.events_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return event


# require explicit artifact fields; never execute model-generated Python.
def parse_response(text):
    # accept a single JSON fence while rejecting surrounding explanation text.
    if text.strip().startswith("```json") and text.strip().endswith("```"):
        text = text.strip()[7:-3].strip()
    payload = json.loads(text)
    # reject omissions, extra fields, and empty or oversized artifacts.
    if not isinstance(payload, dict) or set(payload) != set(ARTIFACT_FILES):
        raise ValueError("Response must contain exactly the four requested artifact fields")
    # check each string before it becomes a file.
    for name, content in payload.items():
        # bound model output size and reject non-text payloads.
        if not isinstance(content, str) or not content.strip() or len(content) > 500_000:
            raise ValueError(f"Invalid artifact: {name}")
    return payload


# match the lab's Portkey client while preserving an audit for every SDK call.
class LLMClient:
    # defer SDK creation so replay and configuration work without an API key.
    def __init__(self, api_key=None, model=None, provider="portkey", *, logger,
                 timeout_s=120, sdk_factory=None):
        # keep the provider explicit and network waits bounded.
        if provider != "portkey":
            raise ValueError(f"Unsupported provider: {provider}")
        if not 0 < timeout_s <= 300:
            raise ValueError("Use a timeout in (0, 300] seconds")
        self.provider = provider
        self.model = model or os.environ.get("PORTKEY_MODEL") or DEFAULT_MODEL
        self.api_key = api_key or os.environ.get("PORTKEY_API_KEY")
        self.base_url = os.environ.get("PORTKEY_BASE_URL", DEFAULT_BASE_URL)
        self.logger = logger
        self.logger.register_secret(self.api_key)
        self.timeout_s = timeout_s
        self.sdk_factory = sdk_factory
        self.client = None
        self.use_temperature = True
        self.last_request_id = None

    # report local credential configuration without contacting the gateway.
    def is_available(self):
        return bool(self.api_key and self.api_key.strip())

    # construct one reusable SDK client with its hidden retries disabled.
    def _get_client(self):
        # retain the same connection pool across generation and repair attempts.
        if self.client is None:
            factory = self.sdk_factory
            # defer the optional dependency until live generation is requested.
            if factory is None:
                from portkey_ai import Portkey
                factory = Portkey
            self.client = factory(base_url=self.base_url, api_key=self.api_key,
                                  timeout=self.timeout_s, max_retries=0)
            # Portkey 2.3.4 resets retries on its nested chat client.
            self.client.openai_client.max_retries = 0
        return self.client

    # retry only an explicit unsupported-temperature client error.
    @staticmethod
    def _temperature_rejected(error):
        status = getattr(error, "status_code", None)
        # SDK HTTP errors normally expose both status_code and response.
        if status is None:
            status = getattr(getattr(error, "response", None), "status_code", None)
        message = str(error).lower()
        unsupported = re.search(r"unsupported|not supported|does not support|not allowed|not permitted|only.{0,40}default", message)
        # auth, server, and transport failures must not trigger a parameter retry.
        return ("temperature" in message and
                ((status in (400, 422) and bool(unsupported)) or
                 (isinstance(error, TypeError) and "unexpected keyword" in message)))

    # generate text with the lab's interface and one logged parameter retry.
    def generate(self, prompt, system_prompt=None, temperature=0.0, max_tokens=16000, *, seed=None):
        messages = []
        # include the optional system prompt exactly as supplied.
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        kwargs = {"model": self.model, "messages": messages,
                  "max_tokens": max_tokens, "stream": False, "timeout": self.timeout_s}
        # remember model compatibility across later calls in the same notebook.
        if self.use_temperature:
            kwargs["temperature"] = temperature
        # limit fallback to one retry and log each actual SDK attempt separately.
        for attempt in range(2):
            request_id = uuid.uuid4().hex
            self.last_request_id = request_id
            parameters = {key: value for key, value in kwargs.items() if key != "messages"}
            self.logger.log_event("prompt", request_id=request_id, source="portkey_sdk", mode="live",
                                  messages=messages, parameters=parameters, base_url=self.base_url,
                                  timeout_s=self.timeout_s, simulation_seed=seed,
                                  sdk_max_retries=0, parameter_attempt=attempt + 1)
            # retain failed requests and responses before any fallback or parsing.
            try:
                # missing credentials are an error, never a switch to replay.
                if not self.is_available():
                    raise ValueError("Set PORTKEY_API_KEY before requesting live generation")
                response = self._get_client().chat.completions.create(**kwargs)
                raw = response.model_dump_json()
                self.logger.log_event("output", request_id=request_id, source="portkey_sdk",
                                      live_api_call=True, raw_format="sdk_model_dump_json", raw=raw)
                choice = response.choices[0]
                # reject incomplete or refused model output after saving it.
                if getattr(choice, "finish_reason", None) not in (None, "stop", "end_turn"):
                    raise ValueError("Generation did not finish normally; see the logged response")
                output_text = choice.message.content
                if not isinstance(output_text, str) or not output_text.strip():
                    raise ValueError("Generation returned no text; see the logged response")
                return output_text
            except Exception as error:
                error_response = getattr(error, "response", None)
                raw_error = getattr(error_response, "text", None)
                # preserve structured SDK error bodies when no HTTP text exists.
                if raw_error is None and getattr(error, "body", None) is not None:
                    raw_error = json.dumps(error.body)
                if raw_error is not None:
                    self.logger.log_event("output", request_id=request_id, source="portkey_sdk",
                                          live_api_call=True, raw_format="http_error",
                                          status_code=getattr(error, "status_code", None), raw=raw_error)
                retry = attempt == 0 and "temperature" in kwargs and self._temperature_rejected(error)
                event = self.logger.log_event("error", request_id=request_id,
                                              error_type=type(error).__name__, message=str(error),
                                              retry_without_temperature=retry)
                # omit temperature once and keep it omitted for subsequent calls.
                if retry:
                    self.use_temperature = False
                    kwargs.pop("temperature")
                    continue
                raise RuntimeError(f"Portkey request failed: {event['message']}") from None


# keep recorded replay explicit; all live SDK calls pass through LLMClient.
def request_generation(prompt, logger, mode="replay", replay_path=None, model=None,
                       temperature=0.0, seed=4313, timeout_s=120, client=None):
    # share a client across repair attempts to retain its temperature preference.
    if mode == "live":
        client = client or LLMClient(model=model, logger=logger, timeout_s=timeout_s)
        # prevent a caller from accidentally placing responses in another run's log.
        if client.logger is not logger:
            raise ValueError("The client and request must use the same AI logger")
        output_text = client.generate(prompt, system_prompt=SYSTEM_MESSAGE,
                                      temperature=temperature, max_tokens=16000, seed=seed)
        # link schema failures to the already logged model response.
        try:
            return parse_response(output_text)
        except Exception as error:
            logger.log_event("error", request_id=client.last_request_id,
                             error_type=type(error).__name__, message=str(error))
            raise
    messages = [{"role": "system", "content": SYSTEM_MESSAGE}, {"role": "user", "content": prompt}]
    source = "recorded_response" if replay_path else "recorded_codex_generation"
    request_id = uuid.uuid4().hex
    logger.log_event("prompt", request_id=request_id, source=source, mode=mode,
                     messages=messages, parameters={"simulation_seed": seed, "model": None})
    # log replay failures without importing the SDK or contacting the gateway.
    try:
        if mode != "replay" or not 0 < timeout_s <= 300:
            raise ValueError("Use replay/live and a timeout in (0, 300] seconds")
        path = Path(replay_path) if replay_path else logger.log_root / "bootstrap/response.json"
        original_request = path.with_name("request.json")
        # refuse to replay a transcript against a changed prompt.
        if original_request.is_file():
            original = json.loads(original_request.read_text())
            if original["messages"] != messages:
                raise ValueError("Replay prompt differs from the recorded request; select live generation")
        output_text = path.read_text()
        logger.log_event("output", request_id=request_id, source=source,
                         live_api_call=False, replay_path=str(path),
                         response_sha256=file_hash(path), raw=output_text)
        return parse_response(output_text)
    except Exception as error:
        logger.log_event("error", request_id=request_id, error_type=type(error).__name__, message=str(error))
        raise


# strip comments for conservative source checks; this is not a Verilog proof.
def _code_only(source):
    # preserve strings so quoted comment markers cannot hide executable code.
    tokens = r'"(?:\\.|[^"\\])*"|/\*.*?\*/|//[^\n]*'
    return re.sub(tokens, lambda match: match.group() if match.group().startswith('"') else " ", source, flags=re.S)


# reject missing operations and external simulator actions before compilation.
def validate_artifacts(artifacts):
    parse_response(json.dumps(artifacts))
    sources = {key: _code_only(artifacts[key]) for key in ("operations_sv", "testbench_sv", "wrapper_sv")}
    scans = {key: re.sub(r'"(?:\\.|[^"\\])*"', '""', source) for key, source in sources.items()}
    allowed = {"$display", "$fatal", "$finish", "$dumpfile", "$dumpvars", "$sformatf", "$value$plusargs"}
    # inspect all generated source, including wrappers and preprocessor includes.
    for key, source in sources.items():
        system_tasks = set(re.findall(r"\$[A-Za-z_][A-Za-z0-9_$]*", scans[key]))
        # prevent generated code from invoking a shell or reading external data.
        if system_tasks - allowed:
            raise ValueError(f"Unsupported simulator system tasks: {sorted(system_tasks - allowed)}")
        includes = re.findall(r'^\s*`include\s+([^\n]+)', source, re.M)
        # only the known local operations header may be included.
        if any(value.strip() != '"spi_operations.svh"' for value in includes):
            raise ValueError("Only spi_operations.svh may be included")
        # prevent the candidate from overriding recovered signals or modules.
        if re.search(r"\b(?:force|release)\b", scans[key]):
            raise ValueError("Generated source must not force DUT signals")
        dump_calls = len(re.findall(r"\$dumpfile\s*\(", scans[key]))
        literal_calls = len(re.findall(r'\$dumpfile\s*\(\s*"waveforms\.vcd"\s*\)\s*;', source))
        # confine waveform creation to the current simulation attempt directory.
        if dump_calls != literal_calls or dump_calls != (1 if key == "testbench_sv" else 0):
            raise ValueError("Only one literal $dumpfile(\"waveforms.vcd\") in crypto_tb is allowed")
    # require the reusable API requested in the generation prompt.
    for task in REQUIRED_TASKS:
        # named tasks make later test sequences independent of implementation details.
        if not re.search(r"\btask\s+(?:automatic\s+)?" + task + r"\b", sources["operations_sv"]):
            raise ValueError(f"Missing reusable task: {task}")
    # require exactly the wrapper and testbench modules, never a replacement DUT.
    for key, expected in (("testbench_sv", ["crypto_tb"]), ("wrapper_sv", ["crypto_dut"]), ("operations_sv", [])):
        # constrain module names before linking the recovered design.
        if re.findall(r"\bmodule\s+(\w+)", scans[key]) != expected:
            raise ValueError(f"Unexpected modules in {key}")
    # permit SCK writes only in its pulse task and the documented initialization.
    operations = sources["operations_sv"]
    initialization = re.search(r"\btask\s+(?:automatic\s+)?initialize_interface\b.*?\bendtask\b", operations, re.S)
    initialization_code = re.sub(r'"(?:\\.|[^"\\])*"', '""', initialization.group())
    # only the single zero-valued initialization may bypass the pulse owner.
    if re.findall(r"\bSCK\s*(?:<=|=(?!=))\s*([^;]+);", initialization_code) != ["1'b0"]:
        raise ValueError("initialize_interface must initialize SCK to zero exactly once")
    exempt = re.sub(r"\btask\s+(?:automatic\s+)?(?:sck_pulse|initialize_interface)\b.*?\bendtask\b", "", operations, flags=re.S)
    # strip literal strings so diagnostic text does not look like a clock assignment.
    remainder = re.sub(r'"(?:\\.|[^"\\])*"', '""', exempt + sources["testbench_sv"] + sources["wrapper_sv"])
    # declaration-time initialization is allowed, but no other clock driver is.
    remainder = re.sub(r"\b(?:reg|logic)\s+SCK\s*=\s*1'b0\s*;", "", remainder)
    if re.search(r"\bSCK\s*(?:<=|=(?!=))", remainder):
        raise ValueError("SCK is assigned outside its pulse/initialization operations")


# materialize only fixed filenames in the current attempt directory.
def write_artifacts(artifacts, output_dir):
    validate_artifacts(artifacts)
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    # keep the model's scheduling rationale next to its source files.
    for key, filename in ARTIFACT_FILES.items():
        path = output_dir / filename
        path.write_text(artifacts[key], encoding="utf-8")
        paths[key] = path
    return paths


# discover installed tools without downloading or modifying the toolchain.
def discover_tools():
    suite = Path(os.environ.get("OSS_CAD_SUITE", "~/.local/opt/oss-cad-suite")).expanduser().resolve()
    tools = {}
    # prefer the explicitly selected or locally installed suite.
    for name in ("iverilog", "vvp"):
        path = suite / "bin" / name
        resolved = str(path) if path.is_file() else shutil.which(name)
        # show an actionable failure when a required executable is missing.
        if not resolved:
            raise FileNotFoundError(f"Install OSS CAD Suite or set PATH for {name}")
        tools[name] = resolved
    cells = Path(os.environ.get("ICE40_CELLS_SIM", str(suite / "share/yosys/ice40/cells_sim.v"))).expanduser().resolve()
    # require actual vendor models; never replace RAM behavior with a stub.
    if not cells.is_file():
        raise FileNotFoundError("Set ICE40_CELLS_SIM to Yosys share/yosys/ice40/cells_sim.v")
    tools["cells"] = str(cells)
    return tools


# count actual top-level SCK edges independently of the testbench summary.
def count_sck_edges(path, top="crypto_tb"):
    scope, symbol, value, rising = [], None, None, 0
    # stream VCD data instead of loading all internal-net activity at once.
    with Path(path).open() as stream:
        # identify the top-level signal, then count scalar changes.
        for line in stream:
            parts = line.split()
            # track scope to avoid confusing unrelated internal clocks.
            if line.startswith("$scope"):
                scope.append(parts[2])
            elif line.startswith("$upscope"):
                scope.pop()
            elif line.startswith("$var") and scope == [top] and parts[4] == "SCK":
                symbol = parts[3]
            elif symbol and line.strip() in ("0" + symbol, "1" + symbol, "x" + symbol, "z" + symbol):
                new = line[0]
                # count only real low-to-high transitions.
                if new == "1" and value == "0":
                    rising += 1
                value = new
    # missing clocks indicate an invalid or incomplete waveform.
    if symbol is None:
        raise ValueError(f"VCD does not contain {top}.SCK")
    return rising


# retain detailed diagnostics with a failing run result.
class SimulationError(RuntimeError):
    # attach structured results for the notebook's repair prompt.
    def __init__(self, message, result):
        super().__init__(message)
        self.result = result


# compile and simulate the recovered core with bounded subprocesses.
def run_simulation(root, generated_dir, output_dir, seed=4313, random_trials=8,
                   timeout_s=30, extra_plusargs=()):
    root, generated_dir, output_dir = map(lambda p: Path(p).resolve(), (root, generated_dir, output_dir))
    # reject invalid run parameters before invoking the toolchain.
    if not 0 <= seed <= 0xFFFFFFFF or not 0 <= random_trials <= 64 or not 0 < timeout_s <= 300:
        raise ValueError("Use a 32-bit seed, 0..64 random trials, and a bounded timeout")
    tools = discover_tools()
    output_dir.mkdir(parents=True, exist_ok=True)
    dut = root / "recovery/recovered_sim.v"
    original_hash = file_hash(dut)
    result = {"test_kind": "recovered_dut", "target": "crypto_tb",
              "passed": False, "seed": seed, "random_trials": random_trials,
              "dut_sha256": original_hash, "tools": tools, "output_dir": str(output_dir)}
    executable = output_dir / "simulation.vvp"
    commands = {
        "compile": [tools["iverilog"], "-g2012", "-s", "crypto_tb", "-I", str(generated_dir),
                    "-o", str(executable), str(generated_dir / "crypto_dut.sv"),
                    str(generated_dir / "crypto_tb.sv"), str(dut), tools["cells"]],
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
        matches = re.findall(r"^SUMMARY PASS checks=(\d+) baseline_pulses=(\d+) total_pulses=(\d+) seed=(\d+)$", output, re.M)
        # require one complete summary and the exact expected check count.
        if len(matches) != 1 or re.search(r"\b(?:FAIL|FATAL)\b", output):
            raise ValueError("Missing/duplicate summary or failure diagnostic")
        checks, baseline, total, observed_seed = map(int, matches[0])
        # validate both reported checks and their corresponding PASS records.
        if (checks, baseline, total, observed_seed) != (8 + 2 * random_trials, 180, 180 + 66 * random_trials, seed):
            raise ValueError("Summary differs from the requested transaction/check counts")
        if len(re.findall(r"^PASS\s", output, re.M)) != checks:
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
        result.update(passed=True, checks=checks, baseline_pulses=baseline, total_pulses=total,
                      waveform=str(waveform), processing_trace=trace)
    except Exception as error:
        result["error"] = str(error)
        raise SimulationError(str(error), result) from error
    finally:
        result["dut_unchanged"] = file_hash(dut) == original_hash
        (output_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    # protect source provenance even if a candidate somehow modified the input.
    if not result["dut_unchanged"]:
        raise SimulationError("Recovered DUT changed during validation", result)
    return result


# retain the previous response and measured failures in a bounded repair request.
def repair_prompt(original_prompt, artifacts, diagnostics):
    return (original_prompt + "\n\n<previous_response>\n" + json.dumps(artifacts)
            + "\n</previous_response>\n<validation_feedback>\n" + diagnostics[-16000:]
            + "\n</validation_feedback>\nRepair the generated testbench only. Keep the same JSON schema, "
            "transaction sequence, and expected values. Explain any scheduling correction.")
