`timescale 1ns/1ps

/* measure reset and result timing separately from the 180-pulse application. */
module baseline_timing_tb;
    /* share the processor interface and sole SCK owner with the baseline. */
    reg SCK, RST_N, MOSI, NORM_CS_N, START, ENC_DEC;
    wire MISO, BUSY, ICE_LED;
    reg sampled_miso, sampled_busy;
    reg [31:0] ignored, received, input_word, expected_word;
    reg busy_before_read;
    integer mode_index, edge_index, cutoff;
    string operation_name, trial_name;

    /* exercise actual recovered pins without internal forcing or mock logic. */
    crypto_dut dut (
        .SCK(SCK), .RST_N(RST_N), .MOSI(MOSI), .MISO(MISO),
        .NORM_CS_N(NORM_CS_N), .START(START), .ENC_DEC(ENC_DEC),
        .BUSY(BUSY), .ICE_LED(ICE_LED)
    );
    `include "spi_operations.svh"

    /* check registered MISO without permitting unknown values. */
    task automatic check_miso(input string name, input reg expected);
        begin
            /* reject unknown expectations and sampled outputs. */
            if ((expected !== 1'b0 && expected !== 1'b1) || MISO !== expected) begin
                $display("FAIL %s expected_miso=%b actual_miso=%b", name, expected, MISO);
                $fatal(1, "reset MISO mismatch");
            end
            checks = checks + 1;
            $display("PASS %s miso=%b", name, MISO);
        end
    endtask

    /* deliberately read before completion to measure read-boundary behavior. */
    task automatic probe_read(input reg [31:0] tx, output reg [31:0] rx);
        integer bit_index;
        reg serial_bit, status_bit;
        begin
            require_idle_clock("timing.probe_read");
            NORM_CS_N = 1'b0;
            rx = 0;
            /* these read clocks also advance computation; they are not passive. */
            for (bit_index = 31; bit_index >= 0; bit_index = bit_index - 1) begin
                MOSI = tx[bit_index];
                sck_pulse(serial_bit, status_bit);
                /* every checked serial bit must be known. */
                if (serial_bit !== 1'b0 && serial_bit !== 1'b1) begin
                    $display("FAIL timing.probe_read unknown bit=%0d", bit_index);
                    $fatal(1, "unknown timing read");
                end
                rx[bit_index] = serial_bit;
            end
            NORM_CS_N = 1'b1;
        end
    endtask

    /* cap all timing trials even if a probe stops emitting clocks. */
    initial begin
        #100000000;
        $fatal(1, "FAIL timing.watchdog exceeded 100 ms");
    end

    /* drive only during low SCK and settle combinational sidebands explicitly. */
    initial begin
        $dumpfile("waveforms.vcd");
        $dumpvars(0, baseline_timing_tb);
        initialize_interface();
        apply_synchronous_reset("timing.initial_reset");

        /* observe that START alone changes BUSY without proving accepted work. */
        transfer_word32("timing.reset_busy.load", 32'h59c359c3, ignored);
        check_busy("timing.start.idle", 1'b0);
        START = 1'b1;
        #2;
        check_busy("timing.start.no_clock_high", 1'b1);
        $display("TIMING START phase=assert_no_clock busy=%b", BUSY);
        START = 1'b0;
        #2;
        check_busy("timing.start.no_clock_release", 1'b0);
        $display("TIMING START phase=release_no_clock busy=%b", BUSY);

        /* latch an operation, then demonstrate reset masking without state reset. */
        pulse_start("timing.reset_busy", 1'b1);
        #2;
        check_busy("timing.reset_busy.accepted", 1'b1);
        RST_N = 1'b0;
        #2;
        check_busy("timing.reset_busy.masked", 1'b0);
        $display("TIMING RESET_ACTIVE phase=assert_no_clock busy=%b", BUSY);
        RST_N = 1'b1;
        #2;
        check_busy("timing.reset_busy.restored", 1'b1);
        $display("TIMING RESET_ACTIVE phase=release_no_clock busy=%b", BUSY);
        RST_N = 1'b0;
        sck_pulse(sampled_miso, sampled_busy);
        check_busy("timing.reset_busy.clocked", 1'b0);
        RST_N = 1'b1;
        #2;
        check_busy("timing.reset_busy.released", 1'b0);
        $display("TIMING RESET_ACTIVE phase=release_after_reset_clock busy=%b", BUSY);
        sck_pulse(sampled_miso, sampled_busy);

        /* load ones so reset persistence and the MISO register lag are observable. */
        transfer_word32("timing.reset_data.load", 32'hffffffff, ignored);
        read_result("timing.reset_data.echo", 32'hffffffff, received);
        check_word32("timing.reset_data.echo", received, 32'hffffffff);
        check_miso("timing.reset_data.before", 1'b1);
        RST_N = 1'b0;
        #2;
        check_miso("timing.reset_data.no_clock", 1'b1);
        RST_N = 1'b1;
        #2;
        read_result("timing.reset_data.no_clock_read", 32'hffffffff, received);
        check_word32("timing.reset_data.no_clock_read", received, 32'hffffffff);
        $display("TIMING RESET_DATA phase=unclocked_reset word=%h", received);
        RST_N = 1'b0;
        sck_pulse(sampled_miso, sampled_busy);
        check_miso("timing.reset_data.first_edge", 1'b1);
        $display("TIMING RESET_DATA phase=reset_edge miso=%b", MISO);
        RST_N = 1'b1;
        sck_pulse(sampled_miso, sampled_busy);
        check_miso("timing.reset_data.release_edge", 1'b0);
        $display("TIMING RESET_DATA phase=release_edge miso=%b", MISO);
        read_result("timing.reset_data.cleared", 32'h00000000, received);
        check_word32("timing.reset_data.cleared", received, 32'h00000000);
        $display("TIMING RESET_DATA phase=clocked_reset word=%h", received);

        /* measure each mode independently with exact supplied input/output pairs. */
        for (mode_index = 0; mode_index < 2; mode_index = mode_index + 1) begin
            operation_name = mode_index == 0 ? "encrypt" : "decrypt";
            input_word = mode_index == 0 ? 32'h59c359c3 : 32'h9cd84392;
            expected_word = mode_index == 0 ? 32'h9cd84392 : 32'h59c359c3;
            apply_synchronous_reset({"timing.", operation_name, ".reset"});
            set_mode(mode_index[0]);
            transfer_word32({"timing.", operation_name, ".load"}, input_word, ignored);
            check_busy({"timing.", operation_name, ".idle"}, 1'b0);
            $display("TIMING BUSY mode=%s edge=-1 busy=%b", operation_name, BUSY);
            pulse_start({"timing.", operation_name}, 1'b1);
            #2;
            check_busy({"timing.", operation_name, ".accepted"}, 1'b1);
            $display("TIMING BUSY mode=%s edge=0 busy=%b", operation_name, BUSY);
            /* preserve all seven application clocks while observing completion. */
            for (edge_index = 1; edge_index <= 7; edge_index = edge_index + 1) begin
                sck_pulse(sampled_miso, sampled_busy);
                check_busy($sformatf("timing.%s.edge.%0d", operation_name, edge_index), edge_index < 3);
                $display("TIMING BUSY mode=%s edge=%0d busy=%b", operation_name, edge_index, BUSY);
            end
            read_result({"timing.", operation_name, ".result"}, input_word, received);
            check_word32({"timing.", operation_name, ".result"}, received, expected_word);

            /* reset and reload before every attempted read boundary. */
            for (cutoff = 0; cutoff <= 7; cutoff = cutoff + 1) begin
                trial_name = $sformatf("timing.%s.after_e%0d", operation_name, cutoff);
                apply_synchronous_reset({trial_name, ".reset"});
                set_mode(mode_index[0]);
                transfer_word32({trial_name, ".load"}, input_word, ignored);
                pulse_start(trial_name, 1'b1);
                #2;
                check_busy({trial_name, ".accepted"}, 1'b1);
                /* hold CS inactive for exactly the chosen number of processing edges. */
                for (edge_index = 1; edge_index <= cutoff; edge_index = edge_index + 1) begin
                    sck_pulse(sampled_miso, sampled_busy);
                end
                busy_before_read = BUSY;
                probe_read(input_word, received);
                /* early CS selection suppresses loading; e4 is the first valid boundary. */
                check_word32(trial_name, received, cutoff < 4 ? input_word : expected_word);
                $display("TIMING AVAIL mode=%s after_edge=%0d busy_before=%b word=%h expected=%h match=%0d",
                    operation_name, cutoff, busy_before_read, received, expected_word, received === expected_word);
            end
        end

        /* emit one explicit success only after every four-state check passed. */
        $display("TIMING SUMMARY PASS checks=%0d total_pulses=%0d", checks, total_pulses);
        $finish;
    end
endmodule
