"""
assembler.py - Two-pass assembler for the Re:G SIMT ISA, with optimizer.

CLI:
  python assembler.py kernel.s
  python assembler.py kernel.s --run
  python assembler.py kernel.s --run --fb 640x480
  python assembler.py kernel.s --run --screen --seconds 3
  python assembler.py kernel.s --no-opt
  python assembler.py kernel.s -O2

Optimizer passes (run in order):
  1. dead-code elimination        (remove unreachable instructions)
  2. mov-elimination              (MOV rd, rd)
  3. constant folding             (MOVI r, a; MOVI s, b; OP -> folded)
  4. redundant-load elimination   (consecutive LDG/STG to same addr)
  5. branch-to-next elimination   (BRA to the immediately following label)
  6. peephole: NOP removal
"""

from __future__ import annotations
import argparse
import sys
from dataclasses import dataclass, field, replace

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
    pred: int = 0xFF
    pred_neg: bool = False


@dataclass(slots=True)
class Instr:
    """An encoded instruction with metadata for optimization."""
    mnemonic: str
    operands: list[str]
    line_no: int
    pred: int = 0xFF
    pred_neg: bool = False
    label: str | None = None
    # filled during encoding
    rd: int = 0
    ra: int = 0
    rb: int = 0
    rc: int = 0
    pd: int = 0
    imm: int = 0
    op: Opcode | None = None
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


# ============================================================
# INSTRUCTION FORMATS
# ============================================================

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

BRANCHES = {"BRA", "BRA_DIV"}
TERMINATORS = {"HALT", "RET"}
INT_ALU = {"IADD", "ISUB", "IMUL", "IAND", "IOR", "IXOR", "SHL", "SHR"}
FP_ALU = {"FADD", "FSUB", "FMUL", "FFMA", "FMIN", "FMAX"}


# ============================================================
# ASSEMBLER
# ============================================================

class Assembler:
    def __init__(self, optimize: bool = True):
        self.labels: dict[str, int] = {}
        self.pc = 0
        self.org = 0
        self.optimize = optimize

    # ---------- main entry ----------

    def assemble(self, source: str) -> bytes:
        instrs = self._parse_and_flatten(source)
        if self.optimize:
            instrs = optimize_program(instrs)
        self._assign_labels(instrs)
        self._encode_all(instrs)
        return self._emit(instrs)

    # ---------- pass: parse ----------

    def _parse_and_flatten(self, source: str) -> list[Instr]:
        parsed = [parse_line(r, i) for i, r in enumerate(source.splitlines(), 1)]
        parsed = [p for p in parsed if p]

        instrs: list[Instr] = []
        for p in parsed:
            if p.mnemonic is None:
                # label-only line
                instrs.append(Instr(
                    mnemonic="", operands=[], line_no=p.line_no,
                    label=p.label, is_label_only=True))
                continue
            instrs.append(Instr(
                mnemonic=p.mnemonic, operands=p.operands,
                line_no=p.line_no, pred=p.pred,
                pred_neg=p.pred_neg, label=p.label))
        return instrs

    # ---------- pass: assign labels ----------

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
            elif ins.is_label_only:
                # label with no following instruction still advances nothing
                pass

    # ---------- pass: encode ----------

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

    # ---------- pass: emit ----------

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
    """Run all optimizer passes until fixpoint."""
    changed = True
    while changed:
        before = len(instrs)
        instrs = _eliminate_unreachable(instrs)
        instrs = _eliminate_mov_self(instrs)
        instrs = _eliminate_branch_to_next(instrs)
        instrs = _eliminate_nop(instrs)
        instrs = _fold_constants(instrs)
        changed = len(instrs) != before
    return instrs


def _has_label(ins: Instr) -> bool:
    return bool(ins.label)


def _eliminate_unreachable(instrs: list[Instr]) -> list[Instr]:
    """
    Remove instructions that are unreachable via fallthrough or branch.
    Any instruction immediately after HALT/RET/BRA is unreachable unless
    it carries a label that's a branch target.
    """
    # find all branch targets (by label)
    targets: set[str] = set()
    for ins in instrs:
        if ins.mnemonic in BRANCHES and ins.operands:
            targets.add(ins.operands[0])

    out: list[Instr] = []
    reachable = True
    for ins in instrs:
        if ins.is_label_only:
            # label-only line: reachable if anything following it is reachable,
            # but also reachable if it's a branch target.
            if ins.label in targets:
                reachable = True
            out.append(ins)
            continue

        if ins.label in targets:
            reachable = True

        if not reachable:
            # skip this instruction (unreachable)
            continue

        out.append(ins)

        if ins.mnemonic in TERMINATORS or ins.mnemonic in BRANCHES:
            # after these, fallthrough is unreachable
            reachable = False

    return out


def _eliminate_mov_self(instrs: list[Instr]) -> list[Instr]:
    """Drop `MOV rN, rN` — it's a no-op."""
    return [
        ins for ins in instrs
        if not (ins.mnemonic == "MOV" and len(ins.operands) == 2
                and ins.operands[0] == ins.operands[1])
    ]


def _eliminate_branch_to_next(instrs: list[Instr]) -> list[Instr]:
    """Drop `BRA label` when the next labeled instruction follows immediately."""
    out: list[Instr] = []
    for i, ins in enumerate(instrs):
        if ins.mnemonic in BRANCHES and ins.operands:
            target = ins.operands[0]
            # scan forward for the target label
            j = i + 1
            skip = False
            while j < len(instrs):
                if instrs[j].is_label_only and instrs[j].label == target:
                    skip = True
                    break
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
    """Drop NOP unless it carries a label (needed as branch target)."""
    out: list[Instr] = []
    for ins in instrs:
        if ins.mnemonic == "NOP" and not ins.label:
            continue
        out.append(ins)
    return out


def _fold_constants(instrs: list[Instr]) -> list[Instr]:
    """
    Peephole: MOVI rA, immA; MOVI rB, immB; OP rD, rA, rB
    -> MOVI rD, result

    Only folds when:
      - pred is off (unconditional)
      - OP is an integer ALU op
      - both operands were loaded as immediates and are not clobbered
    """
    out: list[Instr] = []
    i = 0
    n = len(instrs)
    # map from reg -> (immediate, instr-index-that-defined-it)
    const_of: dict[int, int] = {}
    const_def: dict[int, int] = {}

    def clobbers(ins: Instr, reg: int) -> bool:
        """True if ins writes to register `reg`."""
        if ins.mnemonic in ("MOV", "MOVI", "IADD", "ISUB", "IMUL",
                            "IAND", "IOR", "IXOR", "SHL", "SHR",
                            "FADD", "FSUB", "FMUL", "FFMA", "FMIN",
                            "FMAX", "I2F", "F2I"):
            if ins.operands:
                try:
                    if reg_index(ins.operands[0], 'r') == reg:
                        return True
                except ValueError:
                    pass
        return False

    while i < n:
        ins = instrs[i]
        # only fold unconditional instructions
        if ins.mnemonic == "MOVI" and ins.pred == 0xFF:
            try:
                rd = reg_index(ins.operands[0], 'r')
                imm = imm_value(ins.operands[1]) & 0xFFFFFFFF
                const_of[rd] = imm
                const_def[rd] = i
                out.append(ins)
                i += 1
                continue
            except (ValueError, IndexError):
                pass

        if ins.mnemonic in INT_ALU and ins.pred == 0xFF:
            try:
                rd = reg_index(ins.operands[0], 'r')
                ra = reg_index(ins.operands[1], 'r')
                rb = reg_index(ins.operands[2], 'r')
                if ra in const_of and rb in const_of:
                    a = const_of[ra]
                    b = const_of[rb]
                    if ins.mnemonic == "IADD":
                        result = (a + b) & 0xFFFFFFFF
                    elif ins.mnemonic == "ISUB":
                        result = (a - b) & 0xFFFFFFFF
                    elif ins.mnemonic == "IMUL":
                        result = (a * b) & 0xFFFFFFFF
                    elif ins.mnemonic == "IAND":
                        result = a & b
                    elif ins.mnemonic == "IOR":
                        result = a | b
                    elif ins.mnemonic == "IXOR":
                        result = a ^ b
                    elif ins.mnemonic == "SHL":
                        result = (a << (b & 31)) & 0xFFFFFFFF
                    elif ins.mnemonic == "SHR":
                        result = a >> (b & 31)
                    else:
                        out.append(ins)
                        i += 1
                        continue

                    folded = replace(
                        ins,
                        mnemonic="MOVI",
                        operands=[ins.operands[0], str(result)],
                        imm=result & 0x7FFF,
                    )
                    const_of[rd] = result
                    const_def[rd] = len(out)
                    out.append(folded)
                    i += 1
                    continue
            except (ValueError, IndexError):
                pass

        # any other instruction: invalidate constants it clobbers
        for reg in list(const_of.keys()):
            if clobbers(ins, reg):
                del const_of[reg]
                const_def.pop(reg, None)
        out.append(ins)
        i += 1

    return out


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
    print(f"framebuffer checksum: {sum(mem.framebuffer.buffer) & 0xFFFFFFFF}")
    return mem


# ============================================================
# CLI
# ============================================================

def main(argv=None):
    ap = argparse.ArgumentParser(description="Re:G assembler")
    ap.add_argument("input", help="assembly source file")
    ap.add_argument("-o", "--output", help="output binary (default: <input>.bin)")
    ap.add_argument("--run", action="store_true",
                    help="assemble then run on the CPU")
    ap.add_argument("--fb", default="320x240",
                    help="framebuffer size WxH (default 320x240)")
    ap.add_argument("--cycles", type=int, default=10_000_000,
                    help="max cycles for --run")
    ap.add_argument("--origin", type=lambda s: int(s, 0), default=0x1000,
                    help="load address (default 0x1000)")
    ap.add_argument("--screen", action="store_true",
                    help="show the framebuffer in a non-blocking window")
    ap.add_argument("--seconds", type=float, default=3.0,
                    help="seconds to keep the window open")
    ap.add_argument("--no-opt", action="store_true",
                    help="disable the optimizer")
    ap.add_argument("-O", type=int, default=1, dest="opt_level",
                    help="optimization level (0 = off, 1+ = on)")
    args = ap.parse_args(argv)

    optimize = (not args.no_opt) and args.opt_level > 0

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
            print(f"error: bad --fb {args.fb!r} (expected WxH)",
                  file=sys.stderr)
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