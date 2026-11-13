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
    /* use fixed vectors and timing expectations independent of generated HDL. */

    reg [3:0] reference_index = 0, reference_cutoff = 0;
    reg [31:0] reference_rng = 0;
    reg reference_mode = 0;
    wire reference_frame_valid, reference_timing_valid, reference_busy, reference_ready;
    wire [31:0] reference_tx, reference_response, reference_rng_next;
    wire [31:0] reference_input, reference_result, reference_read;
    spi_harness_controller reference_controller (
        .INDEX(reference_index), .RNG_STATE(reference_rng), .ENC_DEC(reference_mode), .CUTOFF(reference_cutoff),
        .FRAME_VALID(reference_frame_valid), .TX_WORD(reference_tx), .RESPONSE_WORD(reference_response),
        .RNG_NEXT(reference_rng_next), .TIMING_VALID(reference_timing_valid),
        .INPUT_WORD(reference_input), .RESULT_WORD(reference_result), .READ_WORD(reference_read),
        .BUSY_EXPECTED(reference_busy), .RESULT_READY(reference_ready)
    );

    `include "spi_operations.svh"

    /* compare known values locally, independently of generated checker tasks. */
    task automatic local_check_word32(input string check_name, input reg [31:0] actual,
                                       input reg [31:0] expected);
        begin
            /* case comparison alone could accept matching unknown values. */
            if ((^actual) === 1'bx || (^expected) === 1'bx || actual !== expected) begin
                $display("FAIL %s expected=%h actual=%h", check_name, expected, actual);
                $fatal(1, "maintained scoreboard mismatch");
            end
            checks = checks + 1;
            $display("PASS %s value=%h", check_name, actual);
        end
    endtask

    /* sample BUSY locally; unknown values cannot satisfy timing checks. */
    task automatic local_check_busy(input string check_name, input reg expected);
        begin
            if ((expected !== 1'b0 && expected !== 1'b1) || BUSY !== expected) begin
                $display("FAIL %s expected_busy=%b actual_busy=%b", check_name, expected, BUSY);
                $fatal(1, "maintained BUSY mismatch");
            end
            checks = checks + 1;
            $display("PASS %s busy=%b", check_name, BUSY);
        end
    endtask


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
        local_check_busy("timing.start.idle", 1'b0);
        START = 1'b1;
        #2;
        local_check_busy("timing.start.no_clock_high", 1'b1);
        $display("TIMING START phase=assert_no_clock busy=%b", BUSY);
        START = 1'b0;
        #2;
        local_check_busy("timing.start.no_clock_release", 1'b0);
        $display("TIMING START phase=release_no_clock busy=%b", BUSY);

        /* latch an operation, then demonstrate reset masking without state reset. */
        pulse_start("timing.reset_busy", 1'b1);
        #2;
        local_check_busy("timing.reset_busy.accepted", 1'b1);
        RST_N = 1'b0;
        #2;
        local_check_busy("timing.reset_busy.masked", 1'b0);
        $display("TIMING RESET_ACTIVE phase=assert_no_clock busy=%b", BUSY);
        RST_N = 1'b1;
        #2;
        local_check_busy("timing.reset_busy.restored", 1'b1);
        $display("TIMING RESET_ACTIVE phase=release_no_clock busy=%b", BUSY);
        RST_N = 1'b0;
        sck_pulse(sampled_miso, sampled_busy);
        local_check_busy("timing.reset_busy.clocked", 1'b0);
        RST_N = 1'b1;
        #2;
        local_check_busy("timing.reset_busy.released", 1'b0);
        $display("TIMING RESET_ACTIVE phase=release_after_reset_clock busy=%b", BUSY);
        sck_pulse(sampled_miso, sampled_busy);

        /* load ones so reset persistence and the MISO register lag are observable. */
        transfer_word32("timing.reset_data.load", 32'hffffffff, ignored);
        read_result("timing.reset_data.echo", 32'hffffffff, received);
        local_check_word32("timing.reset_data.echo", received, 32'hffffffff);
        check_miso("timing.reset_data.before", 1'b1);
        RST_N = 1'b0;
        #2;
        check_miso("timing.reset_data.no_clock", 1'b1);
        RST_N = 1'b1;
        #2;
        read_result("timing.reset_data.no_clock_read", 32'hffffffff, received);
        local_check_word32("timing.reset_data.no_clock_read", received, 32'hffffffff);
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
        local_check_word32("timing.reset_data.cleared", received, 32'h00000000);
        $display("TIMING RESET_DATA phase=clocked_reset word=%h", received);

        /* measure each mode independently with exact supplied input/output pairs. */
        for (mode_index = 0; mode_index < 2; mode_index = mode_index + 1) begin
            operation_name = mode_index == 0 ? "encrypt" : "decrypt";
            reference_mode = mode_index[0];
            reference_cutoff = 0;
            #1;
            /* reject unknown local references before starting either mode. */
            if (reference_timing_valid !== 1'b1 || (^{reference_input,reference_result}) === 1'bx)
                $fatal(1, "FAIL timing.reference_unknown");
            input_word = reference_input;
            expected_word = reference_result;
            apply_synchronous_reset({"timing.", operation_name, ".reset"});
            set_mode(mode_index[0]);
            transfer_word32({"timing.", operation_name, ".load"}, input_word, ignored);
            local_check_busy({"timing.", operation_name, ".idle"}, 1'b0);
            $display("TIMING BUSY mode=%s edge=-1 busy=%b", operation_name, BUSY);
            pulse_start({"timing.", operation_name}, 1'b1);
            #2;
            local_check_busy({"timing.", operation_name, ".accepted"}, 1'b1);
            $display("TIMING BUSY mode=%s edge=0 busy=%b", operation_name, BUSY);
            /* preserve all seven application clocks while observing completion. */
            for (edge_index = 1; edge_index <= 7; edge_index = edge_index + 1) begin
                reference_cutoff = edge_index[3:0];
                #1;
                sck_pulse(sampled_miso, sampled_busy);
                local_check_busy($sformatf("timing.%s.edge.%0d", operation_name, edge_index), reference_busy);
                $display("TIMING BUSY mode=%s edge=%0d busy=%b", operation_name, edge_index, BUSY);
            end
            read_result({"timing.", operation_name, ".result"}, input_word, received);
            local_check_word32({"timing.", operation_name, ".result"}, received, expected_word);

            /* reset and reload before every attempted read boundary. */
            for (cutoff = 0; cutoff <= 7; cutoff = cutoff + 1) begin
                reference_cutoff = cutoff[3:0];
                #1;
                /* keep read-boundary expectations independent of observed DUT values. */
                if (reference_timing_valid !== 1'b1 || (^{reference_read,reference_busy,reference_ready}) === 1'bx)
                    $fatal(1, "FAIL timing.boundary_unknown");
                trial_name = $sformatf("timing.%s.after_e%0d", operation_name, cutoff);
                apply_synchronous_reset({trial_name, ".reset"});
                set_mode(mode_index[0]);
                transfer_word32({trial_name, ".load"}, input_word, ignored);
                pulse_start(trial_name, 1'b1);
                #2;
                local_check_busy({trial_name, ".accepted"}, 1'b1);
                /* hold CS inactive for exactly the chosen number of processing edges. */
                for (edge_index = 1; edge_index <= cutoff; edge_index = edge_index + 1) begin
                    sck_pulse(sampled_miso, sampled_busy);
                end
                busy_before_read = BUSY;
                probe_read(input_word, received);
                /* early CS selection suppresses loading; e4 is the first valid boundary. */
                local_check_word32(trial_name, received, reference_read);
                $display("TIMING AVAIL mode=%s after_edge=%0d busy_before=%b word=%h expected=%h match=%0d",
                    operation_name, cutoff, busy_before_read, received, expected_word, received === expected_word);
            end
        end

        /* emit one explicit success only after every four-state check passed. */
        $display("TIMING SUMMARY PASS checks=%0d total_pulses=%0d", checks, total_pulses);
        $finish;
    end
endmodule
