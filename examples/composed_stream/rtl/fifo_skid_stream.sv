module fifo_skid_stream (
  input  logic       clk,
  input  logic       rst_n,
  input  logic       s_valid,
  output logic       s_ready,
  input  logic [7:0] s_data,
  output logic       m_valid,
  input  logic       m_ready,
  output logic [7:0] m_data,
  output logic [2:0] fifo_level,
  output logic       skid_occupied
);
  logic link_valid;
  logic link_ready;
  logic [7:0] link_data;
  logic fifo_full;
  logic fifo_empty;

  sync_fifo #(.WIDTH(8), .DEPTH(4)) fifo (
    .clk(clk), .rst_n(rst_n),
    .wr_valid(s_valid), .wr_ready(s_ready), .wr_data(s_data),
    .rd_valid(link_valid), .rd_ready(link_ready), .rd_data(link_data),
    .full(fifo_full), .empty(fifo_empty), .level(fifo_level)
  );
  skid_buffer #(.WIDTH(8)) skid (
    .clk(clk), .rst_n(rst_n),
    .s_valid(link_valid), .s_ready(link_ready), .s_data(link_data),
    .m_valid(m_valid), .m_ready(m_ready), .m_data(m_data),
    .occupied(skid_occupied)
  );
endmodule
