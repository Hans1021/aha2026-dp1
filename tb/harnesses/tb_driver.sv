`timescale 1ns/1ps

/* test the shared driver against a mock, without recovered circuitry. */
module tb_driver;
    /* use the same processor interface and the same sole SCK owner. */
    reg SCK, RST_N, MOSI, NORM_CS_N, START, ENC_DEC;
    wire MISO, BUSY, ICE_LED;
    reg [31:0] response_word;
    wire [31:0] mock_word, frame_count, frame_bits;
    reg [31:0] received, tx_word, sent_word, random_state;
    reg [31:0] seed = 32'd4313;
    reg sampled_miso, sampled_busy;
    integer index, used_pulses, argument_found;
    string scenario = "normal";
    string argument_text;
    string name;

    /* independently observe wire framing rather than trusting mock telemetry. */
    reg monitor_ready = 1'b0;
    reg monitor_open = 1'b0;
    reg [31:0] monitor_word;
    integer monitor_bits, monitor_frames;

    /* open a passive frame before the next SCK edge. */
    always @(negedge NORM_CS_N) begin
        /* ignore startup before the first synchronous reset. */
        if (monitor_ready === 1'b1) begin
            /* require inactive SCK and a distinct selection edge. */
            if (SCK !== 1'b0 || monitor_open !== 1'b0) begin
                $fatal(1, "DRIVER FAIL monitor.frame_start");
            end
            monitor_open = 1'b1;
            monitor_bits = 0;
            monitor_word = 0;
        end
    end

    /* count actual selected edges and reconstruct MOSI independently. */
    always @(posedge SCK) begin
        /* synchronize observer reset with the peripheral reset. */
        if (RST_N === 1'b0) begin
            monitor_ready = 1'b1;
            monitor_open = 1'b0;
            monitor_bits = 0;
            monitor_frames = 0;
            monitor_word = 0;
        end else if (NORM_CS_N === 1'b0) begin
            /* reject a selected edge without a complete CS frame. */
            if (monitor_open !== 1'b1 || monitor_bits >= 32) begin
                $fatal(1, "DRIVER FAIL monitor.frame_overflow");
            end
            monitor_word = {monitor_word[30:0], MOSI};
            monitor_bits = monitor_bits + 1;
        end
    end

    /* reject premature deassertion and count complete wire frames. */
    always @(posedge NORM_CS_N) begin
        /* startup and inactive reset have no frame to close. */
        if (monitor_ready === 1'b1 && monitor_open === 1'b1) begin
            /* require all bits before closing a low-phase selection. */
            if (SCK !== 1'b0 || monitor_bits != 32) begin
                $fatal(1, "DRIVER FAIL monitor.frame_length bits=%0d", monitor_bits);
            end
            monitor_open = 1'b0;
            monitor_frames = monitor_frames + 1;
            $display("DRIVER FRAME number=%0d bits=%0d mosi=%h", monitor_frames, monitor_bits, monitor_word);
        end
    end

    /* configure mock MISO independently from each transmitted word. */

    /* commit combinational mock state after each settled interface event. */
    reg [106:0] mock_state_q = 107'd0;
    wire [106:0] mock_state_d;
    reg [1:0] mock_event = 2'd0;
    wire [3:0] mock_error;
    reg previous_sck = 1'b0;
    reg previous_cs = 1'b1;
    reg clock_rise, cs_change;
    reg [1:0] pending_event;
    reg [106:0] captured_state;

    /* update edge history before settling so falling edges are retained. */
    always @(SCK or NORM_CS_N) begin
        clock_rise = previous_sck === 1'b0 && SCK === 1'b1;
        cs_change = previous_cs !== NORM_CS_N;
        pending_event = 2'd0;
        /* forbid ambiguous simultaneous selection and sampling edges. */
        if (mock_state_q[106] === 1'b1 && clock_rise && cs_change)
            $fatal(1, "DRIVER FAIL mock.simultaneous_edges");
        /* encode selection changes or the sole SCK rising edge. */
        if (cs_change && mock_state_q[106] === 1'b1) begin
            if (NORM_CS_N === 1'b0) pending_event = 2'd1;
            else if (NORM_CS_N === 1'b1) pending_event = 2'd3;
            else $fatal(1, "DRIVER FAIL mock.unknown_cs");
        end else if (clock_rise) pending_event = 2'd2;
        previous_sck = SCK;
        previous_cs = NORM_CS_N;
        /* allow injected X/Z only in response storage and MISO. */
        if (pending_event != 0) begin
            if ((^{SCK,RST_N,MOSI,NORM_CS_N,START,ENC_DEC}) === 1'bx)
                $fatal(1, "DRIVER FAIL mock.unknown_control");
            if ((^{mock_state_q[106:105],mock_state_q[103:102],mock_state_q[69:0]}) === 1'bx)
                $fatal(1, "DRIVER FAIL mock.unknown_state");
            if (!RST_N && !NORM_CS_N)
                $fatal(1, "DRIVER FAIL mock.selected_reset");
            /* settle next-state logic, then commit with NBA before driver sampling. */
            mock_event = pending_event;
            #0.001;
            if (mock_error !== 4'd0)
                $fatal(1, "DRIVER FAIL mock.event error=%0d", mock_error);
            captured_state = mock_state_d;
            mock_state_q <= captured_state;
            mock_event <= 2'd0;
            #0.001;
        end
    end

    spi_mock_peripheral peripheral (
        .EVENT(mock_event), .STATE_Q(mock_state_q), .STATE_D(mock_state_d), .ERROR(mock_error),
        .SCK(SCK), .RST_N(RST_N), .MOSI(MOSI), .NORM_CS_N(NORM_CS_N),
        .START(START), .ENC_DEC(ENC_DEC), .MISO(MISO), .BUSY(BUSY),
        .ICE_LED(ICE_LED), .RESPONSE_WORD(response_word),
        .RECEIVED_WORD(mock_word), .FRAME_COUNT(frame_count), .FRAME_BITS(frame_bits)
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


    /* reverse all bits only for deliberate driver fault injection. */
    function automatic [31:0] reverse_bits(input reg [31:0] word_value);
        integer bit_index;
        begin
            /* preserve every position while reversing its index. */
            for (bit_index = 0; bit_index < 32; bit_index = bit_index + 1) begin
                reverse_bits[bit_index] = word_value[31-bit_index];
            end
        end
    endfunction

    /* reject malformed unsigned seeds before narrowing to 32 bits. */
    function automatic [31:0] parse_seed(input string digits);
        reg [63:0] accumulator;
        integer position;
        begin
            accumulator = 0;
            /* bound decimal parsing and reject empty input. */
            if (digits.len() < 1 || digits.len() > 10) begin
                $fatal(1, "DRIVER FAIL invalid seed length");
            end
            /* reject signs, unknowns, and overflow rather than truncating. */
            for (position = 0; position < digits.len(); position = position + 1) begin
                /* accept decimal digits only. */
                if (digits[position] < 48 || digits[position] > 57) begin
                    $fatal(1, "DRIVER FAIL invalid seed digit");
                end
                accumulator = accumulator * 10 + digits[position] - 48;
                /* retain the entire unsigned seed range. */
                if (accumulator > 64'd4294967295) begin
                    $fatal(1, "DRIVER FAIL seed overflow");
                end
            end
            parse_seed = accumulator[31:0];
        end
    endfunction

    /* create malformed selections using the existing pulse owner. */
    task automatic malformed_frame(input integer clocks);
        integer bit_index;
        begin
            NORM_CS_N = 1'b0;
            /* emit a bounded missing, extra, or split frame. */
            for (bit_index = 0; bit_index < clocks; bit_index = bit_index + 1) begin
                MOSI = tx_word[31-(bit_index % 32)];
                sck_pulse(sampled_miso, sampled_busy);
            end
            NORM_CS_N = 1'b1;
            #1;  // allow the framing monitor to report this edge
            $fatal(1, "DRIVER FAIL framing fault escaped mock");
        end
    endtask

    /* bound every run independently of the host process timeout. */
    initial begin
        #20000000;
        $fatal(1, "DRIVER FAIL watchdog exceeded 20 ms");
    end

    /* sequence all stimulus below rising SCK; no background clock is used. */
    initial begin
        $display("DRIVER TEST tb_driver: mock validates harness only, not recovered cryptography");
        $dumpfile("waveforms.vcd");
        $dumpvars(0, tb_driver);
        initialize_interface();
        response_word = 32'hd2963a5c;
        argument_found = $value$plusargs("seed=%s", argument_text);
        /* retain the documented seed when no override is supplied. */
        if (argument_found != 0) begin
            seed = parse_seed(argument_text);
        end
        argument_found = $value$plusargs("scenario=%s", scenario);
        random_state = seed;
        $display("DRIVER TEST configuration seed=%0d scenario=%s", seed, scenario);
        apply_synchronous_reset("driver.reset");
        tx_word = 32'h01234567;

        /* prove the existing scoreboard rejects wrong and unknown expectations. */
        if (scenario == "mismatch") begin
            local_check_word32("driver.negative.mismatch", 32'h12345678, 32'h12345679);
        end else if (scenario == "unknown_x") begin
            local_check_word32("driver.negative.unknown_x", 32'hxxxxxxxx, 32'hxxxxxxxx);
        end else if (scenario == "unknown_z") begin
            local_check_word32("driver.negative.unknown_z", 32'hzzzzzzzz, 32'hzzzzzzzz);
        end else if (scenario == "short_frame") begin
            malformed_frame(31);
        end else if (scenario == "long_frame") begin
            malformed_frame(33);
        end else if (scenario == "split_frame") begin
            malformed_frame(16);
        end else if (scenario == "unknown_miso_x") begin
            response_word = 32'hx2963a5c;
        end else if (scenario == "unknown_miso_z") begin
            response_word = 32'hz2963a5c;
        end else if (scenario != "normal" && scenario != "reverse_tx" &&
                     scenario != "reverse_rx" && scenario != "timeout") begin
            $fatal(1, "DRIVER FAIL unknown scenario");
        end

        /* check six directed patterns and eight reproducible LCG patterns. */
        for (index = 0; index < 14; index = index + 1) begin
            name = $sformatf("driver.frame.%0d", index);
            /* sample the maintained reference once per frame. */
            reference_index = index[3:0];
            reference_rng = random_state;
            #1;
            /* reject unknown vectors before passing them to generated operations. */
            if (reference_frame_valid !== 1'b1 ||
                (^{reference_tx,reference_response,reference_rng_next}) === 1'bx)
                $fatal(1, "DRIVER FAIL reference.unknown");
            tx_word = reference_tx;
            /* advance the LCG only for random frames. */
            if (index >= 6) random_state = reference_rng_next;
            /* preserve deliberate unknown-MISO fault injection. */
            if (scenario != "unknown_miso_x" && scenario != "unknown_miso_z")
                response_word = reference_response;
            sent_word = tx_word;
            /* introduce a serial-order defect without changing the expected word. */
            if (scenario == "reverse_tx") begin
                sent_word = reverse_bits(tx_word);
            end
            $display("DRIVER TEST %s tx=%h response=%h", name, tx_word, response_word);
            /* readout must still transmit and update the mock's input register. */
            if (index == 5) begin
                read_result(name, sent_word, received);
            end else begin
                transfer_word32(name, sent_word, received);
            end
            #1;  // settle the independent CS edge monitor before inspecting telemetry
            /* expose reversed read assembly to the unchanged scoreboard. */
            if (scenario == "reverse_rx") begin
                received = reverse_bits(received);
            end
            local_check_word32({name, ".rx"}, received, response_word);
            local_check_word32({name, ".tx"}, mock_word, tx_word);
            local_check_word32({name, ".wire"}, monitor_word, tx_word);
            local_check_word32({name, ".frames"}, frame_count, index + 1);
            local_check_word32({name, ".bits"}, frame_bits, 32'd32);
        end

        /* clock while deselected and prove no phantom frame or input shift occurs. */
        MOSI = ~MOSI;
        repeat (3) begin
            sck_pulse(sampled_miso, sampled_busy);
        end
        local_check_word32("driver.deselected.rx", mock_word, tx_word);
        local_check_word32("driver.deselected.frames", frame_count, 32'd14);

        /* exercise arbitrary mock completion while continuing to supply SCK. */
        pulse_start("driver.mock_compute", 1'b1);
        /* an intentionally short limit must fail before the mock's completion. */
        if (scenario == "timeout") begin
            clock_until_complete("driver.negative.timeout", 1, 1'b0, used_pulses);
        end else begin
            clock_until_complete("driver.mock_compute", 4, 1'b0, used_pulses);
        end
        /* check exact pulse and scoreboard counts before declaring driver success. */
        if (used_pulses != 3 || checks != 75 || total_pulses != 457 || monitor_frames != 14) begin
            $fatal(1, "DRIVER FAIL unexpected accounting checks=%0d pulses=%0d", checks, total_pulses);
        end
        $display("DRIVER SUMMARY PASS checks=%0d frames=%0d total_pulses=%0d seed=%0d",
            checks, frame_count, total_pulses, seed);
        $finish;
    end
endmodule
