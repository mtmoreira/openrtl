"""Parameterized end-to-end scoreboard; no imports from producer packages."""
from collections import deque
import json
import os
from pathlib import Path
import random
from typing import Any

import cocotb
from cocotb.triggers import Timer


@cocotb.test()
async def composed_stream_contract(dut: Any) -> None:
    width = int(os.environ["COMPOSED_WIDTH"])
    depth = int(os.environ["COMPOSED_DEPTH"])
    seed = int(os.environ["RANDOM_SEED"])
    capacity = depth + 1
    data_modulus = 1 << width
    queue: deque[int] = deque()
    coverage = dict(accepted=0, delivered=0, backpressure=0, simultaneous=0,
                    stalled_output=0, resets=0, reset_with_data=0, max_occupancy=0)
    held: int | None = None

    async def cycle(valid: bool, data: int, ready: bool, reset: bool = False) -> bool:
        nonlocal held
        dut.clk.value = 0
        dut.rst_n.value = int(not reset)
        dut.s_valid.value = int(valid)
        dut.s_data.value = data
        dut.m_ready.value = int(ready)
        await Timer(5, unit="ns")
        take_in = valid and bool(int(dut.s_ready.value))
        take_out = bool(int(dut.m_valid.value)) and ready
        if reset:
            coverage["resets"] += 1
            coverage["reset_with_data"] += int(bool(queue))
            queue.clear()
            held = None
        else:
            if held is not None:
                assert int(dut.m_valid.value) == 1 and int(dut.m_data.value) == held
            if take_out:
                assert queue, "unexpected output without an accepted input"
                assert int(dut.m_data.value) == queue.popleft(), "stream order/data mismatch"
                coverage["delivered"] += 1
            if take_in:
                queue.append(data)
                coverage["accepted"] += 1
            coverage["simultaneous"] += int(take_in and take_out)
            coverage["backpressure"] += int(valid and not take_in)
            stalled = bool(int(dut.m_valid.value)) and not ready
            coverage["stalled_output"] += int(stalled)
            held = int(dut.m_data.value) if stalled else None
        dut.clk.value = 1
        await Timer(5, unit="ns")
        occupancy = int(dut.fifo_level.value) + int(dut.skid_occupied.value)
        assert occupancy == len(queue), "accepted/delivered occupancy mismatch"
        assert 0 <= occupancy <= capacity
        coverage["max_occupancy"] = max(coverage["max_occupancy"], occupancy)
        if reset:
            assert int(dut.m_valid.value) == 0
        return take_in and not reset

    await cycle(False, 0, False, True)
    next_data = 0
    for _ in range(capacity * 2 + 2):
        if await cycle(True, next_data, False):
            next_data = (next_data + 1) % data_modulus
    for _ in range(max(64, capacity * 4)):
        if await cycle(True, next_data, True):
            next_data = (next_data + 1) % data_modulus
    await cycle(False, 0, False, True)
    rng = random.Random(seed)
    pending = False
    for _ in range(400):
        pending = pending or rng.random() < 0.8
        if await cycle(pending, next_data, rng.random() < 0.55):
            next_data = (next_data + 1) % data_modulus
            pending = False
    # Honour any blocked input before ending the stream.
    if pending:
        for _ in range(8):
            if await cycle(True, next_data, True):
                break
        else:
            raise AssertionError("pending input did not complete")
    for _ in range(8):
        await cycle(False, 0, True)
    assert not queue and int(dut.m_valid.value) == 0
    assert coverage["max_occupancy"] == capacity
    assert all(coverage[key] > 0 for key in coverage)
    Path(os.environ["COMPOSED_COVERAGE"]).write_text(
        json.dumps({"schema": "openrtl.composed-stream-coverage.v1", "seed": seed,
                    "width": width, "depth": depth, "capacity": capacity,
                    "status": "passed", "drained": True, "counts": coverage},
                   sort_keys=True, indent=2) + "\n", encoding="utf-8")
