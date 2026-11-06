`timescale 1ns/1ps

/* expose the recovered package pins through the documented interface. */
module crypto_dut (
    input wire SCK,
    input wire RST_N,
    input wire MOSI,
    input wire NORM_CS_N,
    input wire START,
    input wire ENC_DEC,
    output wire MISO,
    output wire BUSY,
    output wire ICE_LED
);
    /* preserve the recovered core and its package-pin connections. */
    recovered recovered_core (
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
