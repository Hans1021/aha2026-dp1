`timescale 1ns/1ps
// ---------------------------------------------------------------------------
// crypto_dut: thin wrapper that exposes the recovered accelerator (module
// `recovered`) through readable processor-side signal names. The same wrapper
// drives either the clean recovered core (recovery/recovered_sim.v) or a
// Trojan variant (rtl/trojans/<variant>/attempt_NNN.sv), because every Trojan
// keeps the original `recovered` port list and pin names.
//
// Reused from the project's SPI testbench workflow (tb/spi_workflow); kept here
// so the Trojan verification harness is self-contained under tb/.
// ---------------------------------------------------------------------------
module crypto_dut(
  input  wire SCK,
  input  wire RST_N,
  input  wire MOSI,
  output wire MISO,
  input  wire NORM_CS_N,
  input  wire START,
  input  wire ENC_DEC,
  output wire BUSY,
  output wire ICE_LED
);
  recovered u_recovered (
    .pin_36(SCK),
    .pin_34(RST_N),
    .pin_32(MOSI),
    .pin_31(MISO),
    .pin_28(NORM_CS_N),
    .pin_27(START),
    .pin_25(ENC_DEC),
    .pin_38(BUSY),
    .pin_23(ICE_LED)
  );
endmodule
