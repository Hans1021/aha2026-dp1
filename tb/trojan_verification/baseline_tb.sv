`timescale 1ns/1ps
// ---------------------------------------------------------------------------
// baseline_tb: normal-operation check. Drives a full encrypt and a full
// decrypt and confirms the supplied golden vectors
//     encrypt(0x59C359C3) = 0x9CD84392      decrypt(0x9CD84392) = 0x59C359C3
// Because no Trojan trigger word is ever transmitted, this testbench passes
// identically on the clean recovered core and on every Trojan variant, proving
// that each Trojan leaves the cryptographic datapath intact during normal use.
// ---------------------------------------------------------------------------
module baseline_tb;
    reg SCK, RST_N, MOSI, NORM_CS_N, START, ENC_DEC;
    wire MISO, BUSY, ICE_LED;
    reg [31:0] rx;
    integer used;

    crypto_dut dut (
        .SCK(SCK), .RST_N(RST_N), .MOSI(MOSI), .MISO(MISO),
        .NORM_CS_N(NORM_CS_N), .START(START), .ENC_DEC(ENC_DEC),
        .BUSY(BUSY), .ICE_LED(ICE_LED)
    );

    `include "spi_operations.svh"

    localparam [31:0] P = 32'h59c359c3;   // plaintext
    localparam [31:0] C = 32'h9cd84392;   // ciphertext

    initial begin #100000000; $fatal(1, "FAIL baseline.watchdog"); end

    initial begin
        $dumpfile("baseline.vcd");
        $dumpvars(0, baseline_tb);
        initialize_interface();

        // ---- Encrypt ----
        apply_synchronous_reset("enc.reset");
        set_mode(1'b0);                              // 0 = encrypt
        transfer_word32("enc.load", P, rx);          // ingest plaintext
        read_result("enc.echo.read", P, rx);
        check_word32("enc.echo", rx, P);             // SPI loopback returns P
        pulse_start("encrypt", 1'b1);
        clock_until_complete("encrypt", 7, 1'b1, used);
        read_result("enc.result.read", P, rx);
        check_word32("enc.result", rx, C);           // ciphertext

        // ---- Decrypt ----
        set_mode(1'b1);                              // 1 = decrypt
        apply_synchronous_reset("dec.reset");
        transfer_word32("dec.load", C, rx);          // ingest ciphertext
        pulse_start("decrypt", 1'b0);
        clock_until_complete("decrypt", 7, 1'b1, used);
        read_result("dec.result.read", C, rx);
        check_word32("dec.result", rx, P);           // plaintext

        $display("BASELINE SUMMARY PASS checks=%0d total_pulses=%0d", checks, total_pulses);
        $finish;
    end
endmodule
