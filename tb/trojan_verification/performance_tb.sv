`timescale 1ns/1ps
// ---------------------------------------------------------------------------
// performance_tb: exercises the performance-degradation Trojan
// (rtl/trojans/performance/attempt_010.sv).
//
//   Trigger : the 4th START operation after reset.
//   Payload : externally visible BUSY is held high for 8 additional SCK cycles
//             after the core finishes; earlier and later operations are normal.
//
// Method: issue six consecutive START operations without an intervening reset
// (so the Trojan's internal START counter advances 1..6) and measure how many
// SCK cycles BUSY stays high after each START pulse.
//   normal operation  -> BUSY high for 3 cycles
//   4th operation      -> BUSY high for 3 + 8 = 11 cycles
// ---------------------------------------------------------------------------
module performance_tb;
    reg SCK, RST_N, MOSI, NORM_CS_N, START, ENC_DEC;
    wire MISO, BUSY, ICE_LED;
    reg [31:0] rx;
    reg m, b;
    integer op, dur;
    integer dur_hist [1:6];

    crypto_dut dut (
        .SCK(SCK), .RST_N(RST_N), .MOSI(MOSI), .MISO(MISO),
        .NORM_CS_N(NORM_CS_N), .START(START), .ENC_DEC(ENC_DEC),
        .BUSY(BUSY), .ICE_LED(ICE_LED)
    );
    `include "spi_operations.svh"

    // Count SCK pulses from just after the START pulse until BUSY falls.
    task automatic measure_busy_high(input string name, input integer maxp, output integer d);
        begin
            d = 0;
            while (BUSY === 1'b1 && d < maxp) begin
                sck_pulse(m, b);
                d = d + 1;
            end
            if (BUSY !== 1'b0) $fatal(1, "FAIL %s busy_never_fell", name);
            $display("PERF %s busy_high_cycles=%0d", name, d);
        end
    endtask

    localparam [31:0] P = 32'h59c359c3;

    initial begin #100000000; $fatal(1, "FAIL perf.watchdog"); end

    initial begin
        $dumpfile("performance.vcd");
        $dumpvars(0, performance_tb);
        initialize_interface();

        // One reset, then six START operations with no reset between them.
        apply_synchronous_reset("perf.reset");
        set_mode(1'b0);
        for (op = 1; op <= 6; op = op + 1) begin
            transfer_word32($sformatf("perf.op%0d.load", op), P, rx);
            pulse_start($sformatf("perf.op%0d", op), 1'b1);
            measure_busy_high($sformatf("perf.op%0d", op), 64, dur);
            dur_hist[op] = dur;
            read_result($sformatf("perf.op%0d.read", op), P, rx);
        end

        // ops 1,2,3,5,6 must be normal (3); op4 must be extended by 8 (11).
        if (dur_hist[1] != 3) $fatal(1, "FAIL perf.op1 dur=%0d", dur_hist[1]);
        if (dur_hist[2] != 3) $fatal(1, "FAIL perf.op2 dur=%0d", dur_hist[2]);
        if (dur_hist[3] != 3) $fatal(1, "FAIL perf.op3 dur=%0d", dur_hist[3]);
        if (dur_hist[4] != dur_hist[3] + 8)
            $fatal(1, "FAIL perf.op4 expected %0d got %0d", dur_hist[3] + 8, dur_hist[4]);
        if (dur_hist[5] != 3) $fatal(1, "FAIL perf.op5 dur=%0d", dur_hist[5]);
        if (dur_hist[6] != 3) $fatal(1, "FAIL perf.op6 dur=%0d", dur_hist[6]);

        $display("PERF DURATIONS op1..op6 = %0d %0d %0d %0d %0d %0d",
                 dur_hist[1], dur_hist[2], dur_hist[3], dur_hist[4], dur_hist[5], dur_hist[6]);
        $display("PERF op4 extra cycles = %0d", dur_hist[4] - dur_hist[3]);
        checks = checks + 6;

        $display("PERFORMANCE SUMMARY PASS checks=%0d total_pulses=%0d", checks, total_pulses);
        $finish;
    end
endmodule
