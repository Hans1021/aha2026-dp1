IVERILOG ?= iverilog
VVP ?= vvp
ICE40_CELLS_SIM ?= /usr/share/yosys/ice40/cells_sim.v

.PHONY: sim clean

sim:
	mkdir -p build
	$(IVERILOG) -g2012 -s crypto_tb \
		-I tb \
		-DNO_ICE40_DEFAULT_ASSIGNMENTS \
		-o build/simulation.vvp \
		tb/crypto_dut.sv \
		tb/crypto_tb.sv \
		recovery/recovered_sim.v \
		$(ICE40_CELLS_SIM)
	$(VVP) build/simulation.vvp +seed=4313 +random_trials=0

clean:
	rm -rf build
