"""validation helpers for the SPI notebook workflow."""

# import only the dependencies used by this module.
import json
import re
from .common import ARTIFACT_FILES, REQUIRED_TASKS


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


# strip comments for conservative source checks; this is not a Verilog proof.
def _code_only(source):
    # preserve strings so quoted comment markers cannot hide executable code.
    tokens = r'"(?:\\.|[^"\\])*"|/\*.*?\*/|//[^\n]*'
    return re.sub(tokens, lambda match: match.group() if match.group().startswith('"') else " ", source, flags=re.S)


# reject missing operations and external simulator actions before compilation.
def validate_artifacts(artifacts):
    parse_response(json.dumps(artifacts))
    # enforce the requested comment-free HDL output.
    for key in ("operations_sv", "testbench_sv", "wrapper_sv"):
        if _code_only(artifacts[key]) != artifacts[key]:
            raise ValueError("Generated HDL must not contain comments")
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
    # include the shared operations once inside the application module.
    application = sources["testbench_sv"]
    application_scan = re.sub(r'"(?:\\.|[^"\\])*"',
                              lambda match: " " * len(match.group()), application)
    module_start = re.search(r"\bmodule\s+crypto_tb\b[^;]*;", application_scan)
    module_end = re.search(r"\bendmodule\b", application_scan)
    header_includes = list(re.finditer(r"`include\b[^\n]*", application_scan))
    if (len(header_includes) != 1 or not module_start or not module_end
            or not module_start.end() <= header_includes[0].start() < module_end.start()):
        raise ValueError("crypto_tb must include spi_operations.svh exactly once inside its module")
    # prevent the header from being included by wrappers or itself.
    for key in ("operations_sv", "wrapper_sv"):
        if re.search(r"`include\b", scans[key]):
            raise ValueError("Only crypto_tb may include spi_operations.svh")

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
    # the application must consume its separately generated combinational controller.
    if len(re.findall(r"\bspi_testbench_controller\s+\w+\s*\(", scans["testbench_sv"])) != 1:
        raise ValueError("crypto_tb must instantiate exactly one spi_testbench_controller")
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
