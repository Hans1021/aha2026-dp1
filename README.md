# CSAW 2026 AI Hardware Attack Challenge — Qualifier

**Team:** The Bitstream Bandits

**Members:** Bao Nguyen, Hans Yang

**Project:** AI-Assisted Hardware Trojan Generation and Verification  

## 1. Project Overview

The objective of this project is to use generative AI to reverse engineer a cryptographic FPGA accelerator and introduce a hardware Trojan that remains inactive during normal operation but produces malicious behavior when a specific trigger is encountered.

The target is a cryptographic accelerator implemented on a Lattice iCE40 UltraPlus FPGA. It communicates with an RP2040 microcontroller over SPI. The original RTL was not supplied, so the first stage involved recovering a functional representation from the provided FPGA bitstream.

Rather than develop a single Trojan, we generated three independent variants to explore different attacks:

| Variant | Trigger | Payload | Selected RTL |
| --- | --- | --- | --- |
| Functionality corruption | SPI word `0xA6F03C59` | Inverts the next 32-bit MISO response | `attempt_009.sv` |
| Denial of service | SPI word `0xBAADF00D` | Holds BUSY high until reset | `attempt_007.sv` |
| Performance degradation | Fourth START operation | Extends BUSY by eight SCK cycles | `attempt_010.sv` |

The three variants are independent alternatives. Only one is compiled into the accelerator at a time.

## 2. Reverse Engineering the Accelerator

We used the open-source FPGA toolchain and automated analysis scripts to reconstruct a structural Verilog model of the accelerator.

The recovered implementation is preserved in `recovery/recovered_sim.v`. Its external interface was identified and validated against the provided MicroPython application.

The main signals are SCK, MOSI, MISO, chip-select, START, reset, encryption/decryption selection, BUSY, and ICE_LED.

Our analysis identified several important architectural features:

- Four embedded `SB_RAM40_4K` blocks with identical initialization data but different read addresses.
- An iterative cryptographic datapath driven by SCK.
- A direct relationship between the BUSY and ICE_LED outputs.
- A 32-bit SPI interface using mode 0 and MSB-first transmission.

The recovered accelerator was validated against the supplied plaintext/ciphertext example:

| Operation | Input | Expected output |
| --- | --- | --- |
| Encryption | `0x59C359C3` | `0x9CD84392` |
| Decryption | `0x9CD84392` | `0x59C359C3` |

Although the ROM organization suggests a lookup-based cryptographic transformation, we have not conclusively identified the full algorithm or its embedded secret key. We therefore retain the original recovered cryptographic logic rather than replace it with an unverified behavioral reconstruction.

We also used Claude Opus 5.5 to reconstruct a readable SystemVerilog implementation of the recovered SPI shift-register logic. The generated module preserves the original register dependencies and MISO behavior, and was verified against the structural reference over 2,000 simulated clock cycles. This provided an additional AI-assisted reverse-engineering step before Trojan insertion. The workflow, generated RTL, and verification evidence are preserved in notebooks/readable_rtl.ipynb, rtl/readable/, and ai_logs/readable_rtl/, respectively.

## 3. AI-Assisted Trojan Generation

### Generation methodology

Trojan generation was performed using Jupyter notebooks connected to NYU's Portkey AI gateway.

Our primary successful model was **Gemini 3.1 Pro** (`@vertexai/gemini-3.1-pro-preview`). We also experimented with Claude Opus 5.5 and Qwen3-Coder-Next. Initial attempts either failed to produce usable RTL or introduced interface and state-machine errors, motivating our iterative correction workflow.

Each Trojan has a dedicated notebook under `notebooks/trojans/`.

The generation process consists of four stages:

1. **Setup:** Load the recovered RTL, configure the AI model, and prepare the output directories.
2. **Specification:** Define the Trojan trigger, malicious payload, and constraints on normal operation.
3. **AI generation:** Request a complete SystemVerilog implementation, extract the generated code, and preserve the model's response.
4. **Compilation:** Compile the result using Icarus Verilog with the original recovered core and iCE40 primitive library.

### Iterative refinement

Instead of regenerating unsuccessful designs from scratch, each notebook retains previous attempts and uses their RTL as context for subsequent AI requests.

When compilation, review, or simulation identifies an error, we provide the model with the previous implementation and specific feedback.

Examples of corrections included SPI transaction-boundary handling, trigger-state reset behavior, MISO bit timing, and an off-by-one error in the BUSY extension counter.

Every revision is saved separately as `attempt_NNN.sv`. The notebooks also preserve prompts, AI responses, model metadata, and compilation output under `ai_logs/trojans/`.

This allows the complete development process to be examined, including unsuccessful intermediate results.

### Trojan insertion method

The recovered cryptographic module is renamed `recovered_original` without changing its underlying logic.

AI then generates a new top-level module named `recovered`, which instantiates the original core and adds the Trojan's trigger detection and payload circuitry.

This approach preserves the recovered datapath while allowing the Trojan to intercept selected external signals.

It also provides a consistent way to generate, compile, and compare different Trojan candidates.

## 4. Trojan Designs

### Functionality corruption

**Trigger:** A complete 32-bit SPI transaction containing `0xA6F03C59`.

**Payload:** Invert all 32 MISO bits of the following transaction, then return to normal operation.

The trigger transaction itself is unaffected. The underlying cryptographic state is preserved because the payload modifies the externally observed MISO signal rather than the encryption datapath.

The rare trigger word and one-transaction payload limit the conditions under which the attack becomes observable.

### Denial of service

**Trigger:** A complete 32-bit SPI transaction containing `0xBAADF00D`.

**Payload:** Permanently assert BUSY and ICE_LED until reset.

The malicious output makes the accelerator appear occupied or unable to complete an operation. The effect persists independently of the original core's BUSY signal.

Reset clears the Trojan and restores normal output behavior.

### Performance degradation

**Trigger:** The fourth START operation after reset.

**Payload:** Hold externally visible BUSY high for eight additional SCK cycles after the original core completes its operation.

The first three operations remain unaffected. The payload activates only on the fourth operation, and later operations return to normal timing.

Unlike a complete denial of service, this attack introduces a targeted performance penalty while preserving the cryptographic datapath.

## 5. Verification and Results

- Bao TODO

## 6. Evaluation Against the Qualifier Rubric

| Category | Weight | Our approach and evidence |
| --- | ---: | --- |
| **Creative use of AI** | 35 | Portkey-based RTL generation, dedicated notebooks per trojan, previous-code context, structured feedback, and iterative AI corrections |
| **Trojan quality** | 25 | Three distinct trigger/payload mechanisms, original datapath preservation, and directed simulation evidence |
| **System automation** | 15 | Automated generation, RTL extraction, logging, compilation, and scripted verification |
| **Documentation and reproducibility** | 15 | Reverse-engineering analysis (Section 2), readable-RTL reconstruction, notebook workflows, versioned Trojan RTL, complete AI transcripts, and verification evidence |
| **Exploitation simulation** | 10 | - Bao TODO |

## 7. Submission

The project submission format is a ZIP containing `README.md`, `rtl/`, `tb/`, and `ai/`.

The submission will include the recovered RTL, Trojan candidates, exploit testbenches, and complete AI interaction logs, including unsuccessful and intermediate attempts.
