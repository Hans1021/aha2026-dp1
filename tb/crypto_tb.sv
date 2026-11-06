`timescale 1ns/1ps

/* replay the application and add seeded SPI echoes without a crypto oracle. */
module crypto_tb;
    /* declare processor-driven controls and sampled FPGA outputs. */
    reg SCK;
    reg RST_N;
    reg MOSI;
    reg NORM_CS_N;
    reg START;
    reg ENC_DEC;
    wire MISO;
    wire BUSY;
    wire ICE_LED;

    /* retain fixed vectors and deterministic 32-bit random state. */
    localparam [31:0] PLAINTEXT = 32'h59c359c3;
    localparam [31:0] CIPHERTEXT = 32'h9cd84392;
    reg [31:0] seed = 32'd4313;
    reg [31:0] random_state;
    reg [31:0] random_word;
    reg [31:0] received;
    reg [31:0] ignored;
    reg [31:0] trial_argument;
    integer random_trials = 8;
    integer trial;
    integer baseline_pulses;
    integer used_pulses;
    integer argument_found;
    string argument_text;
    string trial_name;

    /* connect only through documented interface names. */
    crypto_dut dut (
        .SCK(SCK), .RST_N(RST_N), .MOSI(MOSI), .MISO(MISO),
        .NORM_CS_N(NORM_CS_N), .START(START), .ENC_DEC(ENC_DEC),
        .BUSY(BUSY), .ICE_LED(ICE_LED)
    );

    /* share the sole clock owner and reusable bus operations. */
    `include "spi_operations.svh"

    /* reject malformed, signed, or overflowing decimal plusargs. */
    function automatic [31:0] decimal_u32(input string name, input string digits);
        reg [63:0] accumulator;
        integer index;
        /* parse explicitly rather than depending on simulator truncation. */
        begin
            accumulator = 64'd0;
            /* bound the parser and reject empty arguments. */
            if (digits.len() < 1 || digits.len() > 10) begin
                $display("FAIL arguments invalid %s length", name);
                $fatal(1, "invalid decimal argument");
            end
            /* accept only decimal digits within the unsigned 32-bit range. */
            for (index = 0; index < digits.len(); index = index + 1) begin
                /* reject signs, X/Z, separators, and trailing text. */
                if (digits[index] < 8'd48 || digits[index] > 8'd57) begin
                    $display("FAIL arguments invalid %s digit", name);
                    $fatal(1, "invalid decimal argument");
                end
                accumulator = accumulator * 64'd10 + digits[index] - 8'd48;
                /* fail before narrowing the value. */
                if (accumulator > 64'd4294967295) begin
                    $display("FAIL arguments %s exceeds 32 bits", name);
                    $fatal(1, "decimal argument overflow");
                end
            end
            decimal_u32 = accumulator[31:0];
        end
    endfunction

    /* use a fixed 32-bit LCG, including a reproducible sequence for seed zero. */
    function automatic [31:0] next_random_word;
        /* assignment truncates the recurrence modulo 2^32. */
        begin
            random_state = 32'd1664525 * random_state + 32'd1013904223;
            next_random_word = random_state;
        end
    endfunction

    /* bound the full run; 64 echo trials need at most 88.08 ms. */
    initial begin
        #100000000;
        $display("FAIL watchdog simulation exceeded 100 ms");
        $fatal(1, "simulation watchdog timeout");
    end

    /* run all stimulus sequentially; no interrupt or background clock is used. */
    initial begin
        $dumpfile("waveforms.vcd");
        $dumpvars(0, crypto_tb);
        initialize_interface();

        /* parse an explicit seed and a bounded number of extra echo trials. */
        argument_found = $value$plusargs("seed=%s", argument_text);
        /* preserve the documented seed when no override is present. */
        if (argument_found != 0) begin
            seed = decimal_u32("seed", argument_text);
        end
        argument_found = $value$plusargs("random_trials=%s", argument_text);
        /* validate the full unsigned value before storing it as an integer. */
        if (argument_found != 0) begin
            trial_argument = decimal_u32("random_trials", argument_text);
            /* limit runtime and waveform size. */
            if (trial_argument > 32'd64) begin
                $display("FAIL arguments random_trials must be 0..64");
                $fatal(1, "invalid random trial count");
            end
            random_trials = trial_argument;
        end
        random_state = seed;
        $display("TEST configuration seed=%0d random_trials=%0d", seed, random_trials);

        /* reset, load plaintext, and verify the simultaneous SPI echo. */
        $display("TEST baseline.reset");
        apply_synchronous_reset("baseline.reset");
        $display("TEST baseline.echo");
        transfer_word32("baseline.load_plaintext", PLAINTEXT, ignored);
        read_result("baseline.echo", PLAINTEXT, received);
        check_word32("baseline.echo", received, PLAINTEXT);

        /* replay encryption with one start and seven further pulses. */
        $display("TEST baseline.encrypt");
        set_mode(1'b0);
        pulse_start("encrypt", 1'b1);
        clock_until_complete("encrypt", 7, 1'b1, used_pulses);
        read_result("baseline.encrypt", PLAINTEXT, received);
        check_word32("baseline.encrypt", received, CIPHERTEXT);

        /* select decryption before reset and reload the supplied ciphertext. */
        $display("TEST baseline.decrypt");
        set_mode(1'b1);
        apply_synchronous_reset("baseline.decrypt_reset");
        transfer_word32("baseline.load_ciphertext", CIPHERTEXT, ignored);
        pulse_start("decrypt", 1'b0);
        clock_until_complete("decrypt", 7, 1'b1, used_pulses);
        read_result("baseline.decrypt", CIPHERTEXT, received);
        check_word32("baseline.decrypt", received, PLAINTEXT);

        /* freeze the baseline count before additional SPI-only trials. */
        baseline_pulses = total_pulses;
        /* detect any inserted or missing baseline clock. */
        if (baseline_pulses != 180) begin
            $display("FAIL baseline.pulses actual=%0d expected=180", baseline_pulses);
            $fatal(1, "baseline clock count mismatch");
        end

        /* reset each trial; echo arbitrary data without assuming encryption. */
        for (trial = 0; trial < random_trials; trial = trial + 1) begin
            trial_name = $sformatf("random_echo.%0d", trial);
            random_word = next_random_word();
            $display("TEST %s word=%h", trial_name, random_word);
            set_mode(1'b0);
            apply_synchronous_reset({trial_name, ".reset"});
            transfer_word32({trial_name, ".load"}, random_word, ignored);
            read_result(trial_name, random_word, received);
            check_word32(trial_name, received, random_word);
        end

        /* validate total clocks before emitting the only successful summary. */
        if (total_pulses != 180 + 66 * random_trials) begin
            $display("FAIL total.pulses actual=%0d", total_pulses);
            $fatal(1, "total clock count mismatch");
        end
        $display("SUMMARY PASS checks=%0d baseline_pulses=%0d total_pulses=%0d seed=%0d",
            checks, baseline_pulses, total_pulses, seed);
        $finish;
    end
endmodule
