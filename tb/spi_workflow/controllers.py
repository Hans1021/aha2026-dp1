"""controllers helpers for the SPI notebook workflow."""

# import only the dependencies used by this module.
from pathlib import Path
import json
import re
from .client import LLMClient
from .common import CODE_MAX_TOKENS, DEFAULT_MODEL, file_hash


# keep each raw controller stage's artifact and unsigned interface fixed.
CONTROLLER_SPECS = {
    "testbench": {
        "top": "spi_testbench_controller",
        "filename": "spi_testbench_controller.sv",
        "stage": "testbench_controller",
        "ports": {
            "STEP": ("input", 4), "RANDOM_PHASE": ("input", 2),
            "RANDOM_WORD": ("input", 32), "VALID": ("output", 1),
            "ACTION": ("output", 3), "MODE": ("output", 1),
            "SET_MODE": ("output", 1), "TX_WORD": ("output", 32),
            "EXPECTED_WORD": ("output", 32), "CHECK_WORD": ("output", 1),
            "REQUIRE_BUSY": ("output", 1), "PULSE_LIMIT": ("output", 7),
            "FIXED_COUNT": ("output", 1),
        },
    },
    "harness": {
        "top": "spi_harness_controller",
        "filename": "spi_harness_controller.sv",
        "stage": "harness_controller",
        "ports": {
            "INDEX": ("input", 4), "RNG_STATE": ("input", 32),
            "ENC_DEC": ("input", 1), "CUTOFF": ("input", 4),
            "FRAME_VALID": ("output", 1), "TX_WORD": ("output", 32),
            "RESPONSE_WORD": ("output", 32), "RNG_NEXT": ("output", 32),
            "TIMING_VALID": ("output", 1), "INPUT_WORD": ("output", 32),
            "RESULT_WORD": ("output", 32), "READ_WORD": ("output", 32),
            "BUSY_EXPECTED": ("output", 1), "RESULT_READY": ("output", 1),
        },
    },
}
CONTROLLER_SYSTEM_MESSAGE = (
    "Return only the requested comment-free synthesizable combinational module. "
    "Preserve its unsigned interface and behavior. No testbench, helper, or test code."
)


# reject unknown stage names before creating a request or output directory.
def controller_spec(kind):
    if not isinstance(kind, str) or kind not in CONTROLLER_SPECS:
        raise ValueError("Controller kind must be testbench or harness")
    return CONTROLLER_SPECS[kind]


# retain raw source bytes while rejecting extra response content.
def parse_combinational_source(text, top):
    if not isinstance(text, str) or not text.strip() or len(text) > 100_000:
        raise ValueError("Response must be nonempty module text below 100,000 characters")
    source = text.strip()
    if (not re.fullmatch(r"module\s+" + re.escape(top) + r"\b[\s\S]*\bendmodule", source)
            or re.findall(r"\bmodule\s+(\w+)", source) != [top]
            or len(re.findall(r"\bendmodule\b", source)) != 1):
        raise ValueError(f"Return only one raw {top} module")
    return text


# preflight raw HDL; synthesis remains the structural acceptance boundary.
def validate_combinational_source(text, top, ports):
    source = parse_combinational_source(text, top)
    syntax = re.sub(r"\balways\s*@\s*\(\s*\*\s*\)", "always_comb", source)
    if re.search(r'//|/\*|\*/|`|\(\*|\*\)|"', syntax):
        raise ValueError("Source must contain no comments, directives, attributes, or strings")
    forbidden = r"\b(?:initial|final|always_ff|always_latch|posedge|negedge|wait|fork|join|join_any|join_none|force|release|bind|task|function|class|interface|package|program|specify|primitive|defparam|import|export)\b"
    if re.search(forbidden, source) or re.search(r"[#\$]", source):
        raise ValueError("Source must be combinational and contain no simulation actions")
    # explicit event controls cannot introduce a second scheduler or stored state.
    remaining = re.sub(r"\balways\s*@\s*(?:\*|\(\s*\*\s*\))", "", source)
    remaining = re.sub(r"\balways_comb\b", "", remaining)
    if re.search(r"@|\balways\b", remaining):
        raise ValueError("Use only always_comb, always @*, or continuous assignments")
    # local constants are legal; variable declaration initializers are not.
    declarations = re.sub(r"\blocalparam\b[^;]+;", "", source)
    for declaration in re.findall(r"\b(?:reg|logic|integer)\b[^;]*;", declarations):
        if re.search(r"(?<![=!<>])=(?!=)", declaration):
            raise ValueError("Declarations must not initialize stored variables")
    # child modules and gate primitives would violate the single-module contract.
    if ";" not in source:
        raise ValueError("Module declaration must end with a semicolon")
    body = source[source.index(";") + 1:]
    instances = re.findall(r"(?:^|;)\s*(\w+)\s+\w+\s*\(", body, re.M)
    primitives = r"\b(?:and|nand|nor|or|xor|xnor|not|buf|tran|rtran|cmos|rcmos|nmos|pmos|rnmos|rpmos|bufif0|bufif1|notif0|notif1|tranif0|tranif1|rtranif0|rtranif1)\s*(?:\w+\s*)?\("
    if any(name not in {"else", "end", "always_comb"} for name in instances) or re.search(primitives, body):
        raise ValueError("Source must not instantiate helpers or primitives")
    # inputs belong to the simulation runner or external state owner.
    inputs = "|".join(re.escape(name) for name, (direction, _) in ports.items() if direction == "input")
    target = r"(?:" + inputs + r")(?:\s*\[[^\]]+\])?"
    if (re.search(r"\b" + target + r"\s*(?:=(?!=)|\+\+|--|[-+*/%&|^]=)", body)
            or re.search(r"(?:\+\+|--)\s*\b" + target, body)
            or re.search(r"\{[^;{}]*\b(?:" + inputs + r")\b[^;{}]*\}\s*=(?!=)", body)
            or re.search(r"(?:^|;|\bbegin\b|\belse\b|\))\s*" + target + r"\s*<=", body)):
        raise ValueError("Source must not assign its inputs or external state")


# inspect exact ports and lowered gates without treating syntax checks as proof.
def validate_combinational_synthesis(path, top, expected_ports):
    document = json.loads(Path(path).read_text())
    modules = document.get("modules", {})
    if set(modules) != {top}:
        raise ValueError(f"Synthesis must contain exactly {top}")
    module = modules[top]
    if module.get("memories") or module.get("processes"):
        raise ValueError("Synthesized module retains memory or procedural state")
    # an empty black box is not an accepted combinational implementation.
    for flag in ("blackbox", "whitebox"):
        value = module.get("attributes", {}).get(flag, 0)
        enabled = int(value, 2) if isinstance(value, str) else value
        if enabled:
            raise ValueError("Synthesized module must not be a black box")
    ports = module.get("ports", {})
    signature = {name: (port.get("direction"), len(port.get("bits", [])))
                 for name, port in ports.items()}
    if (signature != expected_ports or any(port.get("offset", 0) != 0 or port.get("upto", 0)
                                          or port.get("signed", 0) for port in ports.values())):
        raise ValueError("Synthesized ports differ from the unsigned module contract")
    # synth -noabc must leave only supported combinational technology cells.
    allowed = {"$_BUF_", "$_NOT_", "$_AND_", "$_NAND_", "$_OR_", "$_NOR_",
               "$_XOR_", "$_XNOR_", "$_ANDNOT_", "$_ORNOT_", "$_MUX_", "$_NMUX_",
               "$_AOI3_", "$_OAI3_", "$_AOI4_", "$_OAI4_"}
    kinds = {cell.get("type") for cell in module.get("cells", {}).values()}
    unsupported = kinds - allowed
    if unsupported:
        raise ValueError("Synthesized module contains storage or unsupported cells: "
                         + repr(sorted(unsupported, key=str)))
    return {"combinational": True, "top": top, "cell_count": len(module.get("cells", {})),
            "cell_types": sorted(kinds), "netlist_sha256": file_hash(path)}


# construct a bounded runner command without shell interpretation of source paths.
def synthesis_command(tools, source, top, json_filename):
    if not re.fullmatch(r"[A-Za-z_]\w*", top):
        raise ValueError("Synthesis top must be a Verilog identifier")
    if not isinstance(json_filename, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.json", json_filename):
        raise ValueError("Synthesis output must be a JSON basename")
    script = ("read_verilog -sv " + json.dumps(str(Path(source).resolve()))
              + f"; synth -top {top} -noabc; check -assert; write_json "
              + json.dumps(json_filename))
    return [tools["yosys"], "-Q", "-T", "-p", script]


# parse only the requested controller, never a runner or an artifact bundle.
def parse_controller_response(text, kind):
    return {"source_sv": parse_combinational_source(text, controller_spec(kind)["top"])}


# validate a single controller source without changing its response bytes.
def validate_controller(artifacts, kind):
    spec = controller_spec(kind)
    if not isinstance(artifacts, dict) or set(artifacts) != {"source_sv"}:
        raise ValueError("Controller artifact must contain only source_sv")
    validate_combinational_source(artifacts["source_sv"], spec["top"], spec["ports"])


# apply the same structural checks to either controller's synthesized netlist.
def validate_controller_synthesis(path, kind):
    spec = controller_spec(kind)
    return validate_combinational_synthesis(path, spec["top"], spec["ports"])


# request one controller through the shared, explicitly selected model route.
def request_controller(prompt, logger, *, kind, client=None, seed=4313, timeout_s=120):
    if kind != "testbench":
        raise ValueError("The harness controller is maintained locally; only testbench generation uses Portkey")
    spec = controller_spec(kind)
    if not 0 < timeout_s <= 300:
        raise ValueError("Use a timeout in (0, 300] seconds")
    client = client or LLMClient(model=DEFAULT_MODEL, logger=logger, timeout_s=timeout_s)
    if client.logger is not logger:
        raise ValueError("The client and request must use the same AI logger")
    if client.model != DEFAULT_MODEL:
        raise ValueError(f"Controller generation requires {DEFAULT_MODEL}")
    output = client.generate(prompt, system_prompt=CONTROLLER_SYSTEM_MESSAGE,
                             temperature=0.0, max_tokens=CODE_MAX_TOKENS, seed=seed,
                             stage=spec["stage"])
    # keep malformed responses linked to the already logged actual request.
    try:
        artifacts = parse_controller_response(output, kind)
        validate_controller(artifacts, kind)
        return artifacts
    except Exception as error:
        logger.log_event("error", request_id=client.last_request_id, target=spec["stage"],
                         error_type=type(error).__name__, message=str(error))
        raise


# write only validated model text to the stage's fixed source filename.
def write_controller(artifacts, output_dir, *, kind):
    validate_controller(artifacts, kind)
    spec = controller_spec(kind)
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / spec["filename"]
    path.write_text(artifacts["source_sv"], encoding="utf-8")
    return path
