"""
dma.py - Async DMA engine for Re:G.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ram import GlobalRAM, Scratchpad, Framebuffer


class DMAChannel(IntEnum):
    G2S = 0
    S2G = 1
    G2F = 2
    F2G = 3
    S2F = 4


DMA_BANDWIDTH = {
    DMAChannel.G2S: 64,
    DMAChannel.S2G: 64,
    DMAChannel.G2F: 256,
    DMAChannel.F2G: 256,
    DMAChannel.S2F: 128,
}


@dataclass(slots=True)
class DMADescriptor:
    channel: DMAChannel
    src_addr: int
    dst_addr: int
    length: int
    fence_id: int = -1
    _offset: int = field(default=0, repr=False)
    _done: bool = field(default=False, repr=False)


class FenceTable:
    __slots__ = ("_fences",)
    def __init__(self):
        self._fences = {}
    def allocate(self):
        fid = len(self._fences)
        self._fences[fid] = False
        return fid
    def signal(self, fid):
        if fid >= 0:
            self._fences[fid] = True
    def is_signalled(self, fid):
        return self._fences.get(fid, False)
    def reset(self, fid):
        if fid in self._fences:
            self._fences[fid] = False


class DMAEngine:
    __slots__ = ("global_ram", "scratchpads", "framebuffer",
                 "queues", "fences", "cycles", "bytes_moved")

    def __init__(self, global_ram, scratchpads, framebuffer):
        self.global_ram = global_ram
        self.scratchpads = scratchpads
        self.framebuffer = framebuffer
        self.queues = {ch: [] for ch in DMAChannel}
        self.fences = FenceTable()
        self.cycles = 0
        self.bytes_moved = 0

    def submit(self, desc):
        self.queues[desc.channel].append(desc)

    def submit_copy(self, channel, src_addr, dst_addr, length, fence_id=-1):
        desc = DMADescriptor(channel, src_addr, dst_addr, length, fence_id)
        self.submit(desc)
        return desc

    def new_fence(self):
        return self.fences.allocate()

    def is_signalled(self, fid):
        return self.fences.is_signalled(fid)

    def pending(self, channel=None):
        if channel is None:
            return sum(len(q) for q in self.queues.values())
        return len(self.queues[channel])

    def tick(self):
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

    def _transfer(self, ch, desc, n):
        src = desc.src_addr + desc._offset
        dst = desc.dst_addr + desc._offset

        if ch == DMAChannel.G2S:
            pad_id = (dst >> 24) & 0xFF
            pad_off = dst & 0xFFFFFF
            pad = self.scratchpads.get(pad_id)
            pad.mem[pad_off:pad_off + n] = self.global_ram.read_block(src, n)
        elif ch == DMAChannel.S2G:
            pad_id = (src >> 24) & 0xFF
            pad_off = src & 0xFFFFFF
            pad = self.scratchpads.get(pad_id)
            self.global_ram.write_block(dst, bytes(pad.mv[pad_off:pad_off + n]))
        elif ch == DMAChannel.G2F:
            fb = self.framebuffer
            data = self.global_ram.read_block(src, n)
            off = fb.base + dst
            fb.buffer[off:off + n] = data
        elif ch == DMAChannel.F2G:
            fb = self.framebuffer
            data = bytes(fb.buffer[fb.base + src:fb.base + src + n])
            self.global_ram.write_block(dst, data)
        elif ch == DMAChannel.S2F:
            pad_id = (src >> 24) & 0xFF
            pad_off = src & 0xFFFFFF
            pad = self.scratchpads.get(pad_id)
            data = bytes(pad.mv[pad_off:pad_off + n])
            fb = self.framebuffer
            off = fb.base + dst
            fb.buffer[off:off + n] = data