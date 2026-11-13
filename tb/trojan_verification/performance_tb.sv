module performance_tb;

    // Declarations
    reg SCK, RST_N, MOSI, NORM_CS_N, START, ENC_DEC;
    wire MISO, BUSY, ICE_LED;

    // DUT Instantiation
    crypto_dut dut (
        .SCK(SCK),
        .RST_N(RST_N),
        .MOSI(MOSI),
        .MISO(MISO),
        .NORM_CS_N(NORM_CS_N),
        .START(START),
        .ENC_DEC(ENC_DEC),
        .BUSY(BUSY),
        .ICE_LED(ICE_LED)
    );

    // Harness Include
    `include "spi_operations.svh"

    // Local Variables
    integer used;
    reg [31:0] rx_data;
    reg [31:0] result;
    reg smiso, sbusy;
    integer count;
    integer pulses_after_start;

    initial begin
        fork
            // Watchdog Thread
            begin
                #100ms;
                $fatal(1, "Watchdog timeout - simulation hung");
            end

            // Main Test Thread
            begin
                initialize_interface();

                // -----------------------------------------------------------------
                // Prove: (a) Normal encrypt/decrypt golden vectors
                // -----------------------------------------------------------------
                
                // Normal Encryption (ENC_DEC = 0)
                apply_synchronous_reset("rst_a_enc");
                set_mode(0); 
                transfer_word32("load_a_enc", 32'h01234567, rx_data);
                pulse_start("start_a_enc", 1'b1);
                clock_until_complete("clk_a_enc", 7, 1'b1, used);
                read_result("read_a_enc", 32'h00000000, result);
                check_word32("chk_a_enc", result, 32'h70aa949e);

                // Normal Decryption (ENC_DEC = 1)
                apply_synchronous_reset("rst_a_dec");
                set_mode(1); 
                transfer_word32("load_a_dec", 32'h89ABCDEF, rx_data);
                pulse_start("start_a_dec", 1'b1);
                clock_until_complete("clk_a_dec", 7, 1'b1, used);
                read_result("read_a_dec", 32'h00000000, result);
                check_word32("chk_a_dec", result, 32'h2d00588b);

                // -----------------------------------------------------------------
                // Prove: (b) Performance-degradation Trojan on 4th START
                // -----------------------------------------------------------------
                
                apply_synchronous_reset("rst_b");
                
                for (int i = 1; i <= 6; i++) begin
                    set_mode(0);
                    transfer_word32("load_b", 32'hAABBCCDD, rx_data);
                    
                    // pulse_start consumes 1 cycle, where BUSY goes high
                    pulse_start("start_b", 1'b1);

                    // Initialize count to 1 because pulse_start already consumed 1 BUSY=1 cycle
                    count = 1;
                    pulses_after_start = 0;
                    
                    sck_pulse(smiso, sbusy);
                    pulses_after_start++;
                    
                    // Count how many additional cycles BUSY remains high
                    while (sbusy === 1'b1) begin
                        count++;
                        sck_pulse(smiso, sbusy);
                        pulses_after_start++;
                    end

                    // Evaluate Trojan payload trigger
                    if (i == 4) begin
                        if (count != 11) begin
                            $fatal(1, "Trojan failed on op 4. Expected count=11 (3+8 extra), got %0d", count);
                        end
                    end else begin
                        if (count != 3) begin
                            $fatal(1, "Normal op %0d failed. Expected count=3, got %0d", i, count);
                        end
                    end
                    
                    // Verify BUSY is actually low via check_busy assertion right after the loop
                    check_busy("check_busy_low", 1'b0);

                    // Pad remaining cycles to reach 7 total after start, matching standard operation
                    // so the state machine perfectly aligns for the read_result phase
                    while (pulses_after_start < 7) begin
                        sck_pulse(smiso, sbusy);
                        pulses_after_start++;
                    end

                    // Clear state for next loop iteration
                    read_result("read_b", 32'h00000000, result);
                end

                // Conclusion
                $display("PERFORMANCE SUMMARY PASS");
                $finish;
            end
        join_any
        
        $finish;
    end

endmodule
