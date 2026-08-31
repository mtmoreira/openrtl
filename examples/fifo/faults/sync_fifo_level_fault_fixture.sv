// Intentional regression fixture: an accepted write leaves count unchanged.
module sync_fifo #(
  parameter int unsigned WIDTH = 8,
  parameter int unsigned DEPTH = 4,
  localparam int unsigned PTR_W = (DEPTH <= 2) ? 1 : $clog2(DEPTH),
  localparam int unsigned LEVEL_W = $clog2(DEPTH + 1)
) (
  input  logic                 clk,
  input  logic                 rst_n,
  input  logic                 wr_valid,
  output logic                 wr_ready,
  input  logic [WIDTH-1:0]     wr_data,
  output logic                 rd_valid,
  input  logic                 rd_ready,
  output logic [WIDTH-1:0]     rd_data,
  output logic                 full,
  output logic                 empty,
  output logic [LEVEL_W-1:0]   level
);

  logic [WIDTH-1:0] memory [0:DEPTH-1];
  logic [PTR_W-1:0] write_pointer;
  logic [PTR_W-1:0] read_pointer;
  logic [LEVEL_W-1:0] count;
  logic write_accepted;
  logic read_accepted;

  localparam logic [PTR_W-1:0] LAST_POINTER = PTR_W'(DEPTH - 1);
  localparam logic [LEVEL_W-1:0] MAX_LEVEL = LEVEL_W'(DEPTH);
  localparam bit HAS_UNUSED_POINTER_STATES = (DEPTH & (DEPTH - 1)) != 0;

  initial begin
    assert (WIDTH >= 1) else $fatal(1, "WIDTH must be positive");
    assert (DEPTH >= 2) else $fatal(1, "DEPTH must be at least two");
  end

  assign empty = (count == 0);
  assign full = (count == MAX_LEVEL);
  assign level = count;
  assign rd_valid = !empty;
  assign rd_data = rd_valid ? memory[read_pointer] : '0;
  assign read_accepted = rd_valid && rd_ready;
  assign wr_ready = !full || read_accepted;
  assign write_accepted = wr_valid && wr_ready;

  generate
    if (HAS_UNUSED_POINTER_STATES) begin : non_power_of_two_depth
      always_ff @(posedge clk) begin
        if (rst_n) begin
          assert (write_pointer <= LAST_POINTER) else $fatal(1, "write pointer out of range");
          assert (read_pointer <= LAST_POINTER) else $fatal(1, "read pointer out of range");
        end
      end
    end
  endgenerate

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      write_pointer <= '0;
      read_pointer <= '0;
      count <= '0;
    end else begin
      assert (count <= MAX_LEVEL) else $fatal(1, "FIFO count exceeded DEPTH");

      if (write_accepted) begin
        memory[write_pointer] <= wr_data;
        write_pointer <= (write_pointer == LAST_POINTER) ? '0 : write_pointer + 1'b1;
      end
      if (read_accepted) begin
        read_pointer <= (read_pointer == LAST_POINTER) ? '0 : read_pointer + 1'b1;
      end
      unique case ({write_accepted, read_accepted})
        2'b10: count <= count;
        2'b01: count <= count - 1'b1;
        default: count <= count;
      endcase
    end
  end

endmodule
