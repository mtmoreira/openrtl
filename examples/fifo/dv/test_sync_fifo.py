from __future__ import annotations

import json
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ReadOnly, RisingEdge, Timer

from examples.fifo.model import SyncFifoModel


def log_event(timestamp_fs: int, event: str, message: str, **fields: int | bool) -> None:
    cocotb.log.info(
        json.dumps(
            {
                "component": "fifo.scoreboard",
                "event": event,
                "fields": fields,
                "level": "info",
                "message": message,
                "requirement_ids": ["fifo.order"],
                "timestamp_fs": timestamp_fs,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


@cocotb.test()
async def randomized_fifo_scoreboard(dut: object) -> None:
    await cocotb.start(Clock(dut.clk, 10, unit="ns").start())
    model = SyncFifoModel(width=8, depth=4)
    rng = random.Random(int(os.environ.get("RANDOM_SEED", "1")))
    dut.rst_n.value = 0
    dut.wr_valid.value = 0
    dut.rd_ready.value = 0
    await RisingEdge(dut.clk)
    model.cycle(rst_n=False)
    dut.rst_n.value = 1

    for cycle in range(400):
        wr_valid = bool(rng.getrandbits(1))
        rd_ready = bool(rng.getrandbits(1))
        wr_data = rng.randrange(256)
        dut.wr_valid.value = wr_valid
        dut.wr_data.value = wr_data
        dut.rd_ready.value = rd_ready
        await Timer(1, unit="ns")
        expected = model.cycle(wr_valid=wr_valid, wr_data=wr_data, rd_ready=rd_ready)
        assert int(dut.wr_ready.value) == expected.wr_ready
        assert int(dut.rd_valid.value) == expected.rd_valid
        assert int(dut.rd_data.value) == expected.rd_data
        await RisingEdge(dut.clk)
        await ReadOnly()
        assert int(dut.level.value) == expected.level
        assert int(dut.empty.value) == expected.empty
        assert int(dut.full.value) == expected.full
        if expected.write_accepted or expected.read_accepted:
            log_event(
                (cycle + 2) * 10_000_000,
                "transfer.accepted",
                "FIFO transfer checked",
                write=expected.write_accepted,
                read=expected.read_accepted,
                level=expected.level,
            )
        await Timer(1, unit="ns")
