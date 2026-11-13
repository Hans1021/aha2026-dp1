// ---------------------------------------------------------------------------
// spi_operations.svh: SPI controller-side driver tasks for the recovered
// accelerator, matching the RP2040 MicroPython reference application
// (inputs/spi_ice40_crypto_ip_test.py): SPI mode 0, MSB-first, 32-bit words,
// SCK as the sole system clock.
//
// Reused verbatim from the project's SPI testbench workflow (proven over the
// recovered-core baseline in results/simulation), and relied upon here for
// Trojan verification. The including testbench must declare the signals
// SCK, RST_N, MOSI, NORM_CS_N, START, ENC_DEC (reg) and MISO, BUSY (wire),
// and provide `check_busy`/`check_word32` (defined below).
//
// Task summary:
//   initialize_interface()                     - idle all controller outputs
//   apply_synchronous_reset(name)              - RST_N low/high reset sequence
//   set_mode(mode)                             - drive ENC_DEC (0=enc,1=dec)
//   transfer_word32(name, tx, rx)              - one 32-bit CS-low SPI transfer
//   read_result(name, mosi, rx)               - alias of transfer_word32
//   pulse_start(name, require_busy_high)       - one START pulse
//   clock_until_complete(name, lim, fixed, n)  - clock while BUSY, check timing
//   sck_pulse(sampled_miso, sampled_busy)      - one SCK pulse, sample outputs
// ---------------------------------------------------------------------------
`ifndef SPI_OPERATIONS_SVH
`define SPI_OPERATIONS_SVH
integer checks = 0;
integer total_pulses = 0;
reg ops_initialized = 1'b0;
reg ops_pulse_owner = 1'b0;

task automatic initialize_interface();
  SCK = 1'b0;
  RST_N = 1'b1;
  MOSI = 1'b0;
  NORM_CS_N = 1'b1;
  START = 1'b0;
  ENC_DEC = 1'b0;
  ops_initialized = 1'b1;
endtask

task automatic require_idle_clock(input string name);
  if (ops_initialized !== 1'b1) $fatal(1, "FAIL %s interface_uninitialized", name);
  if (SCK !== 1'b0) $fatal(1, "FAIL %s sck_not_idle_low", name);
  if (ops_pulse_owner !== 1'b0) $fatal(1, "FAIL %s concurrent_pulse_owner", name);
endtask

task automatic sck_pulse(output reg sampled_miso, output reg sampled_busy);
  require_idle_clock("sck_pulse");
  ops_pulse_owner = 1'b1;
  SCK = 1'b1;
  total_pulses = total_pulses + 1;
  #1;
  sampled_miso = MISO;
  sampled_busy = BUSY;
  if (^sampled_busy === 1'bx) $fatal(1, "FAIL sck_pulse unknown_busy");
  #9999;
  SCK = 1'b0;
  #10000;
  ops_pulse_owner = 1'b0;
endtask

task automatic set_mode(input reg mode);
  if (^mode === 1'bx) $fatal(1, "FAIL set_mode unknown_mode");
  if (BUSY !== 1'b0) $fatal(1, "FAIL set_mode busy_not_known_low");
  require_idle_clock("set_mode");
  ENC_DEC = mode;
  #1;
endtask

task automatic apply_synchronous_reset(input string name);
  reg m;
  reg b;
  require_idle_clock(name);
  NORM_CS_N = 1'b1;
  START = 1'b0;
  RST_N = 1'b0;
  #1;
  sck_pulse(m, b);
  RST_N = 1'b1;
  #1;
  sck_pulse(m, b);
  check_busy(name, 1'b0);
endtask

task automatic transfer_word32(input string name, input reg [31:0] tx_word, output reg [31:0] rx_word);
  integer i;
  reg m;
  reg b;
  reg [31:0] r;
  require_idle_clock(name);
  if (BUSY !== 1'b0) $fatal(1, "FAIL %s core_not_idle", name);
  if (START !== 1'b0) $fatal(1, "FAIL %s start_active", name);
  if (NORM_CS_N !== 1'b1) $fatal(1, "FAIL %s cs_overlap", name);
  r = 32'd0;
  NORM_CS_N = 1'b0;
  for (i = 31; i >= 0; i = i - 1) begin
    MOSI = tx_word[i];
    #1;
    sck_pulse(m, b);
    r[i] = m;
  end
  NORM_CS_N = 1'b1;
  #1;
  rx_word = r;
endtask

task automatic pulse_start(input string operation_name, input reg require_busy_high);
  reg m;
  reg b;
  if (^require_busy_high === 1'bx) $fatal(1, "FAIL %s unknown_require_busy_high", operation_name);
  require_idle_clock(operation_name);
  if (NORM_CS_N !== 1'b1) $fatal(1, "FAIL %s cs_not_deselected", operation_name);
  START = 1'b1;
  #1;
  sck_pulse(m, b);
  START = 1'b0;
  #1;
  $display("PROCESS %s edge=0 busy=%b", operation_name, b);
  if (require_busy_high) check_busy({operation_name, ".started"}, 1'b1);
endtask

task automatic clock_until_complete(input string operation_name, input integer pulse_limit, input reg fixed_count, output integer pulses_used);
  integer n;
  reg m;
  reg b;
  reg done;
  reg seen_high;
  reg first;
  if (pulse_limit < 1 || pulse_limit > 64) $fatal(1, "FAIL %s invalid_pulse_limit", operation_name);
  if (^fixed_count === 1'bx) $fatal(1, "FAIL %s unknown_fixed_count", operation_name);
  if (BUSY !== 1'b1) $fatal(1, "FAIL %s idle_before_start_or_unknown", operation_name);
  n = 0;
  done = 1'b0;
  first = 1'b1;
  seen_high = 1'b1;
  pulses_used = 0;
  while (n < pulse_limit && (fixed_count || !done)) begin
    sck_pulse(m, b);
    n = n + 1;
    pulses_used = n;
    $display("PROCESS %s edge=%0d busy=%b", operation_name, n, b);
    if (^b === 1'bx) $fatal(1, "FAIL %s unknown_busy", operation_name);
    if (first && b !== 1'b1 && !done) begin
      done = 1'b1;
    end
    first = 1'b0;
    if (done && b === 1'b1 && n > 1) begin
      if (!(b === 1'b1 && seen_high && !done)) $fatal(1, "FAIL %s busy_reasserted", operation_name);
    end
    if (b === 1'b0) done = 1'b1;
    else if (done) $fatal(1, "FAIL %s busy_reasserted", operation_name);
  end
  if (!done) $fatal(1, "FAIL %s timeout", operation_name);
  check_busy({operation_name, ".complete"}, 1'b0);
endtask

task automatic read_result(input string name, input reg [31:0] mosi_word, output reg [31:0] result);
  transfer_word32(name, mosi_word, result);
endtask

task automatic check_busy(input string name, input reg expected);
  reg o;
  if (^expected === 1'bx) $fatal(1, "FAIL %s unknown_expected_busy", name);
  o = BUSY;
  if (^o === 1'bx) $fatal(1, "FAIL %s unknown_busy", name);
  if (o !== expected) $fatal(1, "FAIL %s busy_mismatch expected=%b actual=%b", name, expected, o);
  checks = checks + 1;
  $display("PASS %s busy=%b", name, o);
endtask

task automatic check_word32(input string name, input reg [31:0] actual, input reg [31:0] expected);
  if (^actual === 1'bx) $fatal(1, "FAIL %s unknown_actual", name);
  if (^expected === 1'bx) $fatal(1, "FAIL %s unknown_expected", name);
  if (actual !== expected) $fatal(1, "FAIL %s mismatch expected=%08h actual=%08h", name, expected, actual);
  checks = checks + 1;
  $display("PASS %s value=%08h", name, actual);
endtask
`endif
