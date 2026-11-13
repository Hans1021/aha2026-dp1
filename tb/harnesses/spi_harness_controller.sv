module spi_harness_controller (
    input wire [3:0] INDEX, input wire [31:0] RNG_STATE,
    input wire ENC_DEC, input wire [3:0] CUTOFF,
    output reg FRAME_VALID, output reg [31:0] TX_WORD, RESPONSE_WORD, RNG_NEXT,
    output reg TIMING_VALID, output reg [31:0] INPUT_WORD, RESULT_WORD, READ_WORD,
    output reg BUSY_EXPECTED, RESULT_READY
);
always_comb begin
    RNG_NEXT=32'd1664525*RNG_STATE+32'd1013904223;
    FRAME_VALID=(INDEX<14); TX_WORD=0; RESPONSE_WORD=0;
    if (FRAME_VALID) begin
        case (INDEX)
            0: TX_WORD=32'h01234567;
            1: TX_WORD=32'h00000000;
            2: TX_WORD=32'hffffffff;
            3: TX_WORD=32'h80000000;
            4: TX_WORD=32'h00000001;
            5: TX_WORD=32'h96a50fc3;
            default: TX_WORD=RNG_NEXT;
        endcase
        RESPONSE_WORD=32'hd2963a5c^(32'h01020408*INDEX);
    end
    INPUT_WORD=ENC_DEC ? 32'h9cd84392 : 32'h59c359c3;
    RESULT_WORD=ENC_DEC ? 32'h59c359c3 : 32'h9cd84392;
    TIMING_VALID=(CUTOFF<8); READ_WORD=0; BUSY_EXPECTED=0; RESULT_READY=0;
    if (TIMING_VALID) begin
        READ_WORD=(CUTOFF<4) ? INPUT_WORD : RESULT_WORD;
        BUSY_EXPECTED=(CUTOFF<3); RESULT_READY=(CUTOFF>=4);
    end
end
endmodule
