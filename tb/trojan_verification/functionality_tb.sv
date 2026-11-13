`timescale 1ns/1ps
// ---------------------------------------------------------------------------
// functionality_tb: exercises the functionality-corruption Trojan
// (rtl/trojans/functionality/attempt_009.sv).
//
//   Trigger : a complete 32-bit SPI transaction equal to 0xA6F03C59.
//   Payload : every MISO bit of the NEXT transaction is inverted, then the
//             Trojan returns to normal (one-shot).
//
// Observation method: the accelerator's SPI port is a 32-bit loopback, so the
// word read back during transaction N equals the word shifted in during
// transaction N-1. That gives a known MISO value to compare against.
//   - trigger transaction itself must be UNAFFECTED (echoes the prior word)
//   - the single following transaction must echo ~0xA6F03C59 = 0x590FC3A6
//   - the transaction after that must be back to normal
// A one-bit-off near-miss (0xA6F03C58) must NOT corrupt anything.
// ---------------------------------------------------------------------------
module functionality_tb;
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

    localparam [31:0] TRIG = 32'hA6F03C59;
    localparam [31:0] NEAR = 32'hA6F03C58;   // one bit off -> must not trigger

    initial begin #100000000; $fatal(1, "FAIL func.watchdog"); end

    initial begin
        $dumpfile("functionality.vcd");
        $dumpvars(0, functionality_tb);
        initialize_interface();

        // ===== 1. TRIGGER + PAYLOAD =====
        apply_synchronous_reset("func.reset");
        transfer_word32("func.prime",   32'h11111111, rx);  // flush loopback
        transfer_word32("func.trigger", TRIG,         rx);  // arms; this txn unaffected
        check_word32("func.trigger.echo_normal", rx, 32'h11111111);
        transfer_word32("func.payload", 32'hDEADBEEF, rx);  // echo of TRIG, inverted
        check_word32("func.payload.inverted", rx, ~TRIG);   // 0x590FC3A6
        $display("FUNC trigger=%08h payload_echo=%08h expected_inverted=%08h",
                 TRIG, rx, ~TRIG);
        transfer_word32("func.after",   32'h00000000, rx);  // one-shot -> normal again
        check_word32("func.after.normal", rx, 32'hDEADBEEF);

        // ===== 2. NON-TRIGGER (near miss, 1 bit off) =====
        apply_synchronous_reset("func.nt.reset");
        transfer_word32("func.nt.prime", 32'h22222222, rx);
        transfer_word32("func.nt.word",  NEAR,         rx);  // does NOT arm
        check_word32("func.nt.word.echo", rx, 32'h22222222);
        transfer_word32("func.nt.next",  32'h33333333, rx);  // must be NORMAL
        check_word32("func.nt.next.normal", rx, NEAR);
        $display("FUNC non_trigger=%08h next_echo=%08h expected_normal=%08h",
                 NEAR, rx, NEAR);

        // ===== 3. normal MISO path still correct (datapath preserved) =====
        apply_synchronous_reset("func.enc.reset");
        set_mode(1'b0);
        transfer_word32("func.enc.load", 32'h59c359c3, rx);
        pulse_start("func.encrypt", 1'b1);
        clock_until_complete("func.encrypt", 7, 1'b1, used);
        read_result("func.enc.read", 32'h59c359c3, rx);
        check_word32("func.enc.result", rx, 32'h9cd84392);

        $display("FUNCTIONALITY SUMMARY PASS checks=%0d total_pulses=%0d", checks, total_pulses);
        $finish;
    end
endmodule
