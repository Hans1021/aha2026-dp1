# Trojan verification harness

Directed SPI simulations that verify the three hardware Trojans against the
recovered cryptographic accelerator. Driven by
[`notebooks/trojan_verification.ipynb`](../../notebooks/trojan_verification.ipynb);
results are summarised in README Section 5.

## Files

| File | Purpose |
| --- | --- |
| `crypto_dut.sv` | Wraps `recovered` (clean core or a Trojan) with readable processor-side signal names |
| `spi_operations.svh` | SPI controller-side driver tasks (mode 0, MSB-first, 32-bit), reused from the SPI testbench workflow |
| `baseline_tb.sv` | Normal operation: full encrypt + decrypt golden-vector check; passes on the clean core and every Trojan |
| `functionality_tb.sv` | Functionality Trojan: trigger `0xA6F03C59` inverts the next transaction's MISO; near-miss `0xA6F03C58` does not |
| `dos_tb.sv` | DoS Trojan: trigger `0xBAADF00D` latches BUSY/ICE_LED until reset; near-miss `0xBAADF00C` does not |
| `performance_tb.sv` | Performance Trojan: 4th START extends BUSY by 8 SCK cycles; other operations normal |

## Running outside the notebook

Each case compiles the DUT wrapper, the design under test, the iCE40 primitive
models (`cells_sim.v`), and one testbench. For a Trojan, the recovered core is
renamed `recovered` → `recovered_original` so the Trojan wrapper can instantiate
it. Example (functionality Trojan):

```sh
# recovered_original.v = recovery/recovered_sim.v with the first
# "module recovered(" renamed to "module recovered_original(".
iverilog -g2012 -DNO_ICE40_DEFAULT_ASSIGNMENTS -s functionality_tb \
  -I tb/trojan_verification -o func.vvp \
  tb/trojan_verification/crypto_dut.sv \
  rtl/trojans/functionality/attempt_009.sv \
  recovered_original.v \
  "$ICE40_CELLS_SIM" \
  tb/trojan_verification/functionality_tb.sv
vvp func.vvp   # expect: FUNCTIONALITY SUMMARY PASS
```

A case passes only when its testbench prints `SUMMARY PASS` and nothing prints
`FAIL`.
