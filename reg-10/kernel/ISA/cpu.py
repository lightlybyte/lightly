"""
cpu.py - SIMT processor core for the Re:G ISA virtual machine.

Architecture:
  - Warps of 32 lanes, executed in lockstep (SIMT)
  - Per-lane general-purpose and floating-point register files
  - Warp scheduler with round-robin arbitration across resident warps
  - Divergence stack for branch reconvergence
  - Scratchpad memory per core (software-managed, no cache coherence)
  - Tensor units as asynchronous co-processors with busy/free state
  - Explicit memory model: global memory + per-core scratchpad
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional
import struct

# ============================================================
# ISA DEFINITION
# ============================================================

LANES = 32
NUM_GP = 32
NUM_FP = 32
NUM_PRED = 8

class Opcode(IntEnum):
    NOP      = 0x00
    HALT     = 0x01
    # Memory
    LDG      = 0x02   # Rd, [Ra + imm]      global load
    STG      = 0x03   # [Ra + imm], Rd      global store
    LDS      = 0x04   # Rd, [Ra + imm]      scratchpad load
    STS      = 0x05   # [Ra + imm], Rd      scratchpad store
    # Integer ALU
    MOV      = 0x06
    MOVI     = 0x07
    IADD     = 0x08
    ISUB     = 0x09
    IMUL     = 0x0A
    IAND     = 0x0B
    IOR      = 0x0C
    IXOR     = 0x0D
    SHL      = 0x0E
    SHR      = 0x0F
    # FP ALU
    FADD     = 0x10
    FSUB     = 0x11
    FMUL     = 0x12
    FFMA     = 0x13   # Fd = Fs * Ft + Fd
    FMIN     = 0x14
    FMAX     = 0x15
    # Conversion
    I2F      = 0x16
    F2I      = 0x17
    # Predication
    SETP     = 0x18   # Pd, Rs, Rt -> predicate
    # Control flow
    BRA      = 0x1A   # imm (PC-relative)
    BRA_DIV  = 0x1B   # divergent branch (pushes reconvergence)
    RET      = 0x1C
    # SIMT intrinsics
    VOTE     = 0x1D   # warp vote (any/all/ballot)
    SHFL     = 0x1E   # warp shuffle
    # Tensor
    TENSOR   = 0x1F   # async tensor op
    # Special
    SYSCALL  = 0x20
    BAR      = 0x21   # warp barrier


# 64-bit instruction encoding for SIMT (more room for operands)
# [63:58] opcode (6 bits)
# [57:53] rd (5)
# [52:48] ra (5)
# [47:43] rb (5)
# [42:38] rc (5)
# [37:33] pd (5) predicate dest
# [32]    pred (1) instruction predicated?
# [31:16] imm16 (16 signed)
# [15:0]  imm16b (16, for second immediate or tensor config)

def encode(op: Opcode, rd=0, ra=0, rb=0, rc=0, pd=0,
           pred=False, imm=0, imm2=0) -> int:
    return ((op & 0x3F) << 58) | ((rd & 0x1F) << 53) | ((ra & 0x1F) << 48) | \
           ((rb & 0x1F) << 43) | ((rc & 0x1F) << 38) | ((pd & 0x1F) << 33) | \
           ((1 if pred else 0) << 32) | ((imm & 0xFFFF) << 16) | (imm2 & 0xFFFF)


def sext16(v: int) -> int:
    return v - 0x10000 if v & 0x8000 else v


# ============================================================
# WARP STATE
# ============================================================

@dataclass
class Warp:
    """A single warp of 32 SIMT lanes."""
    warp_id: int
    gp: list = field(default_factory=lambda: [[0] * LANES for _ in range(NUM_GP)])
    fp: list = field(default_factory=lambda: [[0.0] * LANES for _ in range(NUM_FP)])
    pred: list = field(default_factory=lambda: [True] * LANES)
    pc: int = 0
    active_mask: int = 0xFFFFFFFF          # which lanes are alive
    reconvergence: list = field(default_factory=list)  # divergence stack
    state: str = "READY"                   # READY | RUNNING | WAITING | DONE
    wait_reason: str = ""
    cycles_run: int = 0

    def __post_init__(self):
        self.reconvergence = []

    def get_gp(self, reg: int, lane: int) -> int:
        return 0 if reg == 0 else self.gp[reg][lane]

    def set_gp(self, reg: int, lane: int, val: int) -> None:
        if reg != 0:
            self.gp[reg][lane] = val & 0xFFFFFFFF

    def get_fp(self, reg: int, lane: int) -> float:
        return self.fp[reg][lane]

    def set_fp(self, reg: int, lane: int, val: float) -> None:
        self.fp[reg][lane] = val

    def active_lanes(self) -> list:
        return [i for i in range(LANES) if (self.active_mask >> i) & 1]


# ============================================================
# SCRATCHPAD
# ============================================================

class Scratchpad:
    """Per-core software-managed scratchpad memory."""
    def __init__(self, size_bytes: int = 64 * 1024):
        self.size = size_bytes
        self.mem = bytearray(size_bytes)

    def read(self, addr: int, size: int = 4, signed: bool = False) -> int:
        addr &= (self.size - 1)
        return int.from_bytes(self.mem[addr:addr + size], "little", signed=signed)

    def write(self, addr: int, val: int, size: int = 4) -> None:
        addr &= (self.size - 1)
        self.mem[addr:addr + size] = (val & ((1 << (size * 8)) - 1)).to_bytes(size, "little")

    def read_f(self, addr: int) -> float:
        addr &= (self.size - 1)
        return struct.unpack("<f", self.mem[addr:addr + 4])[0]

    def write_f(self, addr: int, val: float) -> None:
        addr &= (self.size - 1)
        self.mem[addr:addr + 4] = struct.pack("<f", val)


# ============================================================
# TENSOR UNIT
# ============================================================

@dataclass
class TensorUnit:
    busy_cycles: int = 0
    result: float = 0.0
    dest_fp: int = 0
    dest_warp: int = -1

    def is_busy(self) -> bool:
        return self.busy_cycles > 0

    def tick(self) -> bool:
        if self.busy_cycles > 0:
            self.busy_cycles -= 1
            return self.busy_cycles == 0
        return False


# ============================================================
# SIMT CORE
# ============================================================

class SIMTCore:
    """
    A SIMT execution core. Owns:
      - a register file per warp (per-lane, per-register)
      - a warp scheduler (round-robin across resident warps)
      - a scratchpad
      - tensor units (async)
      - a global memory interface (shared with other cores via MemoryController)
    """

    def __init__(self, core_id: int, num_warps: int = 16,
                 scratchpad_bytes: int = 64 * 1024,
                 num_tensor_units: int = 2,
                 memory: "MemoryController | None" = None):
        self.core_id = core_id
        self.warps: list[Warp] = []
        self.active_warps: list[int] = []
        self.scratchpad = Scratchpad(scratchpad_bytes)
        self.tensor_units = [TensorUnit() for _ in range(num_tensor_units)]
        self.memory = memory or MemoryController()

        self.pc = 0
        self.cycles = 0
        self.scheduler_rr = 0  # round-robin cursor
        self.halted = False

        for wid in range(num_warps):
            w = Warp(warp_id=wid)
            w.pc = 0
            w.state = "READY"
            self.warps.append(w)

    # ---------- warp lifecycle ----------

    def launch_warp(self, warp_id: int, pc: int) -> None:
        w = self.warps[warp_id]
        w.pc = pc
        w.active_mask = 0xFFFFFFFF
        w.reconvergence.clear()
        w.state = "READY"
        w.pred = [True] * LANES
        if warp_id not in self.active_warps:
            self.active_warps.append(warp_id)

    def retire_warp(self, warp_id: int) -> None:
        w = self.warps[warp_id]
        w.state = "DONE"
        if warp_id in self.active_warps:
            self.active_warps.remove(warp_id)

    def all_done(self) -> bool:
        return len(self.active_warps) == 0

    # ---------- scheduler ----------

    def next_warp(self) -> Optional[Warp]:
        """Round-robin across ready warps. Skip WAITING/DONE."""
        n = len(self.active_warps)
        if n == 0:
            return None
        for _ in range(n):
            wid = self.active_warps[self.scheduler_rr % n]
            self.scheduler_rr = (self.scheduler_rr + 1) % n
            w = self.warps[wid]
            if w.state == "READY":
                return w
        return None

    # ---------- execution ----------

    def step(self) -> int:
        """Execute one warp-instruction. Returns number of warp-instructions issued."""
        w = self.next_warp()
        if w is None:
            return 0

        w.state = "RUNNING"
        self._issue(w)
        w.cycles_run += 1
        self.cycles += 1

        # Tick tensor units
        for i, tu in enumerate(self.tensor_units):
            if tu.tick():
                if tu.dest_warp >= 0:
                    dw = self.warps[tu.dest_warp]
                    for lane in dw.active_lanes():
                        dw.set_fp(tu.dest_fp, lane, tu.result)
                    if dw.state == "WAITING" and dw.wait_reason == f"tensor{i}":
                        dw.state = "READY"
                        dw.wait_reason = ""

        if w.state == "RUNNING":
            w.state = "READY"
        return 1

    def _issue(self, w: Warp) -> None:
        if w.pc + 7 >= self.memory.SIZE:
            raise RuntimeError(f"warp {w.warp_id}: PC out of bounds {w.pc:#x}")

        word = self.memory.read_global_u64(w.pc) # type: ignore
        w.pc += 8

        op = (word >> 58) & 0x3F
        rd = (word >> 53) & 0x1F
        ra = (word >> 48) & 0x1F
        rb = (word >> 43) & 0x1F
        rc = (word >> 38) & 0x1F
        pd = (word >> 33) & 0x1F
        pred_bit = (word >> 32) & 1
        imm = sext16((word >> 16) & 0xFFFF)
        imm2 = word & 0xFFFF

        try:
            opcode = Opcode(op)
        except ValueError:
            raise RuntimeError(f"warp {w.warp_id}: invalid opcode {op:#x} at pc={w.pc-8:#x}")

        lanes = w.active_lanes()
        if not lanes:
            return

        # Predicate masking: if instruction is predicated, only execute on lanes
        # whose predicate is True. Others are masked off for this instruction.
        if pred_bit and pd != 0xFF:
            exec_lanes = [l for l in lanes if w.pred[l]]
            if not exec_lanes:
                return
        else:
            exec_lanes = lanes

        if opcode == Opcode.NOP:
            pass
        elif opcode == Opcode.HALT:
            self.retire_warp(w.warp_id)
        elif opcode == Opcode.LDG:
            for l in exec_lanes:
                addr = w.get_gp(ra, l) + imm
                w.set_gp(rd, l, self.memory.read_u32(addr))
        elif opcode == Opcode.STG:
            for l in exec_lanes:
                addr = w.get_gp(ra, l) + imm
                self.memory.write_u32(addr, w.get_gp(rd, l))
        elif opcode == Opcode.LDS:
            for l in exec_lanes:
                addr = w.get_gp(ra, l) + imm
                w.set_gp(rd, l, self.scratchpad.read(addr))
        elif opcode == Opcode.STS:
            for l in exec_lanes:
                addr = w.get_gp(ra, l) + imm
                self.scratchpad.write(addr, w.get_gp(rd, l))
        elif opcode == Opcode.MOV:
            for l in exec_lanes:
                w.set_gp(rd, l, w.get_gp(ra, l))
        elif opcode == Opcode.MOVI:
            for l in exec_lanes:
                w.set_gp(rd, l, imm & 0xFFFFFFFF)
        elif opcode == Opcode.IADD:
            for l in exec_lanes:
                w.set_gp(rd, l, w.get_gp(ra, l) + w.get_gp(rb, l))
        elif opcode == Opcode.ISUB:
            for l in exec_lanes:
                w.set_gp(rd, l, w.get_gp(ra, l) - w.get_gp(rb, l))
        elif opcode == Opcode.IMUL:
            for l in exec_lanes:
                w.set_gp(rd, l, w.get_gp(ra, l) * w.get_gp(rb, l))
        elif opcode == Opcode.IAND:
            for l in exec_lanes:
                w.set_gp(rd, l, w.get_gp(ra, l) & w.get_gp(rb, l))
        elif opcode == Opcode.IOR:
            for l in exec_lanes:
                w.set_gp(rd, l, w.get_gp(ra, l) | w.get_gp(rb, l))
        elif opcode == Opcode.IXOR:
            for l in exec_lanes:
                w.set_gp(rd, l, w.get_gp(ra, l) ^ w.get_gp(rb, l))
        elif opcode == Opcode.SHL:
            for l in exec_lanes:
                w.set_gp(rd, l, w.get_gp(ra, l) << (w.get_gp(rb, l) & 31))
        elif opcode == Opcode.SHR:
            for l in exec_lanes:
                w.set_gp(rd, l, w.get_gp(ra, l) >> (w.get_gp(rb, l) & 31))
        elif opcode == Opcode.FADD:
            for l in exec_lanes:
                w.set_fp(rd, l, w.get_fp(ra, l) + w.get_fp(rb, l))
        elif opcode == Opcode.FSUB:
            for l in exec_lanes:
                w.set_fp(rd, l, w.get_fp(ra, l) - w.get_fp(rb, l))
        elif opcode == Opcode.FMUL:
            for l in exec_lanes:
                w.set_fp(rd, l, w.get_fp(ra, l) * w.get_fp(rb, l))
        elif opcode == Opcode.FFMA:
            for l in exec_lanes:
                w.set_fp(rd, l, w.get_fp(ra, l) * w.get_fp(rb, l) + w.get_fp(rd, l))
        elif opcode == Opcode.FMIN:
            for l in exec_lanes:
                w.set_fp(rd, l, min(w.get_fp(ra, l), w.get_fp(rb, l)))
        elif opcode == Opcode.FMAX:
            for l in exec_lanes:
                w.set_fp(rd, l, max(w.get_fp(ra, l), w.get_fp(rb, l)))
        elif opcode == Opcode.I2F:
            for l in exec_lanes:
                w.set_fp(rd, l, float(w.get_gp(ra, l)))
        elif opcode == Opcode.F2I:
            for l in exec_lanes:
                w.set_gp(rd, l, int(w.get_fp(ra, l)))
        elif opcode == Opcode.SETP:
            for l in exec_lanes:
                w.pred[pd] = (w.get_gp(ra, l) < w.get_gp(rb, l)) if imm == 0 else \
                             (w.get_gp(ra, l) == w.get_gp(rb, l)) if imm == 1 else \
                             (w.get_gp(ra, l) != w.get_gp(rb, l)) if imm == 2 else True
        elif opcode == Opcode.BRA:
            # Warp-uniform branch (no divergence)
            if all(w.pred[l] for l in exec_lanes):
                w.pc = (w.pc + (imm << 3)) & 0xFFFFFFFF
        elif opcode == Opcode.BRA_DIV:
            # Divergent branch: push reconvergence, execute one path now,
            # schedule the other later.
            taken = [l for l in exec_lanes if w.pred[l]]
            not_taken = [l for l in exec_lanes if not w.pred[l]]
            if not taken:
                pass  # fall through
            elif not not_taken:
                w.pc = (w.pc + (imm << 3)) & 0xFFFFFFFF
            else:
                # Push the fall-through path for later, take the branch now
                reconv = w.pc  # fall-through
                w.reconvergence.append((reconv, not_taken))
                w.active_mask = sum(1 << l for l in taken)
                w.pc = (w.pc + (imm << 3)) & 0xFFFFFFFF
        elif opcode == Opcode.RET:
            self.retire_warp(w.warp_id)
        elif opcode == Opcode.VOTE:
            # warp vote: all/any/ballot into rd for each lane
            mask = 0
            for l in exec_lanes:
                if w.pred[l]:
                    mask |= (1 << l)
            if imm == 0:  # ballot
                for l in exec_lanes:
                    w.set_gp(rd, l, mask)
            elif imm == 1:  # any
                val = 1 if mask else 0
                for l in exec_lanes:
                    w.set_gp(rd, l, val)
            elif imm == 2:  # all
                val = 1 if mask == w.active_mask else 0
                for l in exec_lanes:
                    w.set_gp(rd, l, val)
        elif opcode == Opcode.SHFL:
            # warp shuffle: rd[l] = ra[src_lane]
            src = imm & 0x1F
            for l in exec_lanes:
                w.set_gp(rd, l, w.get_gp(ra, src))
        elif opcode == Opcode.TENSOR:
            unit = (imm >> 8) & 0x3
            if unit >= len(self.tensor_units):
                raise RuntimeError(f"invalid tensor unit {unit}")
            tu = self.tensor_units[unit]
            if tu.is_busy():
                w.state = "WAITING"
                w.wait_reason = f"tensor{unit}"
                w.pc -= 8  # re-issue when ready
                return
            tu.busy_cycles = 4
            tu.dest_fp = rd
            tu.dest_warp = w.warp_id
            tu.result = w.get_fp(ra, exec_lanes[0]) * w.get_fp(rb, exec_lanes[0])
        elif opcode == Opcode.BAR:
            # warp barrier: no-op in this simplified model
            pass
        elif opcode == Opcode.SYSCALL:
            if imm == 1:
                for l in exec_lanes:
                    print(w.get_gp(ra, l))
            elif imm == 2:
                for l in exec_lanes:
                    print(w.get_fp(ra, l))
        else:
            raise RuntimeError(f"warp {w.warp_id}: unhandled {opcode.name}")

        # Reconvergence: if this warp is at the reconvergence PC and has
        # a pending path, restore it.
        if w.reconvergence and w.pc == w.reconvergence[-1][0]:
            reconv_pc, pending = w.reconvergence.pop()
            w.active_mask |= sum(1 << l for l in pending)
            w.pc = reconv_pc

    def run(self, max_cycles: int = 10_000_000) -> int:
        while not self.all_done() and self.cycles < max_cycles:
            self.step()
        if self.cycles >= max_cycles:
            raise RuntimeError(f"max cycles ({max_cycles}) exceeded")
        return self.cycles


# ============================================================
# GLOBAL MEMORY (shared across cores)
# ============================================================

class MemoryController:
    SIZE = 1 << 24  # 16 MiB global

    def __init__(self):
        self.mem = bytearray(self.SIZE)

    def read_u32(self, addr: int) -> int:
        addr &= (self.SIZE - 1)
        return int.from_bytes(self.mem[addr:addr + 4], "little")

    def write_u32(self, addr: int, val: int) -> None:
        addr &= (self.SIZE - 1)
        self.mem[addr:addr + 4] = (val & 0xFFFFFFFF).to_bytes(4, "little")

    def read_u64(self, addr: int) -> int:
        addr &= (self.SIZE - 1)
        return int.from_bytes(self.mem[addr:addr + 8], "little")

    def write_u64(self, addr: int, val: int) -> None:
        addr &= (self.SIZE - 1)
        self.mem[addr:addr + 8] = (val & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "little")

    def read_f32(self, addr: int) -> float:
        addr &= (self.SIZE - 1)
        return struct.unpack("<f", self.mem[addr:addr + 4])[0]

    def write_f32(self, addr: int, val: float) -> None:
        addr &= (self.SIZE - 1)
        self.mem[addr:addr + 4] = struct.pack("<f", val)

    def load(self, data: bytes, addr: int = 0) -> None:
        addr &= (self.SIZE - 1)
        self.mem[addr:addr + len(data)] = data


# ============================================================
# SMOKE TEST
# ============================================================

if __name__ == "__main__":
    print("SIMT core smoke test")
    mem = MemoryController()
    core = SIMTCore(core_id=0, num_warps=4, memory=mem)

    # Program: for each lane l, compute l * 2 + 1 into gp1, then halt.
    # We encode a few instructions at addr 0.
    prog = [
        encode(Opcode.MOV, rd=1, ra=0, imm=1),      # not used
        encode(Opcode.HALT),
    ]
    for i, w in enumerate(prog):
        mem.write_u64(i * 8, w)

    core.launch_warp(0, 0)
    cycles = core.run(max_cycles=1000)
    print(f"warp 0 retired after {cycles} cycles, state={core.warps[0].state}")