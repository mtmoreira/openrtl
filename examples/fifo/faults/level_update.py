"""Small deterministic FIFO trace with an optional level-update fault."""

from __future__ import annotations


def render_fifo_trace(*, level_update_fault: bool = False) -> str:
    """Render a bounded trace; the fault leaves level at one after a second write."""

    level_at_full = "01" if level_update_fault else "10"
    full_at_full = "0" if level_update_fault else "1"
    return (
        "$timescale 1 ns $end\n"
        "$scope module sync_fifo $end\n"
        "$var wire 1 ! clk $end\n"
        '$var wire 1 " rst_n $end\n'
        "$var wire 1 # wr_valid $end\n"
        "$var wire 1 $ wr_ready $end\n"
        "$var wire 1 % write_accepted $end\n"
        "$var wire 8 & wr_data [7:0] $end\n"
        "$var wire 1 ' rd_valid $end\n"
        "$var wire 1 ( rd_ready $end\n"
        "$var wire 1 ) read_accepted $end\n"
        "$var wire 8 * rd_data [7:0] $end\n"
        "$var wire 2 + level [1:0] $end\n"
        "$var wire 1 , full $end\n"
        "$var wire 1 - empty $end\n"
        "$var wire 1 . write_pointer $end\n"
        "$var wire 1 / read_pointer $end\n"
        "$var wire 32 0 DEPTH [31:0] $end\n"
        "$upscope $end\n"
        "$enddefinitions $end\n"
        "#0\n0!\n1\"\n1#\n1$\n1%\nb00001010 &\n0'\n0(\n0)\n"
        "b00000000 *\nb00 +\n0,\n1-\n0.\n0/\n"
        "b00000000000000000000000000000010 0\n"
        "#5\n1!\nb01 +\n0-\n1'\n1.\nb00001010 *\n"
        "#6\nb00001011 &\n1(\n1)\n"
        "#10\n0!\n"
        "#15\n1!\n0.\n1/\nb00001011 *\n"
        "#16\nb00001100 &\n0(\n0)\n"
        "#20\n0!\n"
        f"#25\n1!\nb{level_at_full} +\n{full_at_full},\n1.\n0$\n0%\n"
        "#26\nb00001101 &\n"
        "#30\n0!\n"
        "#35\n1!\n"
        "#36\n1(\n1)\n1$\n1%\n"
        "#40\n0!\n"
        "#45\n1!\n0.\n0/\nb00001100 *\n"
        "#50\n0!\n"
    )
