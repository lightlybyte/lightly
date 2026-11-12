"""
scheduler.py - Command ring, block scheduler, warp lifecycle for Re:G.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import IntEnum

from cpu import SIMTCore
from ram import MemorySystem
from dma import DMAEngine, DMAChannel


class CmdOp(IntEnum):
    NOP      = 0
    DISPATCH = 1
    COPY     = 2
    FENCE    = 3
    SET_FB   = 4
    PRESENT  = 5


@dataclass(slots=True)
class Block:
    block_id: int
    grid_x: int
    grid_y: int
    block_x: int
    block_y: int
    warps: int
    kernel_pc: int
    args_addr: int
    fence_id: int
    core_id: int = -1
    warp_ids: list = field(default_factory=list)
    launched: bool = False
    retired: bool = False


class Scheduler:
    __slots__ = (
        "cores", "memory", "dma", "fences",
        "ring_base", "ring_head", "ring_tail",
        "pending_blocks", "running_blocks",
        "cycles", "halted",
    )

    def __init__(self, cores, memory, dma, ring_base=0x100):
        self.cores = cores
        self.memory = memory
        self.dma = dma
        self.fences: dict[int, bool] = {}
        self.ring_base = ring_base
        self.ring_head = ring_base
        self.ring_tail = ring_base
        self.pending_blocks: list[Block] = []
        self.running_blocks: list[Block] = []
        self.cycles = 0
        self.halted = False

    def new_fence(self):
        fid = len(self.fences)
        self.fences[fid] = False
        return fid

    def signal_fence(self, fid):
        if fid >= 0:
            self.fences[fid] = True

    def is_fence_signalled(self, fid):
        return self.fences.get(fid, False)

    def submit_command(self, words):
        mem = self.memory.global_ram
        for i, w in enumerate(words):
            mem.write_u32(self.ring_tail + i * 4, w & 0xFFFFFFFF)
        self.ring_tail += len(words) * 4
        return self.ring_tail

    def process_ring(self):
        mem = self.memory.global_ram
        processed = 0
        while self.ring_head < self.ring_tail:
            op = mem.read_u32(self.ring_head)
            if op == CmdOp.NOP:
                self.ring_head += 4
            elif op == CmdOp.DISPATCH:
                grid = mem.read_u32(self.ring_head + 4)
                block = mem.read_u32(self.ring_head + 8)
                warps = mem.read_u32(self.ring_head + 12)
                kernel_pc = mem.read_u32(self.ring_head + 16)
                args_addr = mem.read_u32(self.ring_head + 20)
                fence_id = mem.read_u32(self.ring_head + 24)
                self._enqueue_dispatch(
                    grid & 0xFFFF, (grid >> 16) & 0xFFFF,
                    block & 0xFFFF, (block >> 16) & 0xFFFF,
                    warps, kernel_pc, args_addr, fence_id,
                )
                self.ring_head += 28
            elif op == CmdOp.COPY:
                src = mem.read_u32(self.ring_head + 4)
                dst = mem.read_u32(self.ring_head + 8)
                length = mem.read_u32(self.ring_head + 12)
                ch = mem.read_u32(self.ring_head + 16)
                fence_id = mem.read_u32(self.ring_head + 20)
                self.dma.submit_copy(DMAChannel(ch), src, dst, length, fence_id)
                self.ring_head += 24
            elif op == CmdOp.FENCE:
                self.signal_fence(mem.read_u32(self.ring_head + 4))
                self.ring_head += 8
            elif op == CmdOp.PRESENT:
                self.ring_head += 4
            else:
                raise RuntimeError(
                    f"unknown command {op:#x} at {self.ring_head:#x}")
            processed += 1
        return processed

    def _enqueue_dispatch(self, gx, gy, bx, by, warps, kernel_pc,
                          args_addr, fence_id):
        bid = 0
        for by_i in range(by):
            for bx_i in range(bx):
                for gy_i in range(gy):
                    for gx_i in range(gx):
                        self.pending_blocks.append(Block(
                            block_id=bid,
                            grid_x=gx_i, grid_y=gy_i,
                            block_x=bx_i, block_y=by_i,
                            warps=warps,
                            kernel_pc=kernel_pc,
                            args_addr=args_addr,
                            fence_id=fence_id,
                        ))
                        bid += 1

    def _free_warps_on(self, core_id: int, count: int) -> list[int] | None:
        core = self.cores[core_id]
        active = set(core.active_warps)
        free = [i for i in range(len(core.warps)) if i not in active]
        if len(free) < count:
            return None
        return free[:count]

    def _dispatch_pending(self):
        while self.pending_blocks:
            placed = False
            for core_id in range(len(self.cores)):
                warp_ids = self._free_warps_on(core_id, self.pending_blocks[0].warps)
                if warp_ids is None:
                    continue
                block = self.pending_blocks.pop(0)
                core = self.cores[core_id]
                for wid in warp_ids:
                    core.launch_warp(wid, block.kernel_pc)
                block.core_id = core_id
                block.warp_ids = list(warp_ids)
                block.launched = True
                self.running_blocks.append(block)
                placed = True
                break
            if not placed:
                return

    def tick(self):
        self.cycles += 1
        self.process_ring()
        self._dispatch_pending()
        for core in self.cores:
            core.step()
        self.dma.tick()

        still_running = []
        for block in self.running_blocks:
            core = self.cores[block.core_id]
            if all(core.warps[wid].state == "DONE" for wid in block.warp_ids):
                block.retired = True
                if block.fence_id >= 0:
                    self.signal_fence(block.fence_id)
            else:
                still_running.append(block)
        self.running_blocks = still_running

    def run(self, max_cycles: int = 10_000_000) -> int:
        while (self.pending_blocks or self.running_blocks
               or self.ring_head < self.ring_tail):
            if self.cycles >= max_cycles:
                raise RuntimeError(f"max cycles ({max_cycles}) exceeded")
            self.tick()
        return self.cycles