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

    // Pass-through untriggered outputs seamlessly
    assign pin_23 = orig_ice_led;
    assign pin_31 = orig_miso;

    // State for the hardware modification trigger and payload
    logic       start_d;
    logic [2:0] start_cnt;
    logic [7:0] busy_sr;

    always_ff @(posedge pin_36) begin
        if (!pin_34) begin // Synchronous active-low reset
            start_d   <= 1'b0;
            start_cnt <= 3'd0;
            busy_sr   <= 8'd0;
        end else begin
            start_d <= pin_27;

            // Trigger: Count START assertions sampled on rising SCK edge
            if (pin_27 && !start_d) begin
                if (start_cnt < 3'd7) begin
                    start_cnt <= start_cnt + 1'b1;
                end
            end

            // Payload: Extend externally visible BUSY by 8 cycles upon the 4th completion
            // A shift register seamlessly delays the falling edge of orig_busy by exactly 8 cycles
            if (start_cnt == 3'd4) begin
                busy_sr <= {busy_sr[6:0], orig_busy};
            end else begin
                busy_sr <= 8'd0;
            end
        end
    end

    // Extend BUSY seamlessly: remains high as long as orig_busy is high OR 
    // there are residual '1's still passing through the 8-stage shift register
    assign pin_38 = orig_busy | (|busy_sr);

endmodule
