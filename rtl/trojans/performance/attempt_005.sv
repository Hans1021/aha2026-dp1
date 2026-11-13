/* 
 * LIMITATION EXPLANATION:
 * The requested payload relies on synchronously tracking events using the SCK clock
 * (pin_36) because no unsupported internal clocks are allowed and asynchronous logic
 * is incompatible with exact cycle-counting requirements.
 * 
 * 1. Detecting START: The START signal (pin_27) is asserted when NORM_CS_N (pin_28) 
 *    is 1. In typical SPI systems, the master stops toggling SCK when NORM_CS_N is 1. 
 *    If SCK is gated during the START pulse, sampling START synchronously on a rising 
 *    SCK edge will fail to detect it.
 * 2. Detecting BUSY falling edge: The core completion is flagged by BUSY (orig_busy) 
 *    falling. If the master stops providing SCK while waiting for the core to finish, 
 *    the synchronous edge detector (!orig_busy && orig_busy_d) will either miss the 
 *    transition or suffer arbitrary delays until the master resumes SCK.
 * 3. Extending BUSY by 8 cycles: Counting exactly 8 SCK cycles fundamentally requires
 *    SCK to toggle 8 times after BUSY falls. If SCK is inactive, the BUSY extension 
 *    will pause indefinitely or fail to complete.
 * 
 * In summary, this hardware modification operates under the necessary assumption that 
 * the external SPI master provides a continuous SCK, or at least supplies enough 
 * clock cycles during START assertions and BUSY polling, to satisfy the synchronous 
 * sampling and cycle-counting requirements.
 */

module recovered (
    input  logic pin_36, // SCK
    input  logic pin_25, // ENC_DEC
    input  logic pin_28, // NORM_CS_N
    input  logic pin_32, // MOSI
    input  logic pin_34, // RST_N
    input  logic pin_27, // START
    output logic pin_38, // BUSY
    output logic pin_23, // ICE_LED
    output logic pin_31  // MISO
);

    // Internal signals to monitor original module's outputs
    logic orig_busy;
    logic orig_ice_led;
    logic orig_miso;

    // Instantiate the original, unmodified core
    recovered_original core_inst (
        .pin_36(pin_36),
        .pin_25(pin_25),
        .pin_28(pin_28),
        .pin_32(pin_32),
        .pin_34(pin_34),
        .pin_27(pin_27),
        .pin_38(orig_busy),
        .pin_23(orig_ice_led),
        .pin_31(orig_miso)
    );

    // State for the hardware modification trigger and payload
    logic       start_d;
    logic [2:0] start_cnt;
    logic       orig_busy_d;
    
    logic [2:0] extend_cnt;
    logic       extend_active;
    logic       payload_done;

    always_ff @(posedge pin_36) begin
        if (!pin_34) begin // Synchronous active-low reset
            start_d       <= 1'b0;
            start_cnt     <= 3'd0;
            orig_busy_d   <= 1'b0;
            extend_cnt    <= 3'd0;
            extend_active <= 1'b0;
            payload_done  <= 1'b0;
        end else begin
            start_d     <= pin_27;
            orig_busy_d <= orig_busy;

            // Trigger: Count START assertions sampled on rising SCK edge
            if (pin_27 && !start_d) begin
                if (start_cnt < 3'd7) begin
                    start_cnt <= start_cnt + 1'b1;
                end
            end

            // Payload: Extend externally visible BUSY by 8 cycles upon the 4th completion
            if (start_cnt == 3'd4 && !payload_done && !extend_active && !orig_busy && orig_busy_d) begin
                // On the falling edge of orig_busy during the 4th operation, initiate the 8-cycle extension.
                // 1 cycle is seamlessly covered by the combinational extend_trigger.
                // We count 7 additional cycles sequentially to total exactly 8 SCK cycles.
                extend_active <= 1'b1;
                extend_cnt    <= 3'd7;
            end else if (extend_active) begin
                if (extend_cnt == 3'd1) begin
                    extend_active <= 1'b0;
                    payload_done  <= 1'b1; // Mark payload as done so later operations are unaffected
                end else begin
                    extend_cnt <= extend_cnt - 1'b1;
                end
            end
        end
    end

    // Combinational logic to seamlessly extend BUSY without a 1-cycle dip
    wire extend_trigger = (start_cnt == 3'd4) && !payload_done && !extend_active && !orig_busy && orig_busy_d;
    wire extension = extend_active | extend_trigger;

    // Output routing
    assign pin_38 = orig_busy | extension; // Extended BUSY
    assign pin_23 = orig_ice_led;          // Untriggered outputs pass-through transparently
    assign pin_31 = orig_miso;             // Untriggered outputs pass-through transparently

endmodule
