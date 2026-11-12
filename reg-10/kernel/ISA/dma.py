"""
dma.py - Async DMA engine for Re:G.

Design:
  - DMA transfers are submitted to per-channel queues
  - Each channel ticks once per core cycle
  - The warp scheduler calls `tick()` every cycle; DMA progress happens
    in the background between warp issues
  - Transfers are described by a `DMADescriptor`
  - Completion is signalled by setting a fence flag that the kernel can poll
  - No locks — the core is single-threaded from Python's perspective

Channels:
  - CH_G2S: global  -> scratchpad
  - CH_S2G: scratchpad -> global
  - CH_G2F: global -> framebuffer
  - CH_F2G: framebuffer -> global
  - CH_S2F: scratchpad -> framebuffer
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ram import GlobalRAM, Scratchpad, Framebuffer


# ============================================================
# CHANNELS
# ============================================================

class DMAChannel(IntEnum):
    G2S = 0   # global -> scratchpad
    S2G = 1   # scratchpad -> global
    G2F = 2   # global -> framebuffer
    F2G = 3   # framebuffer -> global
    S2F = 4   # scratchpad -> framebuffer


# Bytes moved per cycle per channel. Tuned for throughput, not latency.
DMA_BANDWIDTH = {
    DMAChannel.G2S: 64,
    DMAChannel.S2G: 64,
    DMAChannel.G2F: 256,
    DMAChannel.F2G: 256,
    DMAChannel.S2F: 128,
}


# ============================================================
# DESCRIPTOR
# ============================================================

@dataclass(slots=True)
class DMADescriptor:
    """A single DMA transfer."""
    channel: DMAChannel
    src_addr: int
    dst_addr: int
    length: int
    fence_id: int = -1          # fence to signal on completion (-1 = none)
    # internal state
    _offset: int = field(default=0, repr=False)
    _done: bool = field(default=False, repr=False)


# ============================================================
# FENCE TRACKING
# ============================================================

class FenceTable:
    """
    Tracks completion of DMA transfers.
    The kernel polls `is_signalled(fence_id)` to know when a transfer
    has landed. Fence IDs are allocated by the kernel.
    """
    __slots__ = ("_fences",)

    def __init__(self):
        self._fences: dict[int, bool] = {}

    def allocate(self) -> int:
        fid = len(self._fences)
        self._fences[fid] = False
        return fid

    def signal(self, fid: int) -> None:
        if fid >= 0:
            self._fences[fid] = True

    def is_signalled(self, fid: int) -> bool:
        return self._fences.get(fid, False)

    def reset(self, fid: int) -> None:
        if fid in self._fences:
            self._fences[fid] = False


# ============================================================
# DMA ENGINE
# ============================================================

class DMAEngine:
    """
    Async copy engine. Holds per-channel queues and ticks them
    once per core cycle. Every channel can have multiple pending
    descriptors; they execute FIFO.
    """

    __slots__ = ("global_ram", "scratchpads", "framebuffer",
                 "queues", "fences", "cycles", "bytes_moved")

    def __init__(self, global_ram: "GlobalRAM",
                 scratchpads,  # ScratchpadPool
                 framebuffer: "Framebuffer"):
        self.global_ram = global_ram
        self.scratchpads = scratchpads
        self.framebuffer = framebuffer
        self.queues: dict[DMAChannel, list[DMADescriptor]] = {
            ch: [] for ch in DMAChannel
        }
        self.fences = FenceTable()
        self.cycles = 0
        self.bytes_moved = 0

    # ---------- submission ----------

    def submit(self, desc: DMADescriptor) -> None:
        self.queues[desc.channel].append(desc)

    def submit_copy(self, channel: DMAChannel,
                    src_addr: int, dst_addr: int, length: int,
                    fence_id: int = -1) -> DMADescriptor:
        desc = DMADescriptor(channel, src_addr, dst_addr, length, fence_id)
        self.submit(desc)
        return desc

    def new_fence(self) -> int:
        return self.fences.allocate()

    def is_signalled(self, fence_id: int) -> bool:
        return self.fences.is_signalled(fence_id)

    def pending(self, channel: DMAChannel | None = None) -> int:
        if channel is None:
            return sum(len(q) for q in self.queues.values())
        return len(self.queues[channel])

    # ---------- tick ----------

    def tick(self) -> int:
        """
        Advance all channels by one cycle.
        Returns the number of bytes moved this cycle.
        """
        self.cycles += 1
        moved = 0
        for ch, queue in self.queues.items():
            if not queue:
                continue
            budget = DMA_BANDWIDTH[ch]
            while queue and budget > 0:
                desc = queue[0]
                remaining = desc.length - desc._offset
                if remaining <= 0:
                    queue.pop(0)
                    if desc.fence_id >= 0:
                        self.fences.signal(desc.fence_id)
                    desc._done = True
                    continue
                chunk = min(budget, remaining)
                self._transfer(ch, desc, chunk)
                desc._offset += chunk
                budget -= chunk
                moved += chunk
                if desc._offset >= desc.length:
                    queue.pop(0)
                    if desc.fence_id >= 0:
                        self.fences.signal(desc.fence_id)
                    desc._done = True
        self.bytes_moved += moved
        return moved

    # ---------- per-channel transfer ----------

    def _transfer(self, ch: DMAChannel, desc: DMADescriptor, n: int) -> None:
        src = desc.src_addr + desc._offset
        dst = desc.dst_addr + desc._offset

        if ch == DMAChannel.G2S:
            # scratchpad index comes from the descriptor's channel state;
            # we encode it in the high bits of dst_addr for simplicity
            pad_id = (dst >> 24) & 0xFF
            pad_off = dst & 0xFFFFFF
            pad = self.scratchpads.get(pad_id)
            data = self.global_ram.read_block(src, n)
            pad.mem[pad_off:pad_off + n] = data

        elif ch == DMAChannel.S2G:
            pad_id = (src >> 24) & 0xFF
            pad_off = src & 0xFFFFFF
            pad = self.scratchpads.get(pad_id)
            data = bytes(pad.mv[pad_off:pad_off + n])
            self.global_ram.write_block(dst, data)

        elif ch == DMAChannel.G2F:
            data = self.global_ram.read_block(src, n)
            self.framebuffer.buffer[dst:dst + n] = data

        elif ch == DMAChannel.F2G:
            data = bytes(self.framebuffer.view()[src:src + n])
            self.global_ram.write_block(dst, data)

        elif ch == DMAChannel.S2F:
            pad_id = (src >> 24) & 0xFF
            pad_off = src & 0xFFFFFF
            pad = self.scratchpads.get(pad_id)
            data = bytes(pad.mv[pad_off:pad_off + n])
            self.framebuffer.buffer[dst:dst + n] = data


# ============================================================
# SMOKE TEST
# ============================================================

if __name__ == "__main__":
    from ram import MemorySystem

    print("DMA engine smoke test")
    mem = MemorySystem(num_cores=2, fb_width=640, fb_height=480)
    dma = DMAEngine(mem.global_ram, mem.scratchpads, mem.framebuffer)

    # Fill global with a test pattern
    pattern = bytes(range(256)) * 4
    mem.global_ram.write_block(0x1000, pattern)

    # 1. Global -> scratchpad 0
    fence = dma.new_fence()
    dma.submit_copy(DMAChannel.G2S, 0x1000, (0 << 24) | 0x0, 256, fence)

    # 2. Scratchpad 0 -> framebuffer row 0
    fence2 = dma.new_fence()
    dma.submit_copy(DMAChannel.S2F, (0 << 24) | 0x0, 0, 256, fence2)

    # Tick until done
    for _ in range(100):
        dma.tick()
        if dma.is_signalled(fence) and dma.is_signalled(fence2):
            break

    assert dma.is_signalled(fence), "G2S fence not signalled"
    assert dma.is_signalled(fence2), "S2F fence not signalled"
    assert mem.framebuffer.buffer[0:256] == pattern[:256], "framebuffer mismatch"

    print(f"transferred {dma.bytes_moved} bytes in {dma.cycles} cycles")
    print("all tests passed")