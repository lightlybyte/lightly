"""
cpu.py - SIMT processor core for the Re:G ISA v2.

Encoding (64-bit):
  [63:57] opcode    (7)  0-127 (was 6 bits in v1; opcodes >= 64 now fit)
  [56:52] rd        (5)  destination (GP or FP depending on opcode)
  [51:47] ra        (5)  source A
  [46:42] rb        (5)  source B
  [41:37] pred_reg  (5)  0-7 = P0-P7, 8-15 = !P0-!P7, 31 = none
  [36:33] pd        (4)  predicate destination (SETP/NOTP), P0-P15
  [32]    use_imm   (1)  1 = operand B is imm32
  [31:0]  imm32     (32) signed 32-bit immediate
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional


LANES = 32
NUM_GP = 32
NUM_FP = 32
NUM_PRED = 8


class Opcode(IntEnum):
    NOP      = 0x00
    HALT     = 0x01
    RET      = 0x02

    LDG      = 0x10
    STG      = 0x11
    LDGB     = 0x12
    LDGH     = 0x13
    LDS      = 0x14
    STS      = 0x15

    MOV      = 0x20
    MOVI     = 0x21
    IADD     = 0x22
    ISUB     = 0x23
    IMUL     = 0x24
    IAND     = 0x25
    IOR      = 0x26
    IXOR     = 0x27
    SHL      = 0x28
    SHR      = 0x29
    ISGT     = 0x2A
    ISLT     = 0x2B
    ISEQ     = 0x2C

    FADD     = 0x30
    FSUB     = 0x31
    FMUL     = 0x32
    FFMA     = 0x33
    FMIN     = 0x34
    FMAX     = 0x35
    I2F      = 0x36
    F2I      = 0x37

    SETP     = 0x40
    NOTP     = 0x41

    BRA      = 0x50
    BRA_DIV  = 0x51
    CALL     = 0x52
    JR       = 0x53

    VOTE     = 0x60
    SHFL     = 0x61
    LANEID   = 0x62

    TENSOR   = 0x70
    SYSCALL  = 0x7E
    BAR      = 0x7F


def encode(op, rd=0, ra=0, rb=0, pred_reg=31, pd=0, use_imm=False, imm=0):
    return (
        ((op & 0x7F) << 57)
        | ((rd & 0x1F) << 52)
        | ((ra & 0x1F) << 47)
        | ((rb & 0x1F) << 42)
        | ((pred_reg & 0x1F) << 37)
        | ((pd & 0x0F) << 33)
        | ((1 if use_imm else 0) << 32)
        | (imm & 0xFFFFFFFF)
    )


def sext32(v):
    return v - 0x100000000 if v & 0x80000000 else v


@dataclass
class Warp:
    warp_id: int
    gp: list = field(default_factory=lambda: [[0] * LANES for _ in range(NUM_GP)])
    fp: list = field(default_factory=lambda: [[0.0] * LANES for _ in range(NUM_FP)])
    pred: list = field(default_factory=lambda: [[False] * LANES for _ in range(NUM_PRED)])
    pc: int = 0
    active_mask: int = 0xFFFFFFFF
    call_stack: list = field(default_factory=list)
    reconvergence: list = field(default_factory=list)
    state: str = "READY"
    wait_reason: str = ""
    cycles_run: int = 0

    def get_gp(self, reg, lane):
        return 0 if reg == 0 else self.gp[reg][lane]

    def set_gp(self, reg, lane, val):
        if reg != 0:
            self.gp[reg][lane] = val & 0xFFFFFFFF

    def get_fp(self, reg, lane):
        return self.fp[reg][lane]

    def set_fp(self, reg, lane, val):
        self.fp[reg][lane] = val

    def get_pred(self, reg, lane):
        if reg < 8:
            return self.pred[reg][lane]
        return not self.pred[reg - 8][lane]

    def set_pred(self, reg, lane, val):
        self.pred[reg][lane] = bool(val)

    def active_lanes(self):
        return [i for i in range(LANES) if (self.active_mask >> i) & 1]


@dataclass
class TensorUnit:
    busy_cycles: int = 0
    results: list = field(default_factory=lambda: [0.0] * LANES)
    dest_fp: int = 0
    dest_warp: int = -1

    def is_busy(self):
        return self.busy_cycles > 0

    def tick(self):
        if self.busy_cycles > 0:
            self.busy_cycles -= 1
            return self.busy_cycles == 0
        return False


class SIMTCore:
    def __init__(self, core_id, num_warps=16, scratchpad_bytes=64 * 1024,
                 num_tensor_units=2, memory=None):
        from ram import Scratchpad, GlobalRAM
        self.core_id = core_id
        self.warps = [Warp(warp_id=i) for i in range(num_warps)]
        self.active_warps = []
        self.scratchpad = Scratchpad(scratchpad_bytes)
        self.tensor_units = [TensorUnit() for _ in range(num_tensor_units)]
        self.memory = memory if memory is not None else GlobalRAM()
        self.cycles = 0
        self.scheduler_rr = 0

    def launch_warp(self, warp_id, pc):
        w = self.warps[warp_id]
        w.pc = pc
        w.active_mask = 0xFFFFFFFF
        w.call_stack.clear()
        w.reconvergence.clear()
        for p in range(NUM_PRED):
            for l in range(LANES):
                w.pred[p][l] = False
        w.state = "READY"
        if warp_id not in self.active_warps:
            self.active_warps.append(warp_id)

    def retire_warp(self, warp_id):
        self.warps[warp_id].state = "DONE"
        if warp_id in self.active_warps:
            self.active_warps.remove(warp_id)

    def all_done(self):
        return len(self.active_warps) == 0

    def next_warp(self):
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

    def step(self):
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
                        dw.set_fp(tu.dest_fp, lane, tu.results[lane])
                    if dw.state == "WAITING" and dw.wait_reason == f"tensor{i}":
                        dw.state = "READY"
                        dw.wait_reason = ""

        if w.state == "RUNNING":
            w.state = "READY"
        return 1

    def _issue(self, w):
        if w.pc + 7 >= self.memory.size:
            raise RuntimeError(f"warp {w.warp_id}: PC out of bounds {w.pc:#x}")

        word = self.memory.read_u64(w.pc)
        w.pc += 8

        op       = (word >> 57) & 0x7F
        rd       = (word >> 52) & 0x1F
        ra       = (word >> 47) & 0x1F
        rb       = (word >> 42) & 0x1F
        pred_reg = (word >> 37) & 0x1F
        pd       = (word >> 33) & 0x0F
        use_imm  = (word >> 32) & 1
        imm      = sext32(word & 0xFFFFFFFF)

        try:
            opcode = Opcode(op)
        except ValueError:
            raise RuntimeError(
                f"warp {w.warp_id}: invalid opcode {op:#x} at pc={w.pc-8:#x}")

        lanes = w.active_lanes()
        if not lanes:
            return

        if pred_reg == 31:
            exec_lanes = lanes
        else:
            exec_lanes = [l for l in lanes if w.get_pred(pred_reg, l)]
            if not exec_lanes:
                return

        def b_val(l):
            return imm if use_imm else w.get_gp(rb, l)

        if opcode == Opcode.NOP:
            pass

        elif opcode == Opcode.HALT:
            self.retire_warp(w.warp_id)

        elif opcode == Opcode.RET:
            if w.call_stack:
                w.pc = w.call_stack.pop()
            else:
                self.retire_warp(w.warp_id)

        elif opcode == Opcode.LDG:
            for l in exec_lanes:
                w.set_gp(rd, l, self.memory.read_u32(w.get_gp(ra, l) + imm))
        elif opcode == Opcode.STG:
            for l in exec_lanes:
                self.memory.write_u32(w.get_gp(ra, l) + imm, w.get_gp(rd, l))
        elif opcode == Opcode.LDGB:
            for l in exec_lanes:
                w.set_gp(rd, l, self.memory.read_u8(w.get_gp(ra, l) + imm))
        elif opcode == Opcode.LDGH:
            for l in exec_lanes:
                w.set_gp(rd, l, self.memory.read_u16(w.get_gp(ra, l) + imm))
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
                w.set_gp(rd, l, w.get_gp(ra, l) + b_val(l))
        elif opcode == Opcode.ISUB:
            for l in exec_lanes:
                w.set_gp(rd, l, w.get_gp(ra, l) - b_val(l))
        elif opcode == Opcode.IMUL:
            for l in exec_lanes:
                w.set_gp(rd, l, w.get_gp(ra, l) * b_val(l))
        elif opcode == Opcode.IAND:
            for l in exec_lanes:
                w.set_gp(rd, l, w.get_gp(ra, l) & b_val(l))
        elif opcode == Opcode.IOR:
            for l in exec_lanes:
                w.set_gp(rd, l, w.get_gp(ra, l) | b_val(l))
        elif opcode == Opcode.IXOR:
            for l in exec_lanes:
                w.set_gp(rd, l, w.get_gp(ra, l) ^ b_val(l))
        elif opcode == Opcode.SHL:
            for l in exec_lanes:
                w.set_gp(rd, l, (w.get_gp(ra, l) << (b_val(l) & 31)) & 0xFFFFFFFF)
        elif opcode == Opcode.SHR:
            for l in exec_lanes:
                w.set_gp(rd, l, w.get_gp(ra, l) >> (b_val(l) & 31))
        elif opcode == Opcode.ISGT:
            for l in exec_lanes:
                w.set_gp(rd, l,
                         1 if (w.get_gp(ra, l) & 0xFFFFFFFF) > (b_val(l) & 0xFFFFFFFF) else 0)
        elif opcode == Opcode.ISLT:
            for l in exec_lanes:
                w.set_gp(rd, l,
                         1 if (w.get_gp(ra, l) & 0xFFFFFFFF) < (b_val(l) & 0xFFFFFFFF) else 0)
        elif opcode == Opcode.ISEQ:
            for l in exec_lanes:
                w.set_gp(rd, l,
                         1 if w.get_gp(ra, l) == b_val(l) else 0)

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
            mode = imm
            for l in exec_lanes:
                a = w.get_gp(ra, l)
                b = w.get_gp(rb, l)
                if mode == 0:
                    w.set_pred(pd, l, a < b)
                elif mode == 1:
                    w.set_pred(pd, l, a == b)
                elif mode == 2:
                    w.set_pred(pd, l, a != b)
                elif mode == 3:
                    w.set_pred(pd, l, (a & 0xFFFFFFFF) < (b & 0xFFFFFFFF))
                else:
                    w.set_pred(pd, l, False)

        elif opcode == Opcode.NOTP:
            for l in exec_lanes:
                w.set_pred(pd, l, not w.pred[ra][l])

        elif opcode == Opcode.BRA:
            if pred_reg == 31:
                w.pc = (w.pc + imm) & 0xFFFFFFFF
            else:
                if all(w.get_pred(pred_reg, l) for l in exec_lanes):
                    w.pc = (w.pc + imm) & 0xFFFFFFFF

        elif opcode == Opcode.BRA_DIV:
            if pred_reg == 31:
                w.pc = (w.pc + imm) & 0xFFFFFFFF
            else:
                taken = [l for l in exec_lanes if w.get_pred(pred_reg, l)]
                not_taken = [l for l in exec_lanes if l not in taken]
                if not taken:
                    pass
                elif not not_taken:
                    w.pc = (w.pc + imm) & 0xFFFFFFFF
                else:
                    w.reconvergence.append((w.pc, not_taken))
                    w.active_mask = sum(1 << l for l in taken)
                    w.pc = (w.pc + imm) & 0xFFFFFFFF

        elif opcode == Opcode.CALL:
            w.call_stack.append(w.pc)
            w.pc = (w.pc + imm) & 0xFFFFFFFF

        elif opcode == Opcode.JR:
            for l in exec_lanes:
                w.pc = w.get_gp(ra, l)
                break

        elif opcode == Opcode.VOTE:
            mask = 0
            for l in exec_lanes:
                if w.pred[0][l]:
                    mask |= (1 << l)
            mode = imm
            if mode == 0:
                for l in exec_lanes:
                    w.set_gp(rd, l, mask)
            elif mode == 1:
                v = 1 if mask else 0
                for l in exec_lanes:
                    w.set_gp(rd, l, v)
            elif mode == 2:
                v = 1 if mask == w.active_mask else 0
                for l in exec_lanes:
                    w.set_gp(rd, l, v)

        elif opcode == Opcode.SHFL:
            src = imm & 0x1F
            for l in exec_lanes:
                w.set_gp(rd, l, w.get_gp(ra, src))

        elif opcode == Opcode.LANEID:
            for l in exec_lanes:
                w.set_gp(rd, l, l)

        elif opcode == Opcode.TENSOR:
            unit = imm & 0x3
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
            for l in exec_lanes:
                tu.results[l] = (
                    w.get_fp(ra, l) * w.get_fp(rb, l) + w.get_fp(rd, l)
                )

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

    def run(self, max_cycles=10_000_000):
        while not self.all_done() and self.cycles < max_cycles:
            self.step()
        if self.cycles >= max_cycles:
            raise RuntimeError(f"max cycles ({max_cycles}) exceeded")
        return self.cycles