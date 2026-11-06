"""run separately labeled driver or recovered-DUT tests."""

# defer target imports until the requested test is known.
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid


# allocate separate diagnostics and waveforms for each CLI run.
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("tb_driver", "crypto_tb"), default="tb_driver")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--seed", type=int, default=4313)
    parser.add_argument("--random-trials", type=int)
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args(argv)
    # only the recovered suite has a configurable random trial count.
    if args.target == "tb_driver" and args.random_trials is not None:
        parser.error("--random-trials applies only to crypto_tb")
    root = Path(__file__).resolve().parents[1]
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    output_dir = args.output_dir or root / "results/simulation" / args.target / run_id

    # keep mock compilation independent of recovered files and iCE40 models.
    try:
        # preserve earlier evidence when a caller supplies an output directory.
        if output_dir.exists():
            raise FileExistsError("Choose a fresh output directory for this run")
        # import only the selected target's runner.
        if args.target == "tb_driver":
            from scripts.driver_workflow import run_driver

            print("DRIVER TEST: mock SPI validation; no cryptographic validation", flush=True)
            result = run_driver(root, output_dir, seed=args.seed, timeout_s=args.timeout)
        else:
            from scripts.testbench_workflow import run_simulation

            print("RECOVERED DUT TEST: supplied application vectors and SPI echoes", flush=True)
            result = run_simulation(root, root / "tb", output_dir, seed=args.seed,
                                    random_trials=8 if args.random_trials is None else args.random_trials,
                                    timeout_s=args.timeout)
    except Exception as error:
        print(f"FAIL {args.target}: {error}")
        print("Artifacts:", output_dir)
        return 1
    print(json.dumps(result, indent=2))
    return 0


# propagate simulation failures to make and CI callers.
if __name__ == "__main__":
    raise SystemExit(main())
