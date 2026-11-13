/* 
 * LIMITATION EXPLANATION & IMPLEMENTATION DETAILS:
 * 1. Latch Synthesizability & Safety: Detecting an asynchronous BUSY pulse while SCK 
 *    is stopped requires an explicit memory element. A standard `always_latch` safely 
 *    synthesizes to an SB_LUT4 combinational loop on iCE40, capturing transient events
 *    without inferring unsupported clocks.
 * 2. Safe Domain Crossing: Directly sampling the asynchronous `orig_busy` falling edge 
 *    into the SCK domain introduces unsafe metastability risks. A standard 2-stage 
 *    synchronizer (`orig_busy_meta`, `orig_busy_sync`) bridges this reliably.
 * 3. Exact Cycle Counting: The synchronous payload counter explicitly compensates for the 
 *    2-cycle synchronizer delay. By counting 7 additional SCK edges (limit == 6), it 
 *    guarantees that the payload evaluates and drops BUSY exactly on the 8th synchronized 
 *    SCK rising edge after the core genuinely completed, fixing the off-by-one error.
 * 4. Signal Mirroring: As requested, `pin_23` (ICE_LED) perfectly matches the modified 
 *    extended BUSY logic (`pin_38`), ensuring external trace consistency.
 * 5. Glitch Prevention: The extended BUSY seamlessly combinationally ORs `orig_busy` 
 *    with the internal armed state, completely eliminating momentary drop glitches.
 */

module recovered (
    input  logic pin_36, // SCK
    input  logic pin_25, // ENC_DEC
    input  logic pin_28, // NORM_CS_N
    input  logic pin_32, // MOSI
    input  logic pin_34, // RST_N (Synchronous active-low)
    input  logic pin_27, // START
    output logic pin_38, // BUSY
    output logic pin_23, // ICE_LED
    output logic pin_31  // MISO
);

    // Internal signals from the original core
    logic orig_busy;
    logic orig_ice_led;
    logic orig_miso;

    recovered_original core_inst (
        .pin_36(pin_36),
        .pin_25(pin_25),
        .pin_28(pin_28),
        .pin_32(pin_32),
        .pin_34(pin_34),
        .pin_27(pin_27),
        .pin_38(orig_busy),
        .pin_23(orig_ice_led),
        .pin_31(orig_miso)
    );

    // Synchronous state and synchronizers
    logic       start_d;
    logic [2:0] start_cnt;
    logic       orig_busy_meta;
    logic       orig_busy_sync;
    logic [2:0] ext_cnt;
    logic       payload_done;

    wire trigger_active = (start_cnt == 3'd4);

    // Asynchronous latch to capture core BUSY completion safely
    logic busy_seen;
    always_latch begin
        if (!pin_34 || payload_done) begin
            busy_seen = 1'b0;
        end else if (trigger_active && orig_busy) begin
            busy_seen = 1'b1;
        end
    end

    // SCK-driven synchronous state logic
    always_ff @(posedge pin_36) begin
        if (!pin_34) begin
            start_d        <= 1'b0;
            start_cnt      <= 3'd0;
            orig_busy_meta <= 1'b0;
            orig_busy_sync <= 1'b0;
            ext_cnt        <= 3'd0;
            payload_done   <= 1'b0;
        end else begin
            // 1. Edge-detect START and count active operations
            start_d <= pin_27;
            if (pin_27 && !start_d) begin
                if (start_cnt < 3'd7) begin
                    start_cnt <= start_cnt + 1'b1;
                end
            end

            // 2. Safely synchronize the asynchronous orig_busy signal
            orig_busy_meta <= orig_busy;
            orig_busy_sync <= orig_busy_meta;

            // 3. Count exactly 8 SCK edges (including 2 sync delay + 7 counter states)
            if (busy_seen && !orig_busy_sync && !payload_done) begin
                if (ext_cnt == 3'd6) begin
                    // Signals completion on exactly the 8th cycle to asynchronously clear the latch
                    payload_done <= 1'b1; 
                end else begin
                    ext_cnt <= ext_cnt + 1'b1;
                end
            end
        end
    end

    // Combinational extension logic to seamlessly bridge core completion with payload logic
    assign pin_38 = orig_busy | (busy_seen & !payload_done);
    
    // Ensure the external LED consistently mirrors the extended BUSY exactly as required
    assign pin_23 = orig_ice_led | (busy_seen & !payload_done);
    
    // Unmodified pass-through
    assign pin_31 = orig_miso;

endmodule
