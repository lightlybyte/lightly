"""
assembler.py - Two-pass assembler for the Re:G SIMT ISA, with runner.

CLI:
  python assembler.py kernel.s
  python assembler.py kernel.s --run
  python assembler.py kernel.s --run --fb 640x480
"""

from __future__ import annotations
import argparse
import sys
from dataclasses import dataclass

from cpu import Opcode, encode


@dataclass(slots=True)
class ParsedLine:
    line_no: int
    label: str | None
    mnemonic: str | None
    operands: list[str]
    pred: int = 0xFF
    pred_neg: bool = False


def strip_comment(line):
    in_str = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_str = not in_str
        elif ch == ';' and not in_str:
            return line[:i]
    return line


def parse_line(line, line_no):
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


def reg_index(tok, prefix):
    if not tok or tok[0] != prefix:
        raise ValueError(f"expected {prefix}N, got {tok!r}")
    n = int(tok[1:])
    if not 0 <= n <= 31:
        raise ValueError(f"{prefix} register out of range: {tok}")
    return n


def imm_value(tok):
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


class Assembler:
    def __init__(self):
        self.labels = {}
        self.pc = 0
        self.org = 0

    def assemble(self, source):
        parsed = [parse_line(r, i) for i, r in enumerate(source.splitlines(), 1)]
        parsed = [p for p in parsed if p]

        self.pc = 0
        for p in parsed:
            if p.label:
                self.labels[p.label] = self.pc
            if p.mnemonic == ".org":
                self.pc = imm_value(p.operands[0])
            elif p.mnemonic == ".label":
                self.labels[p.operands[0]] = self.pc
            elif p.mnemonic == ".word":
                self.pc += 8
            elif p.mnemonic == ".string":
                s = p.operands[0].strip('"')
                self.pc += 4 + len(s.encode())
            elif p.mnemonic == ".fill":
                self.pc += 8 * imm_value(p.operands[0])
            elif p.mnemonic and not p.mnemonic.startswith('.'):
                self.pc += 8

        out = bytearray()
        self.pc = 0
        for p in parsed:
            if p.mnemonic == ".org":
                target = imm_value(p.operands[0])
                while len(out) < target:
                    out.append(0)
                self.pc = target
            elif p.mnemonic == ".word":
                out += imm_value(p.operands[0]).to_bytes(8, "little")
                self.pc += 8
            elif p.mnemonic == ".string":
                s = p.operands[0].strip('"').encode()
                out += len(s).to_bytes(4, "little") + s
                self.pc += 4 + len(s)
            elif p.mnemonic == ".fill":
                n = imm_value(p.operands[0])
                out += b"\x00" * (8 * n)
                self.pc += 8 * n
            elif p.mnemonic and not p.mnemonic.startswith('.'):
                out += self._encode(p).to_bytes(8, "little")
                self.pc += 8
        return bytes(out)

    def _encode(self, p):
        if p.mnemonic not in INSTR_FORMATS:
            raise SyntaxError(f"line {p.line_no}: unknown mnemonic {p.mnemonic}")
        op, fmt = INSTR_FORMATS[p.mnemonic]
        if len(p.operands) != len(fmt):
            raise SyntaxError(
                f"line {p.line_no}: {p.mnemonic} expects {len(fmt)} operands")
        rd = ra = rb = rc = pd = 0
        imm = 0
        for i, (kind, tok) in enumerate(zip(fmt, p.operands)):
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
                    raise SyntaxError(
                        f"line {p.line_no}: undefined label {tok}")
                target = self.labels[tok]
                word_pc = (self.pc - self.org) // 8
                target_words = (target - self.org) // 8
                imm = (target_words - word_pc) & 0x7FFF
        return encode(op, rd, ra, rb, rc, pd,
                      pred=(p.pred != 0xFF), pred_neg=p.pred_neg, imm=imm)


def assemble(source: str) -> bytes:
    return Assembler().assemble(source)


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
    print(f"framebuffer checksum: {sum(mem.framebuffer.buffer) & 0xFFFFFFFF}")
    return mem


def main(argv=None):
    ap = argparse.ArgumentParser(description="Re:G assembler")
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--fb", default="320x240")
    ap.add_argument("--cycles", type=int, default=10_000_000)
    ap.add_argument("--origin", type=lambda s: int(s, 0), default=0x1000)
    ap.add_argument("--screen", action="store_true",
                    help="show the framebuffer in a non-blocking window")
    ap.add_argument("--seconds", type=float, default=3.0,
                    help="seconds to keep the window open")
    args = ap.parse_args(argv)

    src = open(args.input, "r", encoding="utf-8").read()
    try:
        code = assemble(src)
    except SyntaxError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    out_path = args.output or (args.input.rsplit('.', 1)[0] + ".bin")
    with open(out_path, "wb") as f:
        f.write(code)
    print(f"assembled {len(code)} bytes -> {out_path}")

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
                print("warning: tkinter unavailable; skipping window",
                      file=sys.stderr)
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