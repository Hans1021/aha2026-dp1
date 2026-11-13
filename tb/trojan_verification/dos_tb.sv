`timescale 1ns/1ps
// ---------------------------------------------------------------------------
// dos_tb: exercises the denial-of-service Trojan
// (rtl/trojans/dos/attempt_007.sv).
//
//   Trigger : a complete 32-bit SPI transaction equal to 0xBAADF00D.
//   Payload : BUSY and ICE_LED are forced high and stay high (independently of
//             the core) until a synchronous reset.
//
// Checks:
//   - BUSY/ICE_LED latch high after the trigger word
//   - the stuck state survives extra SCK pulses AND a period with no clock
//   - RST_N clears the Trojan and the core encrypts correctly afterward
//   - a one-bit-off near-miss (0xBAADF00C) never latches BUSY
// ---------------------------------------------------------------------------
module dos_tb;
    reg SCK, RST_N, MOSI, NORM_CS_N, START, ENC_DEC;
    wire MISO, BUSY, ICE_LED;
    reg [31:0] rx;
    reg m, b;
    integer k, used;

    crypto_dut dut (
        .SCK(SCK), .RST_N(RST_N), .MOSI(MOSI), .MISO(MISO),
        .NORM_CS_N(NORM_CS_N), .START(START), .ENC_DEC(ENC_DEC),
        .BUSY(BUSY), .ICE_LED(ICE_LED)
    );
    `include "spi_operations.svh"

    localparam [31:0] TRIG = 32'hBAADF00D;
    localparam [31:0] NEAR = 32'hBAADF00C;   // one bit off -> must not trigger

    // BUSY and ICE_LED are mirrored; check them together.
    task automatic check_dos(input string name, input reg expect_high);
        begin
            if (BUSY !== expect_high) begin
                $display("FAIL %s BUSY=%b expected=%b", name, BUSY, expect_high);
                $fatal(1, "dos busy mismatch");
            end
            if (ICE_LED !== expect_high) begin
                $display("FAIL %s ICE_LED=%b expected=%b", name, ICE_LED, expect_high);
                $fatal(1, "dos led mismatch");
            end
            checks = checks + 1;
            $display("PASS %s busy=%b led=%b", name, BUSY, ICE_LED);
        end
    endtask

    initial begin #100000000; $fatal(1, "FAIL dos.watchdog"); end

    initial begin
        $dumpfile("dos.vcd");
        $dumpvars(0, dos_tb);
        initialize_interface();

        // ===== 1. TRIGGER: BUSY/ICE_LED latch high =====
        apply_synchronous_reset("dos.reset");
        check_dos("dos.idle", 1'b0);
        transfer_word32("dos.trigger", TRIG, rx);           // 0xBAADF00D
        check_dos("dos.after_trigger", 1'b1);               // latched high
        for (k = 0; k < 5; k = k + 1) begin                 // survives more clocks
            sck_pulse(m, b);
            check_dos($sformatf("dos.persist.clk%0d", k), 1'b1);
        end
        #50000;                                             // survives with NO clock
        check_dos("dos.persist.noclock", 1'b1);

        // ===== 2. RESET recovers the device =====
        apply_synchronous_reset("dos.recover");
        check_dos("dos.after_reset", 1'b0);
        set_mode(1'b0);
        transfer_word32("dos.enc.load", 32'h59c359c3, rx);
        pulse_start("dos.encrypt", 1'b1);
        clock_until_complete("dos.encrypt", 7, 1'b1, used);
        read_result("dos.enc.read", 32'h59c359c3, rx);
        check_word32("dos.enc.result", rx, 32'h9cd84392);   // core alive again

        // ===== 3. NON-TRIGGER (near miss) never latches =====
        apply_synchronous_reset("dos.nt.reset");
        transfer_word32("dos.nt.word", NEAR, rx);           // 0xBAADF00C
        check_dos("dos.nt.after", 1'b0);                    // stays low
        transfer_word32("dos.nt.again", 32'h12345678, rx);  // core still usable
        check_dos("dos.nt.again", 1'b0);

        $display("DOS SUMMARY PASS checks=%0d total_pulses=%0d", checks, total_pulses);
        $finish;
    end
endmodule
