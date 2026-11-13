"""generate candidates around maintained local driver and timing harnesses."""

# keep orchestration separate from requests, files, and simulator implementations.
import hashlib
import json
from pathlib import Path
import subprocess

from . import controllers, generation, mock, prompts
from .common import DEFAULT_MODEL, file_hash
from .driver import run_driver
from .harnesses import copy_fixed_harnesses
from .recovered import run_simulation
from .timing import run_timing


# ignore execution outputs when fingerprinting the interactive entry point.
def notebook_code_hash(path):
    notebook = json.loads(Path(path).read_text())
    sources = ["".join(cell.get("source", [])) for cell in notebook["cells"]
               if cell["cell_type"] == "code"]
    return hashlib.sha256(json.dumps(sources, ensure_ascii=False).encode()).hexdigest()


# include every maintained helper and harness alongside the transaction inputs.
def input_hashes(root, target):
    paths = [root / "docs/transaction_specification.md",
             root / "inputs/spi_ice40_crypto_ip_test.py"]
    paths.extend(sorted((root / "tb/spi_workflow").glob("*.py")))
    paths.extend(sorted((root / "tb/harnesses").glob("*.sv")))
    if target == "crypto_tb":
        paths.append(root / "recovery/recovered_sim.v")
    return {str(path.relative_to(root)): file_hash(path) for path in paths}


# reject a source edit rather than silently validating different implementations.
def verify_inputs(root, hashes, notebook_hash):
    if any(not (root / name).is_file() or file_hash(root / name) != digest
           for name, digest in hashes.items()):
        raise RuntimeError("Source inputs changed during this run")
    if notebook_code_hash(root / "notebooks/spi_tb_gen.ipynb") != notebook_hash:
        raise RuntimeError("Notebook code changed during this run")


# run only the selected target, with bounded fresh generation attempts.
def run_workflow(root, logger, client, *, target="tb_driver", seed=4313,
                 random_trials=8, max_attempts=1, timeout_s=30, api_timeout_s=300,
                 model=DEFAULT_MODEL, temperature=0.0):
    root = Path(root).resolve()
    if target not in {"tb_driver", "crypto_tb"}:
        raise ValueError("Use tb_driver or crypto_tb")
    if not isinstance(max_attempts, int) or not 1 <= max_attempts <= 10:
        raise ValueError("Use between one and ten generation attempts")
    if not 0 < api_timeout_s <= 300 or not 0 < timeout_s <= 300:
        raise ValueError("Use timeouts in (0, 300] seconds")
    if not isinstance(seed, int) or not 0 <= seed <= 0xFFFFFFFF:
        raise ValueError("Use a 32-bit simulation seed")
    if not isinstance(random_trials, int) or not 0 <= random_trials <= 64:
        raise ValueError("Use between zero and 64 random trials")
    if client.logger is not logger:
        raise ValueError("The client and workflow must share the same AI logger")

    # preserve prior runs and record the exact local implementation used.
    run_dir = root / "results/simulation" / logger.run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    source_hashes = input_hashes(root, target)
    notebook_hash = notebook_code_hash(root / "notebooks/spi_tb_gen.ipynb")
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                              text=True, check=True, timeout=10).stdout.strip()
    logger.log_event("context", source_sha256=source_hashes,
                     notebook_code_sha256=notebook_hash, revision=revision,
                     seed=seed, mode="live", target=target,
                     harness_origin="maintained local HDL")
    controller_feedback = operations_feedback = mock_feedback = ""
    history = []

    # a failed candidate never replaces the maintained scoreboards or timing probe.
    for attempt in range(1, max_attempts + 1):
        attempt_dir = run_dir / f"attempt-{attempt:02d}"
        generated_dir = attempt_dir / "generated"
        simulation_dir, timing_dir = attempt_dir / "simulation", attempt_dir / "timing"
        artifacts = mock_artifacts = result = timing_result = None
        stage = "local_harnesses"
        try:
            harness_paths = copy_fixed_harnesses(root, generated_dir)
            logger.log_event("local_sources", attempt=attempt, target=target,
                             origin="maintained local HDL",
                             source_sha256={name: file_hash(path) for name, path in harness_paths.items()})

            # request only the application controller, never the harness reference logic.
            stage = "testbench_controller"
            logger.log_event("candidate", attempt=attempt, stage=stage, target=target)
            controller = controllers.request_controller(
                prompts.build_prompt(root) + controller_feedback, logger,
                kind="testbench", client=client, seed=seed, timeout_s=api_timeout_s)
            controller_path = controllers.write_controller(controller, generated_dir, kind="testbench")
            logger.log_event("validation", attempt=attempt, stage=stage, target=target,
                             status="controller source accepted", artifact_sha256=file_hash(controller_path))

            # the model supplies shared SPI operations, application checks, and the DUT wrapper.
            stage = "operations_runner"
            logger.log_event("candidate", attempt=attempt, stage=stage, target=target)
            references = {"spi_testbench_controller.sv": controller_path.read_text()}
            prompt = prompts.build_operations_runner_prompt(root, references)
            artifacts = generation.request_generation(
                prompt + operations_feedback, logger, model=model, temperature=temperature,
                seed=seed, timeout_s=api_timeout_s, client=client)
            paths = generation.write_artifacts(artifacts, generated_dir)
            logger.log_event("validation", attempt=attempt, stage=stage, target=target,
                             status="runner sources accepted",
                             artifact_sha256={name: file_hash(path) for name, path in paths.items()})

            # driver validation uses a fresh mock and the fixed local scoreboard.
            if target == "tb_driver":
                stage = "mock"
                logger.log_event("candidate", attempt=attempt, stage=stage, target=target)
                mock_artifacts = mock.request_mock(
                    prompts.build_driver_prompt(root, generated_dir) + mock_feedback,
                    logger, client=client, seed=seed, timeout_s=api_timeout_s)
                mock_paths = mock.write_mock(mock_artifacts, generated_dir)
                logger.log_event("validation", attempt=attempt, stage=stage, target=target,
                                 status="mock accepted",
                                 artifact_sha256={name: file_hash(path) for name, path in mock_paths.items()})

            # freeze the candidate bytes while the selected tools run.
            candidate_hashes = {path.name: file_hash(path) for path in generated_dir.iterdir()
                                if path.is_file()}
            stage = "simulation"
            if target == "tb_driver":
                result = run_driver(root, simulation_dir, seed=seed,
                                    timeout_s=timeout_s, source_dir=generated_dir)
            else:
                result = run_simulation(root, generated_dir, simulation_dir, seed=seed,
                                        random_trials=random_trials, timeout_s=timeout_s)
            logger.log_event("simulation", attempt=attempt, target=target, result=result)
            if target == "crypto_tb":
                stage = "timing"
                timing_result = run_timing(root, timing_dir, timeout_s=timeout_s,
                                           source_dir=generated_dir)
                logger.log_event("simulation", attempt=attempt, target="baseline_timing_tb",
                                 result=timing_result)
            if not result["passed"] or (timing_result is not None and not timing_result["passed"]):
                raise RuntimeError("The selected simulation did not pass")
            if any(not (generated_dir / name).is_file() or file_hash(generated_dir / name) != digest
                   for name, digest in candidate_hashes.items()):
                raise RuntimeError("Candidate sources changed during validation")
            verify_inputs(root, source_hashes, notebook_hash)
            history.append({"attempt": attempt, "passed": True})
            logger.log_event("summary", target=target, attempts=history, result=result,
                             timing=timing_result, generated_sources=str(generated_dir))
            return {"generated_dir": generated_dir, "result": result, "timing_result": timing_result,
                    "attempts": history, "artifacts": artifacts, "paths": paths,
                    "mock_artifacts": mock_artifacts, "harness_paths": harness_paths,
                    "source_hashes": source_hashes, "notebook_hash": notebook_hash}
        except Exception as error:
            event = logger.log_event("validation", attempt=attempt, target=target,
                                     stage=stage, status="failed", message=str(error))
            history.append({"attempt": attempt, "passed": False, "stage": stage,
                            "error": event["message"]})
            if attempt == max_attempts:
                raise RuntimeError(
                    f"Validation failed at {stage}: {event['message']}\n"
                    f"Inspect {logger.run_dir} and {attempt_dir}"
                ) from None
            verify_inputs(root, source_hashes, notebook_hash)

            # preserve diagnostic line structure while removing redundant prose wrapping.
            diagnostics = event["message"]
            for directory in (simulation_dir, timing_dir):
                for path in sorted(directory.glob("*.log")):
                    diagnostics += "\n" + path.name + "\n" + path.read_text()[-8000:]
            short_feedback = ("\n<validation_feedback>" + event["message"][-2000:]
                              + "</validation_feedback> Keep the module contract unchanged.")
            controller_feedback = short_feedback
            mock_feedback = short_feedback
            operations_feedback = generation.repair_prompt("", artifacts or {}, diagnostics)
    raise RuntimeError("No candidate was accepted")
