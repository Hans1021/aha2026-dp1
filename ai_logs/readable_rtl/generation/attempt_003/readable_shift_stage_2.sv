module readable_shift_stage_2 (
    input  logic clk,
    input  logic cs_n,
    input  logic load_data,
    input  logic shift_in,
    input  logic enable,
    input  logic reset,
    output logic shift_out = 1'b0
);

    // Combinational next-state select (originally LUT n2207):
    // cs_n high -> inverted load_data; cs_n low -> shift_in
    logic next_bit;
    assign next_bit = cs_n ? ~load_data : shift_in;

    // Register holds unless enable is asserted; while enabled,
    // reset synchronously forces zero, otherwise loads next_bit.
    always_ff @(posedge clk) begin
        if (enable)
            shift_out <= reset ? 1'b0 : next_bit;
    end

endmodule
