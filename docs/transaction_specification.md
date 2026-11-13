# Cryptographic IP transaction specification

This is the simulation stimulus contract derived from the supplied MicroPython
application. Reproduce its executable sequence before changing timing to match
measurements of the recovered circuit. Expected values below come from the
application, not from a completed simulation of the recovered design.

## Sources and precedence

- [Local application](../inputs/spi_ice40_crypto_ip_test.py), copied from
  [the supplied application at challenge commit `6c5a4a3`](https://github.com/JBlocklove/CSAW-AI-Hardware-Attack-Challenge-2026/blob/6c5a4a35e4fa3644cba8f3b755c1edd16af3fdf9/qualifier/spi_ice40_crypto_ip_test.py).
  Only comments and whitespace were changed; executable statements are preserved.
  Original file SHA-256:
  `f47bd1783747da483526475fe3e61475c83b85d346672a8dd408980498df4a99`.
- [Local interface guide](../inputs/ice40_cryptographic_IP.md) and
  [upstream guide](https://github.com/JBlocklove/CSAW-AI-Hardware-Attack-Challenge-2026/blob/6c5a4a35e4fa3644cba8f3b755c1edd16af3fdf9/qualifier/ice40_cryptographic_IP.md).
- [MicroPython SPI API](https://docs.micropython.org/en/latest/library/machine.SPI.html)
  for clock polarity, sampling phase, bit order, and simultaneous transfers.

Executable pin writes, loops, and buffer contents define the initial replay.
The guide supplies interface intent. Conflicting prose and comments are recorded
below rather than used to shorten the application sequence.

## Signals and transfer format

GPIO numbers below belong to the **RP2040**, not to FPGA package pins or recovered
Verilog port names. Establish the recovered-port mapping separately before wiring
a testbench.

| Signal | RP2040 GPIO | Direction at FPGA | Application use |
| --- | ---: | --- | --- |
| `SCK` | 6 | input | SPI clock and the core's system clock |
| `RST_N` | 7 | input | synchronous active-low reset |
| `MOSI` | 8 | input | serial data driven by the processor |
| `MISO` | 9 | output | serial data sampled by the processor |
| `NORM_CS_N` | 10 | input | active-low input shift-enable |
| `START` | 12 | input | high for one clock pulse to start an operation |
| `ENC_DEC` | 13 | input | `0` encrypts; `1` decrypts |
| `BUSY` | 14 | output | sampled status, not an interrupt |
| `ice_done` | 3 | configuration status | configured as an input but never read |

The guide also names `ICE_LED` as a mirror of `BUSY`; the application does not
configure or sample it. There is no application interrupt handler or interrupt
driven completion path.

`SoftSPI` requests 50,000 bits/s, `polarity=0`, `phase=0`, eight bits per byte, and
MSB-first transmission. Each transfer contains four bytes, with chip-select held
low across all 32 bits. Send bytes in array order and bits 7 through 0 within each
byte. Mode 0 idles `SCK` low and samples on its rising edge; establish `MOSI` while
the clock is low. Do not reverse the bytes or insert chip-select pulses between
them. `spi.write()` discards received bits; `write_readinto()` captures them while
also transmitting all four bytes.

| Name | Bytes in transmission order | 32-bit notation | Last transmitted bit |
| --- | --- | --- | ---: |
| `P` / `plaintext` | `59 C3 59 C3` | `0x59C359C3` | 1 |
| `C` / `ciphertext` | `9C D8 43 92` | `0x9CD84392` | 0 |

These are one supplied encryption/decryption pair. A reset comment in the original
application says AES, but the test does not establish standard AES, its parameters,
or any other full algorithm specification. There is **no key-loading transaction**,
key buffer, or key-select command in this application. Do not invent one.

## Clock and reset rules

A manual pulse means `SCK: 0 -> 1 -> 0`. The application emits these pulses through
GPIO writes, without explicit delays. Its requested 50 kHz rate applies to
`SoftSPI`; the manual pulse duration is not specified by the Python source.

For a deterministic simulation, use a 20 microsecond period, with 10 microsecond
low and high phases, for both manual and transfer clocks. This is a simulation
timing choice, not a measurement of RP2040 GPIO execution. It respects the guide's
1 MHz maximum. Change control inputs away from the rising edge, and sample status
after sequential updates have settled. Resolve the precise MISO sampling position
against the recovered model and mode-0 transfer behavior without silently adding
or removing a bit clock.

`SCK` drives the entire core. Delays, print calls, or polling `BUSY` with a static
clock cannot advance computation. `NORM_CS_N=1` prevents SPI input shifting; it
does not remove the need to clock processing. Keep chip-select high during reset,
start, and the seven following processing pulses.

Reset is synchronous: hold `RST_N=0` across a rising clock edge. The application
then releases reset and supplies **one additional full pulse** before proceeding.
A low-to-high reset transition alone does not reset a clocked design. Repeat this
two-pulse reset sequence before decryption as well as at startup.

The initial explicit idle assignments are `SCK=0`, `RST_N=1`, `NORM_CS_N=1`, and
`START=0`. The application does not explicitly initialize `ENC_DEC` or `MOSI` before
the initial reset and echo test. A testbench may initialize both to zero for
determinism, but must label that as a testbench choice. No operation is started
until the application explicitly selects a mode. Outside transfers, retain the
last MOSI bit rather than substituting an extra data transfer.

## Successful application sequence

The pulse numbers count every rising `SCK` edge from the first asserted-reset
pulse. They exclude setup writes, status reads, printing, and static-clock gaps.
`START=0` except on pulses 67 and 141. `RST_N=1` except on pulses 1 and 107.
CS in this table means `NORM_CS_N`.

| Step | Pulse numbers | CS | `ENC_DEC` | Processor action and expected observation |
| --- | --- | ---: | --- | --- |
| Initialize | none | 1 | not explicitly set | Set the explicit idle levels and configure `SoftSPI`. |
| Assert reset | 1 | 1 | not explicitly set | Set `RST_N=0`; supply one full pulse. |
| Release reset | 2 | 1 | not explicitly set | Set `RST_N=1`; supply one full pulse. |
| Load plaintext | 3–34 | 0 | not explicitly set | `spi.write(P)`; ignore MISO; then set CS high. |
| Check SPI echo | 35–66 | 0 | not explicitly set | `write_readinto(P, rxdata)`; then set CS high; expect `rxdata=P`. This reloads `P`. |
| Start encryption | 67 | 1 | 0 | Set `START=1`, pulse once, then set `START=0`; expect `BUSY=1`. |
| Clock encryption | 68–74 | 1 | 0 | Supply exactly seven further pulses; then expect `BUSY=0`. |
| Read encryption | 75–106 | 0 | 0 | `write_readinto(P, rxdata)`; then set CS high; expect `rxdata=C`. |
| Select decryption and reset | 107 | 1 | 1 | Set mode to 1, assert `RST_N=0`, and pulse once. |
| Release reset | 108 | 1 | 1 | Set `RST_N=1`; supply one full pulse. |
| Load ciphertext | 109–140 | 0 | 1 | `spi.write(C)`; ignore MISO; then set CS high. |
| Start decryption | 141 | 1 | 1 | Set `START=1`, pulse once, then set `START=0`; no immediate BUSY check in the application. |
| Clock decryption | 142–148 | 1 | 1 | Supply exactly seven further pulses; then expect `BUSY=0`. |
| Read decryption | 149–180 | 0 | 1 | `write_readinto(C, rxdata)`; then set CS high; expect `rxdata=P`. |

This path has five 32-bit transfers, two reset pulses plus two post-reset pulses,
and two sets of eight start/processing pulses: **180 full SCK pulses** in total.
Finish with `SCK=0`, `RST_N=1`, `NORM_CS_N=1`, `START=0`, and `ENC_DEC=1`.

There is no new load between the echo check and encryption: the echo's simultaneous
transmit reloads plaintext. Decryption uses the supplied constant `C`, not the
previous encryption's received buffer. It therefore remains an independent check
even when the application reports an encryption-value mismatch.

### Readout is also an input transaction

| Readout | MOSI bytes | Expected MISO bytes | Input word shifted in by the readout |
| --- | --- | --- | --- |
| SPI echo | `59 C3 59 C3` | `59 C3 59 C3` | `P` |
| Encryption result | `59 C3 59 C3` | `9C D8 43 92` | `P` |
| Decryption result | `9C D8 43 92` | `59 C3 59 C3` | `C` |

The application keeps `txdata` unchanged during each `write_readinto()` call.
Do not model these reads using zero-filled MOSI, high-impedance MOSI, or clocks
that leave the input shifter untouched. After plaintext transfers the retained
last MOSI bit is 1; after ciphertext transfers it is 0. Preserve these levels
during the intervening manual pulses. Confirm actual recovered-register contents
when the shifter is identified; the table states the required transmitted word.

## Status checks and failure behavior

The application checks equality after the echo, high BUSY immediately after the
encryption start pulse, and low BUSY after each seven-pulse loop. A failed check
at any of those points prints an error and returns. It does not poll BUSY inside
the loops, stop clocking early when BUSY falls, or wait indefinitely for it.

Encryption/decryption value mismatches only print diagnostics. In particular, an
encryption mismatch does not prevent the decryption test. A future automated
testbench should record every mismatch and return a failing test result, while
preserving the successful stimulus sequence. That failure reporting is a harness
improvement, not behavior already present in the application.

## Timing discrepancy and measurement plan

| Evidence | Timing statement |
| --- | --- |
| Interface guide | Processing should take four clock cycles. |
| Original application comments | Three additional cycles, four total. |
| Executable application, both modes | One START pulse followed by `range(7)`: eight pulses total. |

The first replay must use **one start pulse plus seven additional pulses** even
if BUSY falls sooner. Eight supplied pulses do not prove eight-cycle latency.
The guide's claim that results load when BUSY falls is an interface expectation
to verify, not a measured property of the recovered netlist.

After reproducing the sequence above:

1. Define operation edge `e0` as the rising edge with `START=1`; label the seven
   following rising edges `e1` through `e7`. Keep `CS=1`, `RST_N=1`, the selected
   mode, and the retained MOSI bit stable through this window.
2. Record BUSY before `e0` and after every rising edge has settled, in both modes.
   Preserve four-state values so unknown status cannot pass as zero. Record the
   first assertion and first high-to-low transition using these edge labels.
3. If internal state is observable, identify when the result is loaded into the
   serial register, including whether it occurs on or after the BUSY transition.
   Observe MISO around the first readout edge to detect sampling or bit alignment
   errors. Do not infer a parallel load merely from one MISO level.
4. In separate, freshly reset trials, vary the number of post-start clocks and
   compare the complete readout with `C` or `P`. Each readout itself adds 32 core
   clocks and overwrites the input word, so do not reuse a trial's state to infer
   an earlier completion time. Mark early reads as diagnostic protocol probes.
5. Record measured BUSY edges, result availability, bit alignment, and any reset
   or LED differences alongside the recovered source revision and simulator.
   Keep those measurements separate from the application-compatible baseline.

**Measurement status:** recovered-circuit latency, result-load timing, and
simulation output values have not yet been measured for this specification.

## Validation boundary

An application-only call trace with scripted peripheral replies confirmed the
180-pulse schedule, reset/start edges, MOSI words, and three BUSY checks. This
checks the stimulus transcription, not the recovered circuit's responses.
The copied application's Python syntax tree and all three recovered Verilog
files' non-comment tokens match their original versions.

With OSS CAD Suite 2026-11-05, `recovery/recovered_sim.v` compiles under Icarus
Verilog with the suite's `share/yosys/ice40/cells_sim.v` models. Direct compilation
of `recovered_netlist.v` and `recovered_pins.v` reports duplicate port declarations;
the same errors occur in their original versions. Use the simulation export for
the upcoming harness. Compilation alone does not validate transaction behavior.
