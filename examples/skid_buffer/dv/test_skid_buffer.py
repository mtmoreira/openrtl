from __future__ import annotations

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ReadOnly, RisingEdge, Timer

from examples.skid_buffer.model import SkidBufferModel


async def _sample_cycle(
    dut: object,
    model: SkidBufferModel,
    *,
    s_valid: bool,
    s_data: int,
    m_ready: bool,
) -> tuple[str, ...]:
    dut.s_valid.value = s_valid
    dut.s_data.value = s_data
    dut.m_ready.value = m_ready
    await Timer(1, unit="ns")
    expected = model.cycle(s_valid=s_valid, s_data=s_data, m_ready=m_ready)
    mismatches: list[str] = []
    observed = {
        "s_ready": int(dut.s_ready.value),
        "m_valid": int(dut.m_valid.value),
        "m_data": int(dut.m_data.value),
    }
    wanted = {
        "s_ready": int(expected.s_ready),
        "m_valid": int(expected.m_valid),
        "m_data": expected.m_data,
    }
    for name, value in wanted.items():
        if observed[name] != value:
            mismatches.append(f"{name}: expected {value}, observed {observed[name]}")
    await RisingEdge(dut.clk)
    await ReadOnly()
    if int(dut.occupied.value) != int(expected.occupied_after):
        mismatches.append(
            f"occupied: expected {int(expected.occupied_after)}, observed {int(dut.occupied.value)}"
        )
    await Timer(1, unit="ns")
    return tuple(mismatches)


@cocotb.test()
async def ready_valid_skid_buffer_contract(dut: object) -> None:
    await cocotb.start(Clock(dut.clk, 10, unit="ns").start())
    model = SkidBufferModel(width=8)
    dut.rst_n.value = 0
    dut.s_valid.value = 0
    dut.s_data.value = 0
    dut.m_ready.value = 0
    await RisingEdge(dut.clk)
    model.cycle(rst_n=False)
    await RisingEdge(dut.clk)
    model.cycle(rst_n=False)
    dut.rst_n.value = 1

    mismatches: list[str] = []
    mismatches.extend(
        await _sample_cycle(dut, model, s_valid=True, s_data=0x11, m_ready=False)
    )
    mismatches.extend(
        await _sample_cycle(dut, model, s_valid=True, s_data=0x22, m_ready=True)
    )
    mismatches.extend(
        await _sample_cycle(dut, model, s_valid=False, s_data=0, m_ready=True)
    )
    for _ in range(3):
        mismatches.extend(
            await _sample_cycle(dut, model, s_valid=False, s_data=0, m_ready=False)
        )

    if not mismatches:
        rng = random.Random(29)
        for _ in range(120):
            mismatches.extend(
                await _sample_cycle(
                    dut,
                    model,
                    s_valid=bool(rng.getrandbits(1)),
                    s_data=rng.randrange(256),
                    m_ready=bool(rng.getrandbits(1)),
                )
            )
    assert not mismatches, "; ".join(mismatches)
