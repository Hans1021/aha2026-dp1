# use deterministic defaults without requiring notebook dependencies.
PYTHON ?= python3
SEED ?= 4313
TIMEOUT ?= 30
RANDOM_TRIALS ?= 8

.PHONY: tb_driver tb_recovered baseline test

# validate the shared SPI driver against the AI-generated mock only.
tb_driver:
	$(PYTHON) -m scripts.run_testbench --target tb_driver --seed $(SEED) --timeout $(TIMEOUT)

# validate supplied vectors against the separate recovered circuit.
tb_recovered:
	$(PYTHON) -m scripts.run_testbench --target crypto_tb --seed $(SEED) --random-trials $(RANDOM_TRIALS) --timeout $(TIMEOUT)

# include negative probes that must fail inside their simulations.
test:
	$(PYTHON) -m unittest discover -s tests -v

# verify frozen inputs and rerun all recovered-baseline acceptance checks.
baseline:
	$(PYTHON) -m scripts.frozen_baseline --timeout $(TIMEOUT)
