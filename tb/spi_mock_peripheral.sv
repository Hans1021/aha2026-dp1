`timescale 1ns/1ps

/* test-only serial peripheral; no cryptographic behavior is modeled. */
module spi_mock_peripheral (
    input wire SCK, RST_N, MOSI, NORM_CS_N, START, ENC_DEC,
    input wire [31:0] RESPONSE_WORD,
    output reg MISO,
    output reg BUSY,
    output wire ICE_LED,
    output reg [31:0] RECEIVED_WORD,
    output reg [31:0] FRAME_COUNT,
    output reg [31:0] FRAME_BITS
);
    /* retain simulation-only framing state and an arbitrary three-clock delay. */
    reg [31:0] response_shift;
    reg frame_open = 1'b0;
    reg reset_seen = 1'b0;
    integer remaining;
    assign ICE_LED = BUSY;

    /* latch an independent response before the first selected rising edge. */
    always @(negedge NORM_CS_N) begin
        /* require reset and idle controls before opening a frame. */
        if (reset_seen === 1'b1) begin
            /* reject control changes on the active sampling phase. */
            if (SCK !== 1'b0 || RST_N !== 1'b1 || START !== 1'b0 || BUSY !== 1'b0) begin
                $display("DRIVER FAIL mock.frame_start invalid controls");
                $fatal(1, "mock frame start");
            end
            frame_open = 1'b1;
            FRAME_BITS = 0;
            RECEIVED_WORD = 0;
            response_shift = RESPONSE_WORD;
        end
    end

    /* require one uninterrupted 32-clock selection per word. */
    always @(posedge NORM_CS_N) begin
        /* ignore time-zero initialization and reset without an open frame. */
        if (reset_seen === 1'b1 && frame_open === 1'b1) begin
            /* reject short frames and deassertion during the sampling phase. */
            if (SCK !== 1'b0 || FRAME_BITS !== 32'd32) begin
                $display("DRIVER FAIL mock.frame_end bits=%0d expected=32", FRAME_BITS);
                $fatal(1, "mock frame length");
            end
            frame_open = 1'b0;
            FRAME_COUNT = FRAME_COUNT + 1;
        end
    end

    /* sample inputs on rising SCK and publish registered outputs through NBA. */
    always @(posedge SCK) begin
        /* synchronous reset requires this edge and clears mock telemetry. */
        if (RST_N === 1'b0) begin
            MISO <= 1'b0;
            BUSY <= 1'b0;
            RECEIVED_WORD <= 0;
            FRAME_COUNT <= 0;
            FRAME_BITS <= 0;
            response_shift <= 0;
            remaining <= 0;
            frame_open <= 1'b0;
            reset_seen <= 1'b1;
        end else begin
            /* fail unknown controls rather than silently ignoring an edge. */
            if (^{RST_N, MOSI, NORM_CS_N, START, ENC_DEC} === 1'bx) begin
                $display("DRIVER FAIL mock.controls unknown input");
                $fatal(1, "unknown mock controls");
            end
            /* shift only during an open selection, always MSB first. */
            if (NORM_CS_N === 1'b0) begin
                /* detect an overlong frame on its first extra edge. */
                if (frame_open !== 1'b1 || FRAME_BITS >= 32) begin
                    $display("DRIVER FAIL mock.frame_length bits=%0d", FRAME_BITS);
                    $fatal(1, "mock frame overflow");
                end
                MISO <= response_shift[31];
                response_shift <= {response_shift[30:0], 1'b0};
                RECEIVED_WORD <= {RECEIVED_WORD[30:0], MOSI};
                FRAME_BITS <= FRAME_BITS + 1;
            end
            /* model completion timing only; START never computes a cipher. */
            if (START === 1'b1) begin
                /* keep START outside selected transfers and ongoing processing. */
                if (NORM_CS_N !== 1'b1 || BUSY !== 1'b0) begin
                    $display("DRIVER FAIL mock.start invalid controls");
                    $fatal(1, "mock START overlap");
                end
                BUSY <= 1'b1;
                remaining <= 3;
            end else if (BUSY === 1'b1) begin
                remaining <= remaining - 1;
                /* complete after exactly three additional mock clocks. */
                if (remaining == 1) begin
                    BUSY <= 1'b0;
                end
            end
        end
    end
endmodule
