"""mock helpers for the SPI notebook workflow."""

# import only the dependencies used by this module.
from pathlib import Path
from .client import LLMClient
from .common import CODE_MAX_TOKENS
from .controllers import parse_combinational_source, validate_combinational_source, validate_combinational_synthesis
from .prompts import DRIVER_SYSTEM_MESSAGE, MOCK_FILES


# describe the exact interface checked after synthesis.
MOCK_PORTS = {
    "EVENT": ("input", 2), "SCK": ("input", 1), "RST_N": ("input", 1),
    "MOSI": ("input", 1), "NORM_CS_N": ("input", 1), "START": ("input", 1),
    "ENC_DEC": ("input", 1), "RESPONSE_WORD": ("input", 32),
    "STATE_Q": ("input", 107), "STATE_D": ("output", 107),
    "ERROR": ("output", 4), "MISO": ("output", 1), "BUSY": ("output", 1),
    "ICE_LED": ("output", 1), "RECEIVED_WORD": ("output", 32),
    "FRAME_COUNT": ("output", 32), "FRAME_BITS": ("output", 32),
}


# accept only the raw mock module; preserve its exact response bytes.
def parse_mock_response(text):
    return {"mock_sv": parse_combinational_source(text, "spi_mock_peripheral")}


# share raw-source policy with the generated combinational controllers.
def validate_mock(artifacts):
    if not isinstance(artifacts, dict) or set(artifacts) != {"mock_sv"}:
        raise ValueError("Mock artifact must contain only mock_sv")
    validate_combinational_source(artifacts["mock_sv"], "spi_mock_peripheral", MOCK_PORTS)


# share structural policy while retaining the mock's exact unsigned interface.
def validate_mock_synthesis(path):
    return validate_combinational_synthesis(path, "spi_mock_peripheral", MOCK_PORTS)


# request one raw combinational module through the shared logged client.
def request_mock(prompt, logger, mode="live", client=None, seed=4313, timeout_s=60):
    if mode != "live" or not 0 < timeout_s <= 300:
        raise ValueError("Use live generation and a timeout in (0, 300] seconds")
    client = client or LLMClient(logger=logger, timeout_s=timeout_s)
    if client.logger is not logger:
        raise ValueError("The client and request must use the same AI logger")
    output = client.generate(prompt, system_prompt=DRIVER_SYSTEM_MESSAGE,
                             temperature=0.0, max_tokens=CODE_MAX_TOKENS, seed=seed,
                             stage="mock")
    # retain rejected raw responses alongside the request that produced them.
    try:
        artifacts = parse_mock_response(output)
        validate_mock(artifacts)
        return artifacts
    except Exception as error:
        logger.log_event("error", request_id=client.last_request_id, target="tb_driver",
                         error_type=type(error).__name__, message=str(error))
        raise


# write only the model-returned module to this attempt's fixed output filename.
def write_mock(artifacts, output_dir):
    validate_mock(artifacts)
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for key, filename in MOCK_FILES.items():
        path = output_dir / filename
        path.write_text(artifacts[key], encoding="utf-8")
        paths[key] = path
    return paths
