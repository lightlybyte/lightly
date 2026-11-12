"""
assembler.py - Two-pass assembler for the Re:G SIMT ISA, with optimizer.

CLI:
  python assembler.py kernel.s
  python assembler.py kernel.s --run --screen
"""

from __future__ import annotations
import argparse
import sys
from dataclasses import dataclass, replace

from cpu import Opcode, encode


@dataclass(slots=True)
class ParsedLine:
    line_no: int
    label: str | None
    mnemonic: str | None
    operands: list[str]
    pred: int = 0xFF
    pred_neg: bool = False


@dataclass(slots=True)
class Instr:
    mnemonic: str
    operands: list[str]
    line_no: int
    pred: int = 0xFF
    pred_neg: bool = False
    label: str | None = None
    rd: int = 0
    ra: int = 0
    rb: int = 0
    rc: int = 0
    pd: int = 0
    imm: int = 0
    op: Opcode | None = None
    is_label_only: bool = False


def strip_comment(line: str) -> str:
    in_str = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_str = not in_str
        elif ch == ';' and not in_str:
            return line[:i]
    return line


def parse_line(line: str, line_no: int) -> ParsedLine | None:
    line = strip_comment(line).strip()
    if not line:
        return None
    if line.endswith(':') and ' ' not in line and ',' not in line:
        return ParsedLine(line_no, line[:-1], None, [])
    pred = 0xFF
    pred_neg = False
    if line.startswith('@'):
        import re
        m = re.match(r'@(!?)P(\d+)\s+(.*)', line)
        if not m:
            raise SyntaxError(f"line {line_no}: bad predicate: {line}")
        pred_neg = m.group(1) == '!'
        pred = int(m.group(2))
        line = m.group(3)
    parts = line.split(None, 1)
    mnemonic = parts[0]
    rest = parts[1] if len(parts) > 1 else ""
    operands = [op.strip() for op in rest.split(',')] if rest else []
    return ParsedLine(line_no, None, mnemonic, operands,
                      pred=pred, pred_neg=pred_neg)


def reg_index(tok: str, prefix: str) -> int:
    if not tok or tok[0] != prefix:
        raise ValueError(f"expected {prefix}N, got {tok!r}")
    n = int(tok[1:])
    if not 0 <= n <= 31:
        raise ValueError(f"{prefix} register out of range: {tok}")
    return n


def imm_value(tok: str) -> int:
    return int(tok, 0)


INSTR_FORMATS = {
    "NOP":    (Opcode.NOP,    ""),
    "HALT":   (Opcode.HALT,   ""),
    "LDG":    (Opcode.LDG,    "RRI"),
    "STG":    (Opcode.STG,    "RRI"),
    "LDS":    (Opcode.LDS,    "RRI"),
    "STS":    (Opcode.STS,    "RRI"),
    "MOV":    (Opcode.MOV,    "RR"),
    "MOVI":   (Opcode.MOVI,   "RI"),
    "IADD":   (Opcode.IADD,   "RRR"),
    "ISUB":   (Opcode.ISUB,   "RRR"),
    "IMUL":   (Opcode.IMUL,   "RRR"),
    "IAND":   (Opcode.IAND,   "RRR"),
    "IOR":    (Opcode.IOR,    "RRR"),
    "IXOR":   (Opcode.IXOR,   "RRR"),
    "SHL":    (Opcode.SHL,    "RRR"),
    "SHR":    (Opcode.SHR,    "RRR"),
    "SHLI":   (Opcode.SHL,    "RRI"),   # NEW: shift left by immediate
    "SHRI":   (Opcode.SHR,    "RRI"),   # NEW: shift right by immediate
    "FADD":   (Opcode.FADD,   "RRR"),
    "FSUB":   (Opcode.FSUB,   "RRR"),
    "FMUL":   (Opcode.FMUL,   "RRR"),
    "FFMA":   (Opcode.FFMA,   "RRR"),
    "FMIN":   (Opcode.FMIN,   "RRR"),
    "FMAX":   (Opcode.FMAX,   "RRR"),
    "I2F":    (Opcode.I2F,    "RR"),
    "F2I":    (Opcode.F2I,    "RR"),
    "SETP":   (Opcode.SETP,   "PRRI"),
    "BRA":    (Opcode.BRA,    "L"),
    "BRA_DIV":(Opcode.BRA_DIV,"L"),
    "RET":    (Opcode.RET,    ""),
    "VOTE":   (Opcode.VOTE,   "RI"),
    "SHFL":   (Opcode.SHFL,   "RRI"),
    "TENSOR": (Opcode.TENSOR, "RRRI"),
    "BAR":    (Opcode.BAR,    ""),
    "SYSCALL":(Opcode.SYSCALL,"RI"),
}

BRANCHES = {"BRA", "BRA_DIV"}
TERMINATORS = {"HALT", "RET"}
INT_ALU = {"IADD", "ISUB", "IMUL", "IAND", "IOR", "IXOR", "SHL", "SHR"}


class Assembler:
    def __init__(self, optimize: bool = True):
        self.labels: dict[str, int] = {}
        self.pc = 0
        self.org = 0
        self.optimize = optimize

    def assemble(self, source: str) -> bytes:
        instrs = self._parse_and_flatten(source)
        if self.optimize:
            instrs = optimize_program(instrs)
        self._assign_labels(instrs)
        self._encode_all(instrs)
        return self._emit(instrs)

    def _parse_and_flatten(self, source: str) -> list[Instr]:
        parsed = [parse_line(r, i) for i, r in enumerate(source.splitlines(), 1)]
        parsed = [p for p in parsed if p]
        instrs: list[Instr] = []
        for p in parsed:
            if p.mnemonic is None:
                instrs.append(Instr(
                    mnemonic="", operands=[], line_no=p.line_no,
                    label=p.label, is_label_only=True))
                continue
            instrs.append(Instr(
                mnemonic=p.mnemonic, operands=p.operands,
                line_no=p.line_no, pred=p.pred,
                pred_neg=p.pred_neg, label=p.label))
        return instrs

    def _assign_labels(self, instrs: list[Instr]) -> None:
        self.labels = {}
        pc = self.org
        for ins in instrs:
            if ins.label:
                self.labels[ins.label] = pc
            if ins.mnemonic == ".org":
                pc = imm_value(ins.operands[0])
                self.org = pc
            elif ins.mnemonic == ".word":
                pc += 8
            elif ins.mnemonic == ".string":
                s = ins.operands[0].strip('"')
                pc += 4 + len(s.encode())
            elif ins.mnemonic == ".fill":
                pc += 8 * imm_value(ins.operands[0])
            elif ins.mnemonic and not ins.mnemonic.startswith('.'):
                pc += 8

    def _encode_all(self, instrs: list[Instr]) -> None:
        pc = self.org
        for ins in instrs:
            if ins.is_label_only:
                continue
            if ins.mnemonic == ".org":
                pc = imm_value(ins.operands[0])
                self.org = pc
            elif ins.mnemonic == ".word":
                ins.imm = imm_value(ins.operands[0]) & 0xFFFFFFFFFFFFFFFF
                pc += 8
            elif ins.mnemonic == ".string":
                ins.operands[0] = ins.operands[0].strip('"')
                pc += 4 + len(ins.operands[0].encode())
            elif ins.mnemonic == ".fill":
                ins.imm = imm_value(ins.operands[0])
                pc += 8 * ins.imm
            elif ins.mnemonic and not ins.mnemonic.startswith('.'):
                self._encode_instruction(ins, pc)
                pc += 8

    def _encode_instruction(self, ins: Instr, pc: int) -> None:
        if ins.mnemonic not in INSTR_FORMATS:
            raise SyntaxError(
                f"line {ins.line_no}: unknown mnemonic {ins.mnemonic!r}")
        op, fmt = INSTR_FORMATS[ins.mnemonic]
        if len(ins.operands) != len(fmt):
            raise SyntaxError(
                f"line {ins.line_no}: {ins.mnemonic} expects {len(fmt)} "
                f"operands, got {len(ins.operands)}")
        ins.op = op
        rd = ra = rb = rc = pd = 0
        imm = 0
        for i, (kind, tok) in enumerate(zip(fmt, ins.operands)):
            try:
                if kind == 'R':
                    reg = reg_index(tok, 'r')
                    if i == 0: rd = reg
                    elif i == 1: ra = reg
                    elif i == 2: rb = reg
                    elif i == 3: rc = reg
                elif kind == 'P':
                    pd = reg_index(tok, 'p') if tok.startswith('p') else int(tok)
                elif kind == 'I':
                    imm = imm_value(tok) & 0x7FFF
                elif kind == 'L':
                    if tok not in self.labels:
                        raise SyntaxError(f"undefined label {tok!r}")
                    target = self.labels[tok]
                    word_pc = (pc - self.org) // 8
                    target_words = (target - self.org) // 8
                    imm = (target_words - word_pc) & 0x7FFF
            except (ValueError, SyntaxError) as e:
                raise SyntaxError(
                    f"line {ins.line_no}: {ins.mnemonic} operand {i+1} "
                    f"({tok!r}) invalid: {e}") from e
        ins.rd, ins.ra, ins.rb, ins.rc, ins.pd = rd, ra, rb, rc, pd
        ins.imm = imm

    def _emit(self, instrs: list[Instr]) -> bytes:
        out = bytearray()
        pc = self.org
        for ins in instrs:
            if ins.is_label_only:
                continue
            if ins.mnemonic == ".org":
                target = imm_value(ins.operands[0])
                while len(out) < target:
                    out.append(0)
                pc = target
                self.org = target
            elif ins.mnemonic == ".word":
                out += ins.imm.to_bytes(8, "little")
                pc += 8
            elif ins.mnemonic == ".string":
                s = ins.operands[0].encode()
                out += len(s).to_bytes(4, "little") + s
                pc += 4 + len(s)
            elif ins.mnemonic == ".fill":
                out += b"\x00" * (8 * ins.imm)
                pc += 8 * ins.imm
            elif ins.mnemonic and not ins.mnemonic.startswith('.'):
                word = encode(
                    ins.op, ins.rd, ins.ra, ins.rb, ins.rc, ins.pd,
                    pred=(ins.pred != 0xFF), pred_neg=ins.pred_neg,
                    imm=ins.imm,
                )
                out += word.to_bytes(8, "little")
                pc += 8
        return bytes(out)


# ============================================================
# OPTIMIZER
# ============================================================

def optimize_program(instrs: list[Instr]) -> list[Instr]:
    changed = True
    while changed:
        before = len(instrs)
        instrs = _eliminate_unreachable(instrs)
        instrs = _eliminate_mov_self(instrs)
        instrs = _eliminate_branch_to_next(instrs)
        instrs = _eliminate_nop(instrs)
        changed = len(instrs) != before
    return instrs


def _eliminate_unreachable(instrs: list[Instr]) -> list[Instr]:
    targets = set()
    for ins in instrs:
        if ins.mnemonic in BRANCHES and ins.operands:
            targets.add(ins.operands[0])
    out = []
    reachable = True
    for ins in instrs:
        if ins.is_label_only:
            if ins.label in targets:
                reachable = True
            out.append(ins)
            continue
        if ins.label in targets:
            reachable = True
        if not reachable:
            continue
        out.append(ins)
        if ins.mnemonic in TERMINATORS or ins.mnemonic in BRANCHES:
            reachable = False
    return out


def _eliminate_mov_self(instrs: list[Instr]) -> list[Instr]:
    return [
        ins for ins in instrs
        if not (ins.mnemonic == "MOV" and len(ins.operands) == 2
                and ins.operands[0] == ins.operands[1])
    ]


def _eliminate_branch_to_next(instrs: list[Instr]) -> list[Instr]:
    out = []
    for i, ins in enumerate(instrs):
        if ins.mnemonic in BRANCHES and ins.operands:
            target = ins.operands[0]
            j = i + 1
            skip = False
            while j < len(instrs):
                if instrs[j].label == target:
                    skip = True
                    break
                if instrs[j].mnemonic not in (".org", ".word", ".fill"):
                    break
                j += 1
            if skip:
                continue
        out.append(ins)
    return out


def _eliminate_nop(instrs: list[Instr]) -> list[Instr]:
    return [ins for ins in instrs if not (ins.mnemonic == "NOP" and not ins.label)]


# ============================================================
# PUBLIC
# ============================================================

def assemble(source: str, optimize: bool = True) -> bytes:
    return Assembler(optimize=optimize).assemble(source)


# ============================================================
# RUNNER
# ============================================================

def run_program(code, fb_w, fb_h, origin=0x1000, max_cycles=10_000_000):
    from ram import MemorySystem
    from cpu import SIMTCore
    from dma import DMAEngine
    from scheduler import Scheduler

    mem = MemorySystem(num_cores=1, fb_width=fb_w, fb_height=fb_h)
    cores = [SIMTCore(core_id=0, num_warps=32, memory=mem.global_ram)]
    dma = DMAEngine(mem.global_ram, mem.scratchpads, mem.framebuffer)
    sched = Scheduler(cores, mem, dma, ring_base=0x100)
    mem.global_ram.load(code, origin)

    fence = sched.new_fence()
    sched.submit_command([1, 1, 1, 1, origin, 0, fence])
    cycles = sched.run(max_cycles=max_cycles)
    print(f"executed {cycles} cycles")
    print(f"fence {fence} signalled: {sched.is_fence_signalled(fence)}")
    return mem


# ============================================================
# CLI
# ============================================================

def main(argv=None):
    ap = argparse.ArgumentParser(description="Re:G assembler")
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--fb", default="320x240")
    ap.add_argument("--cycles", type=int, default=10_000_000)
    ap.add_argument("--origin", type=lambda s: int(s, 0), default=0x1000)
    ap.add_argument("--screen", action="store_true")
    ap.add_argument("--seconds", type=float, default=3.0)
    ap.add_argument("--no-opt", action="store_true")
    args = ap.parse_args(argv)

    optimize = not args.no_opt

    try:
        src = open(args.input, "r", encoding="utf-8").read()
    except OSError as e:
        print(f"error: cannot read {args.input}: {e}", file=sys.stderr)
        return 1

    try:
        code = assemble(src, optimize=optimize)
    except SyntaxError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    out_path = args.output or (args.input.rsplit('.', 1)[0] + ".bin")
    with open(out_path, "wb") as f:
        f.write(code)
    print(f"assembled {len(code)} bytes "
          f"({'optimized' if optimize else 'unoptimized'}) -> {out_path}")

    if args.run or args.screen:
        try:
            w, h = args.fb.lower().split('x')
            fb_w, fb_h = int(w), int(h)
        except ValueError:
            print(f"error: bad --fb {args.fb!r}", file=sys.stderr)
            return 1
        mem = run_program(code, fb_w, fb_h, origin=args.origin,
                          max_cycles=args.cycles)
        if args.screen:
            try:
                from screen import Screen
            except ImportError:
                print("warning: tkinter unavailable", file=sys.stderr)
                return 0
            import time
            screen = Screen(mem.framebuffer, title="Re:G")
            end = time.perf_counter() + args.seconds
            while screen.running and time.perf_counter() < end:
                screen.pump()
                time.sleep(1 / 60)
            screen.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())