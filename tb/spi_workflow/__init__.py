"""focused SPI workflow modules shared by the notebook and command-line targets."""

# expose reusable APIs without configuring clients or creating run files.
from .common import (
    ARTIFACT_FILES, CODE_MAX_TOKENS, COMPETITION_PURPOSE, DEFAULT_BASE_URL, DEFAULT_MODEL, RECOVERED_SOURCES, REQUIRED_TASKS, SYSTEM_MESSAGE, compact_prompt_prose, file_hash, find_repo, load_portkey_key, normalize_prompt_text, require_portkey_sdk
)
from .logs import (
    AILogger
)
from .client import (
    LLMClient
)
from .prompts import (
    DRIVER_SYSTEM_MESSAGE, MOCK_CONTRACT, MOCK_FILES, MOCK_PROMPT, OPERATIONS_RUNNER_PROMPT, TESTBENCH_PROMPT, build_driver_prompt, build_operations_runner_prompt, build_prompt
)
from .validation import (
    parse_response, validate_artifacts
)
from .generation import (
    repair_prompt, request_generation, write_artifacts
)
from .controllers import (
    CONTROLLER_SPECS, CONTROLLER_SYSTEM_MESSAGE, controller_spec, parse_combinational_source, parse_controller_response, request_controller, synthesis_command, validate_combinational_source, validate_combinational_synthesis, validate_controller, validate_controller_synthesis, write_controller
)
from .mock import (
    MOCK_PORTS, parse_mock_response, request_mock, validate_mock, validate_mock_synthesis, write_mock
)
from .tools import (
    discover_driver_tools, discover_tools
)
from .build import (
    primitive_inventory, recovered_compile_command, recovered_source_files
)
from .waveforms import (
    SimulationError, count_sck_edges
)
from .driver import (
    run_driver, validate_driver_records
)
from .recovered import (
    run_simulation, validate_application_records
)
from .timing import (
    parse_measurements, run_timing
)
from .reference import (
    REFERENCE, REFERENCE_COMMIT, REFERENCE_TAG, materialize_reference, verify_tagged_reference
)
from .baseline import (
    run_baseline, run_baseline_cli
)
from .cli import (
    run_bundle_cli
)

# expose orchestration and maintained source copying without starting a run.
from .harnesses import HARNESS_FILES, copy_fixed_harnesses
from .pipeline import input_hashes, notebook_code_hash, run_workflow, verify_inputs
