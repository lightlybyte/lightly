"""
assembler.py - Two-pass assembler for the Re:G ISA v2.

Fixes in this version:
  - SETP operands are now PABI (predicate dest, src A, src B, imm mode).
    The old PRRI format wrote ra/rb in the wrong order, so
    `SETP p0, r1, r13, 0` became `p0 = (r13 < r0)` instead of
    `p0 = (r1 < r13)`. That made every loop terminate immediately.
  - All other operand layouts unchanged.
"""

from __future__ import annotations
import argparse
import sys
from dataclasses import dataclass

from cpu import Opcode, encode


# ============================================================
# PARSED REPRESENTATION
# ============================================================

@dataclass(slots=True)
class ParsedLine:
    line_no: int
    label: str | None
    mnemonic: str | None
    operands: list[str]
    pred_reg: int = 31


@dataclass(slots=True)
class Instr:
    mnemonic: str
    operands: list[str]
    line_no: int
    pred_reg: int = 31
    label: str | None = None
    op: Opcode | None = None
    rd: int = 0
    ra: int = 0
    rb: int = 0
    pd: int = 0
    use_imm: bool = False
    imm: int = 0
    is_label_only: bool = False


# ============================================================
# PARSING
# ============================================================

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
    pred_reg = 31
    if line.startswith('@'):
        import re
        m = re.match(r'@(!?)P(\d+)\s+(.*)', line)
        if not m:
            raise SyntaxError(f"line {line_no}: bad predicate: {line}")
        neg = m.group(1) == '!'
        pidx = int(m.group(2))
        pred_reg = (pidx + 8) if neg else pidx
        line = m.group(3)
    parts = line.split(None, 1)
    mnemonic = parts[0]
    rest = parts[1] if len(parts) > 1 else ""
    operands = [op.strip() for op in rest.split(',')] if rest else []
    return ParsedLine(line_no, None, mnemonic, operands, pred_reg=pred_reg)


def reg_index(tok: str, prefix: str) -> int:
    if not tok or tok[0] != prefix:
        raise ValueError(f"expected {prefix}N, got {tok!r}")
    n = int(tok[1:])
    if not 0 <= n <= 31:
        raise ValueError(f"{prefix} register out of range: {tok}")
    return n


def imm_value(tok: str) -> int:
    return int(tok, 0)


# ============================================================
# INSTRUCTION FORMATS
# ============================================================
#
# Symbols:
#   R = GP register, filled in order rd -> ra -> rb
#   F = FP register, filled in order rd -> ra -> rb
#   A = explicitly ra (reg_index)
#   B = explicitly rb (reg_index)
#   P = predicate register; first P -> pd, second P -> ra
#   I = immediate 32 (sets use_imm)
#   O = register or immediate (rb or imm32)
#   L = label (PC-relative byte offset in imm32)
#
INSTR_FORMATS = {
    "NOP":    ("",     Opcode.NOP),
    "HALT":   ("",     Opcode.HALT),
    "RET":    ("",     Opcode.RET),

    "LDG":    ("RRI",  Opcode.LDG),
    "STG":    ("RRI",  Opcode.STG),
    "LDGB":   ("RRI",  Opcode.LDGB),
    "LDGH":   ("RRI",  Opcode.LDGH),
    "LDS":    ("RRI",  Opcode.LDS),
    "STS":    ("RRI",  Opcode.STS),

    "MOV":    ("RR",   Opcode.MOV),
    "MOVI":   ("RI",   Opcode.MOVI),
    "IADD":   ("RRO",  Opcode.IADD),
    "ISUB":   ("RRO",  Opcode.ISUB),
    "IMUL":   ("RRO",  Opcode.IMUL),
    "IAND":   ("RRO",  Opcode.IAND),
    "IOR":    ("RRO",  Opcode.IOR),
    "IXOR":   ("RRO",  Opcode.IXOR),
    "SHL":    ("RRO",  Opcode.SHL),
    "SHR":    ("RRO",  Opcode.SHR),
    "ISGT":   ("RRO",  Opcode.ISGT),
    "ISLT":   ("RRO",  Opcode.ISLT),
    "ISEQ":   ("RRO",  Opcode.ISEQ),

    "ADDI":   ("RRI",  Opcode.IADD),
    "SUBI":   ("RRI",  Opcode.ISUB),
    "MULI":   ("RRI",  Opcode.IMUL),
    "ANDI":   ("RRI",  Opcode.IAND),
    "ORI":    ("RRI",  Opcode.IOR),
    "XORI":   ("RRI",  Opcode.IXOR),
    "SHLI":   ("RRI",  Opcode.SHL),
    "SHRI":   ("RRI",  Opcode.SHR),

    "FADD":   ("FFF",  Opcode.FADD),
    "FSUB":   ("FFF",  Opcode.FSUB),
    "FMUL":   ("FFF",  Opcode.FMUL),
    "FFMA":   ("FFF",  Opcode.FFMA),
    "FMIN":   ("FFF",  Opcode.FMIN),
    "FMAX":   ("FFF",  Opcode.FMAX),
    "I2F":    ("FR",   Opcode.I2F),
    "F2I":    ("RF",   Opcode.F2I),

    # FIX: PABI so the two source regs land in ra and rb, not rd and ra
    "SETP":   ("PABI", Opcode.SETP),
    "NOTP":   ("PP",   Opcode.NOTP),

    "BRA":    ("L",    Opcode.BRA),
    "BRA_DIV":("L",    Opcode.BRA_DIV),
    "CALL":   ("L",    Opcode.CALL),
    "JR":     ("R",    Opcode.JR),

    "VOTE":   ("RI",   Opcode.VOTE),
    "SHFL":   ("RRI",  Opcode.SHFL),
    "LANEID": ("R",    Opcode.LANEID),

    "TENSOR": ("RRRI", Opcode.TENSOR),
    "SYSCALL":("I",    Opcode.SYSCALL),
    "BAR":    ("",     Opcode.BAR),
}


# ============================================================
# ASSEMBLER
# ============================================================

class Assembler:
    def __init__(self):
        self.labels = {}
        self.org = 0

    def assemble(self, source: str) -> bytes:
        instrs = self._parse_and_flatten(source)
        self._assign_labels(instrs)
        self._encode_all(instrs)
        return self._emit(instrs)

    def _parse_and_flatten(self, source):
        parsed = [parse_line(r, i) for i, r in enumerate(source.splitlines(), 1)]
        parsed = [p for p in parsed if p]
        instrs = []
        for p in parsed:
            if p.mnemonic is None:
                instrs.append(Instr(mnemonic="", operands=[], line_no=p.line_no,
                                    label=p.label, is_label_only=True))
            else:
                instrs.append(Instr(mnemonic=p.mnemonic, operands=p.operands,
                                    line_no=p.line_no, pred_reg=p.pred_reg,
                                    label=p.label))
        return instrs

    def _assign_labels(self, instrs):
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

    def _encode_all(self, instrs):
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

    def _encode_instruction(self, ins, pc):
        if ins.mnemonic not in INSTR_FORMATS:
            raise SyntaxError(
                f"line {ins.line_no}: unknown mnemonic {ins.mnemonic!r}")
        fmt, op = INSTR_FORMATS[ins.mnemonic]

        if len(ins.operands) != len(fmt):
            raise SyntaxError(
                f"line {ins.line_no}: {ins.mnemonic} expects {len(fmt)} "
                f"operands, got {len(ins.operands)}")

        ins.op = op
        rd = ra = rb = pd = 0
        use_imm = False
        imm = 0
        rd_seen = ra_seen = rb_seen = pd_seen = False

        for i, (kind, tok) in enumerate(zip(fmt, ins.operands)):
            try:
                if kind == 'R':
                    r = reg_index(tok, 'r')
                    if not rd_seen:
                        rd = r; rd_seen = True
                    elif not ra_seen:
                        ra = r; ra_seen = True
                    elif not rb_seen:
                        rb = r; rb_seen = True
                elif kind == 'F':
                    r = reg_index(tok, 'f')
                    if not rd_seen:
                        rd = r; rd_seen = True
                    elif not ra_seen:
                        ra = r; ra_seen = True
                    elif not rb_seen:
                        rb = r; rb_seen = True
                elif kind == 'A':
                    ra = reg_index(tok, 'r'); ra_seen = True
                elif kind == 'B':
                    rb = reg_index(tok, 'r'); rb_seen = True
                elif kind == 'P':
                    p = reg_index(tok, 'p') if tok.startswith('p') else int(tok)
                    if not pd_seen:
                        pd = p; pd_seen = True
                    else:
                        ra = p
                elif kind == 'I':
                    imm = imm_value(tok) & 0xFFFFFFFF
                    use_imm = True
                elif kind == 'O':
                    if tok.startswith('r'):
                        rb = reg_index(tok, 'r'); rb_seen = True
                    else:
                        imm = imm_value(tok) & 0xFFFFFFFF
                        use_imm = True
                elif kind == 'L':
                    if tok not in self.labels:
                        raise SyntaxError(f"undefined label {tok!r}")
                    target = self.labels[tok]
                    offset = target - (pc + 8)
                    imm = offset & 0xFFFFFFFF
                    use_imm = True
            except (ValueError, SyntaxError) as e:
                raise SyntaxError(
                    f"line {ins.line_no}: {ins.mnemonic} operand {i+1} "
                    f"({tok!r}) invalid: {e}") from e

        ins.rd, ins.ra, ins.rb, ins.pd = rd, ra, rb, pd
        ins.use_imm = use_imm
        ins.imm = imm

    def _emit(self, instrs):
        out = bytearray()
        for ins in instrs:
            if ins.is_label_only:
                continue
            if ins.mnemonic == ".org":
                target = imm_value(ins.operands[0])
                while len(out) < target:
                    out.append(0)
                self.org = target
            elif ins.mnemonic == ".word":
                out += ins.imm.to_bytes(8, "little")
            elif ins.mnemonic == ".string":
                s = ins.operands[0].encode()
                out += len(s).to_bytes(4, "little") + s
            elif ins.mnemonic == ".fill":
                out += b"\x00" * (8 * ins.imm)
            elif ins.mnemonic and not ins.mnemonic.startswith('.'):
                word = encode(
                    op=ins.op, rd=ins.rd, ra=ins.ra, rb=ins.rb,
                    pred_reg=ins.pred_reg, pd=ins.pd,
                    use_imm=ins.use_imm, imm=ins.imm,
                )
                out += word.to_bytes(8, "little")
        return bytes(out)


def assemble(source: str) -> bytes:
    return Assembler().assemble(source)


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

    print(f"code size: {len(code)} bytes")
    print(f"origin: {hex(origin)}")
    print(f"first word loaded at origin: {hex(mem.global_ram.read_u64(origin))}")

    fence = sched.new_fence()
    sched.submit_command([1, (1 << 16) | 1, (1 << 16) | 1, 1, origin, 0, fence])
    cycles = sched.run(max_cycles=max_cycles)

    print(f"executed {cycles} cycles")
    print(f"fence {fence} signalled: {sched.is_fence_signalled(fence)}")
    print(f"warp 0 state: {cores[0].warps[0].state}")
    print(f"warp 0 pc:    {hex(cores[0].warps[0].pc)}")

    fb = mem.framebuffer
    print(f"fb.base: {hex(fb.base)}")
    print(f"pixel(0,0)       = {hex(fb.read_pixel(0, 0))}")
    print(f"pixel(160,120)   = {hex(fb.read_pixel(160, 120))}")
    print(f"pixel(319,239)   = {hex(fb.read_pixel(319, 239))}")

    return mem


# ============================================================
# CLI
# ============================================================

def main(argv=None):
    ap = argparse.ArgumentParser(description="Re:G assembler v2")
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--fb", default="320x240")
    ap.add_argument("--cycles", type=int, default=10_000_000)
    ap.add_argument("--origin", type=lambda s: int(s, 0), default=0x1000)
    ap.add_argument("--screen", action="store_true")
    ap.add_argument("--seconds", type=float, default=5.0)
    args = ap.parse_args(argv)

    try:
        src = open(args.input, "r", encoding="utf-8").read()
    except OSError as e:
        print(f"error: cannot read {args.input}: {e}", file=sys.stderr)
        return 1

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