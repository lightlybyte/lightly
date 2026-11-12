"""
scheduler.py - Command ring, block scheduler, and warp lifecycle for Re:G.

Responsibilities:
  - Parse commands from the command ring (submitted by the host driver)
  - Launch thread blocks (groups of warps) onto SIMT cores
  - Track block completion via DMA fences and warp retirement
  - Provide fence signalling back to the host driver

Command format (in global RAM, written by the driver):
  ┌──────────┬────────────────────────────────────────┐
  │ word     │ meaning                                │
  ├──────────┼────────────────────────────────────────┤
  │ 0        │ opcode (u32)                           │
  │ 1        │ grid_x, grid_y (packed u16s)           │
  │ 2        │ block_x, block_y (packed u16s)         │
  │ 3        │ warps_per_block (u32)                  │
  │ 4        │ kernel_pc (u32)                        │
  │ 5        │ args_addr (u32)                        │
  │ 6        │ fence_id (u32)                         │
  └──────────┴────────────────────────────────────────┘
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cpu import SIMTCore
    from ram import MemorySystem, GlobalRAM
    from dma import DMAEngine


# ============================================================
# COMMAND OPCODES
# ============================================================

class CmdOp(IntEnum):
    NOP        = 0
    DISPATCH   = 1   # launch a compute grid
    COPY       = 2   # DMA copy
    FENCE      = 3   # insert a fence
    SET_FB     = 4   # set framebuffer origin
    PRESENT    = 5   # signal display endpoint


# ============================================================
# BLOCK
# ============================================================

@dataclass(slots=True)
class Block:
    """A thread block: a group of warps sharing the same kernel PC."""
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
    launched: bool = False
    retired: bool = False


# ============================================================
# SCHEDULER
# ============================================================

class Scheduler:
    """
    Owns:
      - the command ring cursor
      - the block dispatch queue
      - per-core block assignment
      - completion tracking
    """

    __slots__ = (
        "cores", "memory", "dma", "fences",
        "ring_base", "ring_head", "ring_tail",
        "pending_blocks", "running_blocks",
        "cycles", "halted",
    )

    def __init__(self, cores: list["SIMTCore"], memory: "MemorySystem",
                 dma: "DMAEngine", ring_base: int = 0x100):
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

    # ---------- fence API ----------

    def new_fence(self) -> int:
        fid = len(self.fences)
        self.fences[fid] = False
        return fid

    def signal_fence(self, fid: int) -> None:
        if fid >= 0:
            self.fences[fid] = True

    def is_fence_signalled(self, fid: int) -> bool:
        return self.fences.get(fid, False)

    # ---------- command ring ----------

    def submit_command(self, words: list[int]) -> int:
        """Driver writes a command to the ring. Returns the new head."""
        mem = self.memory.global_ram
        for i, w in enumerate(words):
            mem.write_u32(self.ring_tail + i * 4, w & 0xFFFFFFFF)
        self.ring_tail += len(words) * 4
        return self.ring_tail

    def process_ring(self) -> int:
        """Drain the ring, executing commands. Returns number processed."""
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
                    gx=grid & 0xFFFF, gy=(grid >> 16) & 0xFFFF,
                    bx=block & 0xFFFF, by=(block >> 16) & 0xFFFF,
                    warps=warps, kernel_pc=kernel_pc,
                    args_addr=args_addr, fence_id=fence_id,
                )
                self.ring_head += 28
            elif op == CmdOp.COPY:
                src = mem.read_u32(self.ring_head + 4)
                dst = mem.read_u32(self.ring_head + 8)
                length = mem.read_u32(self.ring_head + 12)
                ch = mem.read_u32(self.ring_head + 16)
                fence_id = mem.read_u32(self.ring_head + 20)
                from dma import DMAChannel
                self.dma.submit_copy(DMAChannel(ch), src, dst, length, fence_id)
                self.ring_head += 24
            elif op == CmdOp.FENCE:
                fid = mem.read_u32(self.ring_head + 4)
                self.signal_fence(fid)
                self.ring_head += 8
            elif op == CmdOp.PRESENT:
                # signal display endpoint (host-side; here just a flag)
                self.ring_head += 4
            else:
                raise RuntimeError(f"unknown command {op:#x} at {self.ring_head:#x}")
            processed += 1
        return processed

    # ---------- dispatch ----------

    def _enqueue_dispatch(self, gx: int, gy: int, bx: int, by: int,
                          warps: int, kernel_pc: int,
                          args_addr: int, fence_id: int) -> None:
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

    def _find_free_core(self) -> int | None:
        for i, core in enumerate(self.cores):
            if len(core.active_warps) + 4 <= len(core.warps):
                return i
        return None

    def _dispatch_pending(self) -> None:
        """Move pending blocks onto idle cores."""
        while self.pending_blocks:
            core_id = self._find_free_core()
            if core_id is None:
                return
            block = self.pending_blocks.pop(0)
            core = self.cores[core_id]
            base = len(core.active_warps)
            for w in range(block.warps):
                wid = base + w
                if wid >= len(core.warps):
                    break
                core.launch_warp(wid, block.kernel_pc)
            block.core_id = core_id
            block.launched = True
            self.running_blocks.append(block)

    # ---------- tick ----------

    def tick(self) -> None:
        """One scheduler cycle: advance all cores, check completions."""
        self.cycles += 1
        self.process_ring()
        self._dispatch_pending()

        for core in self.cores:
            core.step()

        self.dma.tick()

        # retire blocks whose core warps are all done
        still_running = []
        for block in self.running_blocks:
            core = self.cores[block.core_id]
            if core.all_done():
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


# ============================================================
# SMOKE TEST
# ============================================================

if __name__ == "__main__":
    from cpu import SIMTCore, Opcode, encode
    from ram import MemorySystem
    from dma import DMAEngine

    mem = MemorySystem(num_cores=2, fb_width=320, fb_height=240)
    cores = [SIMTCore(core_id=i, num_warps=16, memory=mem.global_ram) # type: ignore
             for i in range(2)]
    dma = DMAEngine(mem.global_ram, mem.scratchpads, mem.framebuffer)
    sched = Scheduler(cores, mem, dma)

    # Write a trivial kernel: HALT
    mem.global_ram.write_u64(0x1000, encode(Opcode.HALT))

    fence = sched.new_fence()
    sched.submit_command([
        CmdOp.DISPATCH, 1, 1, 2, 0x1000, 0, fence
    ])

    cycles = sched.run(max_cycles=10_000)
    assert sched.is_fence_signalled(fence), "dispatch fence not signalled"
    print(f"scheduler ran {cycles} cycles, fence {fence} signalled")
    print("all tests passed")