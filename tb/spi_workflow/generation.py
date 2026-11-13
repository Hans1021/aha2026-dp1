"""generation helpers for the SPI notebook workflow."""

# import only the dependencies used by this module.
from pathlib import Path
import json
from .client import LLMClient
from .common import ARTIFACT_FILES, CODE_MAX_TOKENS, RECOVERED_SOURCES, SYSTEM_MESSAGE
from .validation import parse_response, validate_artifacts


# require fresh Portkey responses for each generation attempt.
def request_generation(prompt, logger, mode="live", model=None,
                       temperature=0.0, seed=4313, timeout_s=120, client=None):
    # reject alternate modes and unbounded requests before creating a client.
    if mode != "live" or not 0 < timeout_s <= 300:
        raise ValueError("Use live generation and a timeout in (0, 300] seconds")
    # share a client across repair attempts to retain its temperature preference.
    client = client or LLMClient(model=model, logger=logger, timeout_s=timeout_s)
    # keep each response in the requesting notebook's log.
    if client.logger is not logger:
        raise ValueError("The client and request must use the same AI logger")
    output_text = client.generate(prompt, system_prompt=SYSTEM_MESSAGE,
                                  temperature=temperature, max_tokens=CODE_MAX_TOKENS, seed=seed, stage="operations_runner")
    # link schema failures to the already logged model response.
    try:
        return parse_response(output_text)
    except Exception as error:
        logger.log_event("error", request_id=client.last_request_id,
                         error_type=type(error).__name__, message=str(error))
        raise


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
    # keep the compile list with the notebook's generated source bundle.
    source_list = output_dir / "recovered.f"
    source_list.write_text("\n".join(RECOVERED_SOURCES) + "\n", encoding="utf-8")
    paths["source_file_list"] = source_list
    return paths


# retain the previous response and measured failures in a bounded repair request.
def repair_prompt(original_prompt, artifacts, diagnostics):
    return (original_prompt + "\n<previous_response>\n" + json.dumps(artifacts)
            + "\n</previous_response>\n<validation_feedback>\n" + diagnostics[-16000:]
            + "\n</validation_feedback>\nRepair the generated testbench only. Keep the same JSON schema, "
            "transaction sequence, and expected values. Explain any scheduling correction.")
