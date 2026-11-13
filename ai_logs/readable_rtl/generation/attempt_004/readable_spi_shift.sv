// readable_spi_shift
// 32-bit serial shift register with per-stage parallel load and a
// separately registered MISO output. Reconstructed from recovered netlist.
//
// Stage index (MOSI -> MISO) / original register name / parallel-load net:
//   sr[ 0] n628  <- !n630     sr[16] n898  <- !n1026
//   sr[ 1] n390  <- !n624     sr[17] n1015 <- !n1027
//   sr[ 2] n619  <- !n615     sr[18] n1024 <- !n1033
//   sr[ 3] n384  <- !n618     sr[19] n998  <- !n1032
//   sr[ 4] n610  <- !n599     sr[20] n1034 <- !n1035
//   sr[ 5] n607  <- !n608     sr[21] n907  <- !n905
//   sr[ 6] n609  <- !n611     sr[22] n1039 <- !n1040
//   sr[ 7] n601  <- !n602     sr[23] n1038 <- !n1041
//   sr[ 8] n587  <- !n785     sr[24] n910  <- !n911
//   sr[ 9] n578  <- !n762     sr[25] n921  <- !n923
//   sr[10] n579  <- !n763     sr[26] n915  <- !n914
//   sr[11] n577  <- !n755     sr[27] n810  <- !n812
//   sr[12] n698  <- !n775     sr[28] n814  <- !n813
//   sr[13] n781  <- !n778     sr[29] n798  <- !n802
//   sr[14] n776  <- !n779     sr[30] n917  <- !n916
//   sr[15] n897  <- !n896     sr[31] n800  <- !n807
//
// Per stage: next = cs_n ? !load_net : previous_stage (stage 0 takes mosi).
// All 32 stages: update only when enable; reset (synchronous) forces 0.
// miso: registered copy of sr[31] every clock, no enable, no reset.

module readable_spi_shift (
    input  logic clk,      // pin_36
    input  logic cs_n,     // pin_28
    input  logic mosi,     // pin_32
    output logic miso,     // pin_31
    input  logic enable,   // n1
    input  logic reset,    // n2

    input  logic n1026, n1027, n1032, n1033, n1035, n1040, n1041,
    input  logic n599,  n602,  n608,  n611,  n615,  n618,  n624,  n630,
    input  logic n755,  n762,  n763,  n775,  n778,  n779,  n785,
    input  logic n802,  n807,  n812,  n813,
    input  logic n896,  n905,  n911,  n914,  n916,  n923
);

    // ------------------------------------------------------------------
    // Shift register state (all initialized to 0)
    // ------------------------------------------------------------------
    logic [31:0] sr       = 32'h0000_0000;
    logic        miso_q   = 1'b0;

    // ------------------------------------------------------------------
    // Per-stage parallel-load values (polarity explicit per stage;
    // every recovered stage loads the inverted external net)
    // ------------------------------------------------------------------
    logic [31:0] load_val;

    assign load_val[ 0] = ~n630;
    assign load_val[ 1] = ~n624;
    assign load_val[ 2] = ~n615;
    assign load_val[ 3] = ~n618;
    assign load_val[ 4] = ~n599;
    assign load_val[ 5] = ~n608;
    assign load_val[ 6] = ~n611;
    assign load_val[ 7] = ~n602;
    assign load_val[ 8] = ~n785;
    assign load_val[ 9] = ~n762;
    assign load_val[10] = ~n763;
    assign load_val[11] = ~n755;
    assign load_val[12] = ~n775;
    assign load_val[13] = ~n778;
    assign load_val[14] = ~n779;
    assign load_val[15] = ~n896;
    assign load_val[16] = ~n1026;
    assign load_val[17] = ~n1027;
    assign load_val[18] = ~n1033;
    assign load_val[19] = ~n1032;
    assign load_val[20] = ~n1035;
    assign load_val[21] = ~n905;
    assign load_val[22] = ~n1040;
    assign load_val[23] = ~n1041;
    assign load_val[24] = ~n911;
    assign load_val[25] = ~n923;
    assign load_val[26] = ~n914;
    assign load_val[27] = ~n812;
    assign load_val[28] = ~n813;
    assign load_val[29] = ~n802;
    assign load_val[30] = ~n916;
    assign load_val[31] = ~n807;

    // ------------------------------------------------------------------
    // Next-state: parallel load while cs_n high, shift toward MISO when low
    // ------------------------------------------------------------------
    logic [31:0] sr_next;
    assign sr_next = cs_n ? load_val : {sr[30:0], mosi};

    // ------------------------------------------------------------------
    // 32 shift stages: clock enable + synchronous reset
    // ------------------------------------------------------------------
    always_ff @(posedge clk) begin
        if (enable) begin
            sr <= reset ? 32'h0000_0000 : sr_next;
        end
    end

    // ------------------------------------------------------------------
    // Separately registered MISO output: unconditional, no reset
    // ------------------------------------------------------------------
    always_ff @(posedge clk) begin
        miso_q <= sr[31];
    end

    assign miso = miso_q;

endmodule
