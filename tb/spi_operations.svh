`ifndef SPI_OPERATIONS_SVH
`define SPI_OPERATIONS_SVH

/* include inside a testbench with the named interface signals. */
localparam time HALF_PERIOD_NS = 10000;
localparam time SAMPLE_DELAY_NS = 1;
integer checks = 0;
integer total_pulses = 0;
reg interface_initialized = 1'b0;
reg pulse_active = 1'b0;

/* reject unnamed operations so failures identify their caller. */
task automatic require_name(input string name);
    /* validate the operation label. */
    begin
        /* require a useful test name. */
        if (name.len() == 0) begin
            $display("FAIL arguments empty operation name");
            $fatal(1, "empty operation name");
        end
    end
endtask

/* require initialized, low SCK before changing controls. */
task automatic require_idle_clock(input string name);
    /* reject concurrent clock use and unknown clock state. */
    begin
        require_name(name);
        /* prevent input changes during an active pulse. */
        if (interface_initialized !== 1'b1 || SCK !== 1'b0 ||
                pulse_active !== 1'b0) begin
            $display("FAIL %s interface or clock is not idle", name);
            $fatal(1, "invalid clock ownership");
        end
    end
endtask

/* reject X/Z status before using it for control or checking. */
task automatic require_known_busy(input string name, input reg value);
    /* preserve four-state status checks. */
    begin
        require_name(name);
        /* unknown status must never count as completion. */
        if (value !== 1'b0 && value !== 1'b1) begin
            $display("FAIL %s BUSY is unknown", name);
            $fatal(1, "unknown BUSY");
        end
    end
endtask

/* check status and count exactly one named success. */
task automatic check_busy(input string name, input reg expected);
    /* validate the expectation and observed status. */
    begin
        require_name(name);
        /* reject unknown expected status. */
        if (expected !== 1'b0 && expected !== 1'b1) begin
            $display("FAIL %s expected BUSY is unknown", name);
            $fatal(1, "invalid BUSY expectation");
        end
        require_known_busy(name, BUSY);
        /* fail with a nonzero simulator exit on mismatch. */
        if (BUSY !== expected) begin
            $display("FAIL %s expected_busy=%b actual_busy=%b", name, expected, BUSY);
            $fatal(1, "BUSY mismatch");
        end
        checks = checks + 1;
        $display("PASS %s busy=%b", name, BUSY);
    end
endtask

/* check complete words without allowing matching X/Z bits to pass. */
task automatic check_word32(
    input string name,
    input reg [31:0] actual,
    input reg [31:0] expected
);
    /* validate both words before comparing them. */
    begin
        require_name(name);
        /* reduction XOR detects X/Z in either word. */
        if ((^actual === 1'bx) || (^expected === 1'bx)) begin
            $display("FAIL %s unknown word actual=%h expected=%h", name, actual, expected);
            $fatal(1, "unknown comparison data");
        end
        /* reject unequal known values. */
        if (actual !== expected) begin
            $display("FAIL %s actual=%h expected=%h", name, actual, expected);
            $fatal(1, "word mismatch");
        end
        checks = checks + 1;
        $display("PASS %s value=%h", name, actual);
    end
endtask

/* own every SCK transition after the one-time initialization. */
task automatic sck_pulse(output reg sampled_miso, output reg sampled_busy);
    /* allow setup before rising, then sample registered MISO after NBA settling. */
    begin
        require_idle_clock("sck_pulse");
        /* reject unknown driven control values before a sampling edge. */
        if (^{RST_N, MOSI, NORM_CS_N, START, ENC_DEC} === 1'bx) begin
            $display("FAIL sck_pulse unknown driven control");
            $fatal(1, "unknown interface input");
        end
        pulse_active = 1'b1;
        #(HALF_PERIOD_NS);
        SCK = 1'b1;
        total_pulses = total_pulses + 1;
        #(SAMPLE_DELAY_NS);
        sampled_miso = MISO;
        sampled_busy = BUSY;
        require_known_busy("sck_pulse", sampled_busy);
        #(HALF_PERIOD_NS - SAMPLE_DELAY_NS);
        SCK = 1'b0;
        pulse_active = 1'b0;
    end
endtask

/* initialize once at time zero; MOSI and mode defaults are testbench choices. */
task automatic initialize_interface;
    /* establish deterministic idle signals without emitting a pulse. */
    begin
        /* reject reinitialization instead of resetting clock ownership. */
        if (interface_initialized !== 1'b0 || total_pulses != 0) begin
            $display("FAIL initialize_interface already initialized");
            $fatal(1, "repeated interface initialization");
        end
        SCK = 1'b0;  // time-zero initialization only
        RST_N = 1'b1;
        MOSI = 1'b0;
        NORM_CS_N = 1'b1;
        START = 1'b0;
        ENC_DEC = 1'b0;
        interface_initialized = 1'b1;
    end
endtask

/* change mode only while idle, keeping setup ahead of the next rising edge. */
task automatic set_mode(input reg mode);
    /* reject unknown mode and active transactions. */
    begin
        require_idle_clock("set_mode");
        require_known_busy("set_mode", BUSY);
        /* validate the mode argument and sideband state. */
        if ((mode !== 1'b0 && mode !== 1'b1) ||
                NORM_CS_N !== 1'b1 || START !== 1'b0 || BUSY !== 1'b0) begin
            $display("FAIL set_mode invalid mode or active transaction");
            $fatal(1, "invalid mode change");
        end
        ENC_DEC = mode;  // 0: encrypt; 1: decrypt
    end
endtask

/* clock reset while asserted, then clock once after release. */
task automatic apply_synchronous_reset(input string name);
    reg unused_miso;
    reg sampled_busy;
    /* preserve mode and MOSI while resetting with CS inactive. */
    begin
        require_idle_clock(name);
        NORM_CS_N = 1'b1;
        START = 1'b0;
        RST_N = 1'b0;
        sck_pulse(unused_miso, sampled_busy);
        RST_N = 1'b1;
        sck_pulse(unused_miso, sampled_busy);
        check_busy(name, 1'b0);
    end
endtask

/* transfer all 32 MSB-first bits while capturing simultaneous MISO. */
task automatic transfer_word32(
    input string name,
    input reg [31:0] tx_word,
    output reg [31:0] rx_word
);
    integer bit_index;
    reg sampled_miso;
    reg sampled_busy;
    /* hold CS low across the full word and retain the final MOSI bit. */
    begin
        require_idle_clock(name);
        require_known_busy(name, BUSY);
        /* reject unknown transmit data and overlapping operations. */
        if ((^tx_word === 1'bx) || RST_N !== 1'b1 ||
                NORM_CS_N !== 1'b1 || START !== 1'b0 || BUSY !== 1'b0) begin
            $display("FAIL %s invalid transfer arguments or active core", name);
            $fatal(1, "invalid transfer");
        end
        rx_word = 32'b0;
        NORM_CS_N = 1'b0;
        /* each bit has a low-phase setup and one post-NBA sample. */
        for (bit_index = 31; bit_index >= 0; bit_index = bit_index - 1) begin
            MOSI = tx_word[bit_index];
            sck_pulse(sampled_miso, sampled_busy);
            /* reject unknown data instead of shifting it into a passing word. */
            if (sampled_miso !== 1'b0 && sampled_miso !== 1'b1) begin
                $display("FAIL %s unknown MISO at bit=%0d", name, bit_index);
                $fatal(1, "unknown MISO");
            end
            rx_word[bit_index] = sampled_miso;
        end
        NORM_CS_N = 1'b1;
        $display("TRANSFER %s mosi=%h miso=%h", name, tx_word, rx_word);
    end
endtask

/* pulse START for one full SCK cycle and report operation edge zero. */
task automatic pulse_start(input string operation_name, input reg require_busy_high);
    reg unused_miso;
    reg sampled_busy;
    /* keep CS high and release START only after SCK returns low. */
    begin
        require_idle_clock(operation_name);
        require_known_busy(operation_name, BUSY);
        /* reject unknown policy and an overlapping operation. */
        if ((require_busy_high !== 1'b0 && require_busy_high !== 1'b1) ||
                RST_N !== 1'b1 || NORM_CS_N !== 1'b1 ||
                START !== 1'b0 || BUSY !== 1'b0) begin
            $display("FAIL %s invalid START arguments or active core", operation_name);
            $fatal(1, "invalid START pulse");
        end
        START = 1'b1;
        sck_pulse(unused_miso, sampled_busy);
        START = 1'b0;
        $display("PROCESS %s edge=0 busy=%b", operation_name, sampled_busy);
        /* the supplied application checks this only for encryption. */
        if (require_busy_high === 1'b1) begin
            check_busy({operation_name, ".started"}, 1'b1);
        end
    end
endtask

/* clock processing with either a fixed count or bounded completion polling. */
task automatic clock_until_complete(
    input string operation_name,
    input integer pulse_limit,
    input reg fixed_count,
    output integer pulses_used
);
    reg unused_miso;
    reg sampled_busy;
    /* preserve the replay count even when BUSY falls before its final pulse. */
    begin
        require_idle_clock(operation_name);
        /* reject unknown counts, unbounded limits, and active transfers. */
        if ((^pulse_limit === 1'bx) || pulse_limit < 1 || pulse_limit > 64 ||
                (fixed_count !== 1'b0 && fixed_count !== 1'b1) ||
                RST_N !== 1'b1 || NORM_CS_N !== 1'b1 || START !== 1'b0) begin
            $display("FAIL %s invalid completion arguments", operation_name);
            $fatal(1, "invalid completion request");
        end
        sampled_busy = BUSY;
        require_known_busy(operation_name, sampled_busy);
        pulses_used = 0;
        /* the pulse counter bounds both fixed and completion-driven modes. */
        while (pulses_used < pulse_limit &&
                (fixed_count === 1'b1 || sampled_busy === 1'b1)) begin
            sck_pulse(unused_miso, sampled_busy);
            pulses_used = pulses_used + 1;
            $display("PROCESS %s edge=%0d busy=%b", operation_name, pulses_used, sampled_busy);
        end
        /* report timeout instead of waiting without SCK edges. */
        if (sampled_busy !== 1'b0) begin
            $display("FAIL %s timeout after %0d processing pulses", operation_name, pulse_limit);
            $fatal(1, "completion timeout");
        end
        check_busy({operation_name, ".complete"}, 1'b0);
    end
endtask

/* read output while explicitly shifting the caller's word into MOSI. */
task automatic read_result(
    input string name,
    input reg [31:0] mosi_word,
    output reg [31:0] result
);
    /* reuse the same full-duplex transfer and clock owner. */
    begin
        transfer_word32(name, mosi_word, result);
    end
endtask

`endif
