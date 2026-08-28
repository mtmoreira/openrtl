from __future__ import annotations

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ReadOnly, RisingEdge, Timer


@cocotb.test()
async def accepted_write_increments_level(dut: object) -> None:
    """Expose the reviewed level fault with one deterministic accepted write."""

    await cocotb.start(Clock(dut.clk, 10, unit="ns").start())
    dut.rst_n.value = 0
    dut.wr_valid.value = 0
    dut.wr_data.value = 0
    dut.rd_ready.value = 0
    await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    dut.wr_valid.value = 1
    dut.wr_data.value = 0x2A
    await Timer(1, unit="ns")
    assert int(dut.wr_ready.value) == 1
    await RisingEdge(dut.clk)
    await ReadOnly()
    assert int(dut.level.value) == 1
    await Timer(1, unit="ns")
