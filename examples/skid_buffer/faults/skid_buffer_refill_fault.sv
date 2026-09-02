module skid_buffer #(
  parameter int unsigned WIDTH = 8
) (
  input  logic             clk,
  input  logic             rst_n,
  input  logic             s_valid,
  output logic             s_ready,
  input  logic [WIDTH-1:0] s_data,
  output logic             m_valid,
  input  logic             m_ready,
  output logic [WIDTH-1:0] m_data,
  output logic             occupied
);

  logic full;
  logic [WIDTH-1:0] data_q;
  logic input_accepted;
  logic output_accepted;

  // Intentional regression: a full buffer cannot accept a same-edge refill.
  assign s_ready = !full;
  assign m_valid = full || s_valid;
  assign m_data = full ? data_q : s_data;
  assign occupied = full;
  assign input_accepted = s_valid && s_ready;
  assign output_accepted = m_valid && m_ready;

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      full <= 1'b0;
      data_q <= '0;
    end else begin
      unique case ({input_accepted, output_accepted})
        2'b10: begin
          full <= 1'b1;
          data_q <= s_data;
        end
        2'b01: full <= 1'b0;
        2'b11: begin
          full <= full;
          if (full) begin
            data_q <= s_data;
          end
        end
        default: full <= full;
      endcase
    end
  end

endmodule
