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
                // On falling edge of orig_busy during the 4th operation, start 8-cycle extension
                // (1 cycle is covered by the combinational trigger, 7 cycles by the counter)
                extend_active <= 1'b1;
                extend_cnt    <= 3'd6;
            end else if (extend_active) begin
                if (extend_cnt == 3'd0) begin
                    extend_active <= 1'b0;
                    payload_done  <= 1'b1; // Mark payload as completed to avoid re-triggering
                end else begin
                    extend_cnt <= extend_cnt - 1'b1;
                end
            end
        end
    end

    // Combinational logic to seamlessly extend BUSY and ICE_LED without a 1-cycle gap
    wire extend_trigger = (start_cnt == 3'd4) && !payload_done && !extend_active && !orig_busy && orig_busy_d;
    wire extension = extend_active | extend_trigger;

    // Extend BUSY and ICE_LED seamlessly
    assign pin_38 = orig_busy | extension;
    assign pin_23 = orig_ice_led | extension;

    // Pass-through untriggered outputs
    assign pin_31 = orig_miso;

endmodule
