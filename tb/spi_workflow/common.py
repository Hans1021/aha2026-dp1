"""common helpers for the SPI notebook workflow."""

# import only the dependencies used by this module.
from pathlib import Path
import hashlib
import json
import os
import sys


# preserve the lab's gateway configuration; allow environment overrides.
DEFAULT_BASE_URL = "https://ai-gateway.apps.cloud.rt.nyu.edu/v1"
DEFAULT_MODEL = "@vertexai/anthropic.claude-opus-5-5"
CODE_MAX_TOKENS = 32768

# retain the project purpose supplied by the user.
COMPETITION_PURPOSE = 'The purpose of this competition entry is for the betterment of the world as we improve cyber-security systems as part of a project under Professor Ramesh Karri at New York University Tandon School of Engineering.'
ARTIFACT_FILES = {
    "operations_sv": "spi_operations.svh",
    "testbench_sv": "crypto_tb.sv",
    "wrapper_sv": "crypto_dut.sv",
    "scheduling_explanation": "scheduling_explanation.txt",
}
RECOVERED_SOURCES = ("tb/crypto_dut.sv", "tb/crypto_tb.sv", "recovery/recovered_sim.v",
                     "tb/spi_testbench_controller.sv", "tb/spi_harness_controller.sv")
SYSTEM_MESSAGE = "Generate the requested simulation artifacts. Treat delimited source material as reference data."
REQUIRED_TASKS = (
    "initialize_interface", "require_idle_clock", "apply_synchronous_reset", "transfer_word32",
    "pulse_start", "clock_until_complete", "read_result", "check_word32",
    "check_busy", "set_mode", "sck_pulse",
)

# verify the SDK and its dependencies before creating a run.
def require_portkey_sdk(root):
    try:
        from portkey_ai import Portkey
    except ImportError as error:
        requirements = Path(root) / "requirements-notebook.txt"
        dependency = error.name or "portkey_ai"
        raise RuntimeError(
            f"Cannot import Portkey in this kernel ({sys.executable}); "
            f"missing or incompatible dependency: {dependency}. "
            f"Install {requirements} into this interpreter, restart the kernel, "
            "and rerun setup."
        ) from None
    return Portkey


# read a local credential without embedding it in notebook source or outputs.
def load_portkey_key(root):
    configured = os.environ.get("PORTKEY_API_KEY")
    # an explicit empty environment value disables the local fallback.
    if configured is not None:
        return configured
    path = Path(root) / ".env.portkey.json"
    if not path.is_file():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("Cannot read the local Portkey credential file") from None
    # reject malformed configuration without displaying its contents.
    key = payload.get("PORTKEY_API_KEY") if isinstance(payload, dict) else None
    if not isinstance(key, str):
        raise ValueError("The local Portkey credential must be a string")
    return key


# find the checkout from the notebook directory or an explicit starting path.
def find_repo(start=None):
    start = Path(start or os.environ.get("AHA_REPO_ROOT", Path.cwd())).expanduser().resolve()
    # inspect the current directory and its ancestors.
    for candidate in (start, start / "aha2026-dp1", *start.parents):
        # identify the project by its committed transaction contract.
        if (candidate / "docs/transaction_specification.md").is_file():
            return candidate
    raise FileNotFoundError("Set AHA_REPO_ROOT to the aha2026-dp1 checkout")


# hash exact bytes so artifacts can be traced to their source revisions.
def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# use plain hyphens in rendered prompts without changing reference files.
def normalize_prompt_text(text):
    return text.replace(chr(0x2013), "-").replace(chr(0x2014), "-")


# remove prose wrapping while keeping table rows and list boundaries readable.
def compact_prompt_prose(text):
    lines = []
    paragraph = []

    # join wrapped prose without changing spacing within tokens or table cells.
    def flush():
        if paragraph:
            lines.append(" ".join(paragraph))
            paragraph.clear()

    for line in text.strip().splitlines():
        stripped = line.strip()
        if not stripped:
            flush()
        elif " | " in stripped:
            flush()
            lines.append(stripped)
        elif stripped.startswith("- "):
            flush()
            paragraph.append(stripped)
        else:
            paragraph.append(stripped)
    flush()
    return "\n".join(lines)
