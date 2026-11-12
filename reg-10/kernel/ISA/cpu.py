"""
cpu.py - SIMT processor core for the Re:G ISA virtual machine.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional
import struct

LANES = 32
NUM_GP = 32
NUM_FP = 32
NUM_PRED = 8


class Opcode(IntEnum):
    NOP      = 0x00
    HALT     = 0x01
    LDG      = 0x02
    STG      = 0x03
    LDS      = 0x04
    STS      = 0x05
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
    FADD     = 0x10
    FSUB     = 0x11
    FMUL     = 0x12
    FFMA     = 0x13
    FMIN     = 0x14
    FMAX     = 0x15
    I2F      = 0x16
    F2I      = 0x17
    SETP     = 0x18
    BRA      = 0x1A
    BRA_DIV  = 0x1B
    RET      = 0x1C
    VOTE     = 0x1D
    SHFL     = 0x1E
    TENSOR   = 0x1F
    SYSCALL  = 0x20
    BAR      = 0x21


def encode(op: Opcode, rd=0, ra=0, rb=0, rc=0, pd=0,
           pred=False, imm=0, imm2=0, pred_neg=False) -> int:
    return ((op & 0x3F) << 58) | ((rd & 0x1F) << 53) | ((ra & 0x1F) << 48) | \
           ((rb & 0x1F) << 43) | ((rc & 0x1F) << 38) | ((pd & 0x1F) << 33) | \
           ((1 if pred else 0) << 32) | ((1 if pred_neg else 0) << 31) | \
           (((imm >> 0) & 0x7FFF) << 16) | (imm2 & 0xFFFF)


def sext15(v: int) -> int:
    return v - 0x8000 if v & 0x4000 else v


@dataclass
class Warp:
    warp_id: int
    gp: list = field(default_factory=lambda: [[0] * LANES for _ in range(NUM_GP)])
    fp: list = field(default_factory=lambda: [[0.0] * LANES for _ in range(NUM_FP)])
    pred: list = field(default_factory=lambda: [True] * LANES)
    pc: int = 0
    active_mask: int = 0xFFFFFFFF
    reconvergence: list = field(default_factory=list)
    state: str = "READY"
    wait_reason: str = ""
    cycles_run: int = 0

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


class SIMTCore:
    def __init__(self, core_id: int, num_warps: int = 16,
                 scratchpad_bytes: int = 64 * 1024,
                 num_tensor_units: int = 2,
                 memory=None):
        from ram import Scratchpad, GlobalRAM
        self.core_id = core_id
        self.warps: list[Warp] = [Warp(warp_id=i) for i in range(num_warps)]
        self.active_warps: list[int] = []
        self.scratchpad = Scratchpad(scratchpad_bytes)
        self.tensor_units = [TensorUnit() for _ in range(num_tensor_units)]
        self.memory = memory if memory is not None else GlobalRAM()
        self.cycles = 0
        self.scheduler_rr = 0
        self.halted = False

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
        self.warps[warp_id].state = "DONE"
        if warp_id in self.active_warps:
            self.active_warps.remove(warp_id)

    def all_done(self) -> bool:
        return len(self.active_warps) == 0

    def next_warp(self) -> Optional[Warp]:
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

    def step(self) -> int:
        w = self.next_warp()
        if w is None:
            return 0
        w.state = "RUNNING"
        self._issue(w)
        w.cycles_run += 1
        self.cycles += 1

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

        word = self.memory.read_u64(w.pc)
        w.pc += 8

        op = (word >> 58) & 0x3F
        rd = (word >> 53) & 0x1F
        ra = (word >> 48) & 0x1F
        rb = (word >> 43) & 0x1F
        rc = (word >> 38) & 0x1F
        pd = (word >> 33) & 0x1F
        pred_bit = (word >> 32) & 1
        pred_neg = (word >> 31) & 1
        imm = sext15((word >> 16) & 0x7FFF)
        imm2 = word & 0xFFFF

        try:
            opcode = Opcode(op)
        except ValueError:
            raise RuntimeError(
                f"warp {w.warp_id}: invalid opcode {op:#x} at pc={w.pc-8:#x}")

        lanes = w.active_lanes()
        if not lanes:
            return

        if pred_bit:
            if pred_neg:
                exec_lanes = [l for l in lanes if not w.pred[l]]
            else:
                exec_lanes = [l for l in lanes if w.pred[l]]
            if not exec_lanes:
                return
        else:
            exec_lanes = lanes

        def b_of(l):
            return w.get_gp(rb, l) if rb != 0 else imm

        if opcode == Opcode.NOP:
            pass
        elif opcode == Opcode.HALT:
            self.retire_warp(w.warp_id)
        elif opcode == Opcode.LDG:
            for l in exec_lanes:
                w.set_gp(rd, l, self.memory.read_u32(w.get_gp(ra, l) + imm))
        elif opcode == Opcode.STG:
            for l in exec_lanes:
                self.memory.write_u32(w.get_gp(ra, l) + imm, w.get_gp(rd, l))
        elif opcode == Opcode.LDS:
            for l in exec_lanes:
                w.set_gp(rd, l, self.scratchpad.read_u32(w.get_gp(ra, l) + imm))
        elif opcode == Opcode.STS:
            for l in exec_lanes:
                self.scratchpad.write_u32(w.get_gp(ra, l) + imm, w.get_gp(rd, l))
        elif opcode == Opcode.MOV:
            for l in exec_lanes:
                w.set_gp(rd, l, w.get_gp(ra, l))
        elif opcode == Opcode.MOVI:
            for l in exec_lanes:
                w.set_gp(rd, l, imm & 0xFFFFFFFF)
        elif opcode == Opcode.IADD:
            for l in exec_lanes:
                w.set_gp(rd, l, w.get_gp(ra, l) + b_of(l))
        elif opcode == Opcode.ISUB:
            for l in exec_lanes:
                w.set_gp(rd, l, w.get_gp(ra, l) - b_of(l))
        elif opcode == Opcode.IMUL:
            for l in exec_lanes:
                w.set_gp(rd, l, w.get_gp(ra, l) * b_of(l))
        elif opcode == Opcode.IAND:
            for l in exec_lanes:
                w.set_gp(rd, l, w.get_gp(ra, l) & b_of(l))
        elif opcode == Opcode.IOR:
            for l in exec_lanes:
                w.set_gp(rd, l, w.get_gp(ra, l) | b_of(l))
        elif opcode == Opcode.IXOR:
            for l in exec_lanes:
                w.set_gp(rd, l, w.get_gp(ra, l) ^ b_of(l))
        elif opcode == Opcode.SHL:
            for l in exec_lanes:
                w.set_gp(rd, l, w.get_gp(ra, l) << (b_of(l) & 31))
        elif opcode == Opcode.SHR:
            for l in exec_lanes:
                w.set_gp(rd, l, w.get_gp(ra, l) >> (b_of(l) & 31))
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
                if imm == 0:
                    w.pred[pd] = w.get_gp(ra, l) < w.get_gp(rb, l)
                elif imm == 1:
                    w.pred[pd] = w.get_gp(ra, l) == w.get_gp(rb, l)
                elif imm == 2:
                    w.pred[pd] = w.get_gp(ra, l) != w.get_gp(rb, l)
                else:
                    w.pred[pd] = True
        elif opcode == Opcode.BRA:
            if all(w.pred[l] for l in exec_lanes):
                w.pc = (w.pc + (imm << 3)) & 0xFFFFFFFF
        elif opcode == Opcode.BRA_DIV:
            taken = [l for l in exec_lanes if w.pred[l]]
            not_taken = [l for l in exec_lanes if not w.pred[l]]
            if not taken:
                pass
            elif not not_taken:
                w.pc = (w.pc + (imm << 3)) & 0xFFFFFFFF
            else:
                w.reconvergence.append((w.pc, not_taken))
                w.active_mask = sum(1 << l for l in taken)
                w.pc = (w.pc + (imm << 3)) & 0xFFFFFFFF
        elif opcode == Opcode.RET:
            self.retire_warp(w.warp_id)
        elif opcode == Opcode.VOTE:
            mask = 0
            for l in exec_lanes:
                if w.pred[l]:
                    mask |= (1 << l)
            if imm == 0:
                for l in exec_lanes:
                    w.set_gp(rd, l, mask)
            elif imm == 1:
                v = 1 if mask else 0
                for l in exec_lanes:
                    w.set_gp(rd, l, v)
            elif imm == 2:
                v = 1 if mask == w.active_mask else 0
                for l in exec_lanes:
                    w.set_gp(rd, l, v)
        elif opcode == Opcode.SHFL:
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
                w.pc -= 8
                return
            tu.busy_cycles = 4
            tu.dest_fp = rd
            tu.dest_warp = w.warp_id
            tu.result = w.get_fp(ra, exec_lanes[0]) * w.get_fp(rb, exec_lanes[0])
        elif opcode == Opcode.BAR:
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