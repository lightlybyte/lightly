"""
assembler.py - Two-pass assembler for the Re:G SIMT ISA.

Syntax:
  [@P[!]N]  MNEMONIC  operands...  [; comment]

Operands:
  rN     general-purpose register (0..31)
  fN     floating-point register (0..31)
  pN     predicate register (0..7)
  imm    integer literal (decimal, 0x hex)
  label  target for branch instructions

Predication:
  @P0    execute if predicate 0 is true
  @!P0   execute if predicate 0 is false

Directives:
  .org ADDR       set assembly origin
  .label NAME     define a label at the current PC
  .word VALUE     emit a raw 64-bit word
  .string "..."   emit a length-prefixed UTF-8 string
  .fill N         emit N zero words
"""

from __future__ import annotations
import re
from dataclasses import dataclass
from cpu import Opcode, encode


# ============================================================
# TOKENIZER / PARSER
# ============================================================

@dataclass(slots=True)
class ParsedLine:
    line_no: int
    label: str | None
    mnemonic: str | None
    operands: list[str]
    pred: int = 0xFF
    pred_neg: bool = False


TOKEN_RE = re.compile(
    r"""
    (?P<label>^\w+:)
    |
    (?P<pred>@!?P\d+)
    |
    (?P<directive>\.\w+)
    |
    (?P<mnemonic>[A-Za-z_]\w*)
    |
    (?P<reg>[rfp]\d+)
    |
    (?P<imm>-?(?:0x[0-9A-Fa-f]+|\d+))
    |
    (?P<string>"[^"]*")
    |
    (?P<comma>,)
    |
    (?P<semicolon>;)
    """,
    re.VERBOSE,
)


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

    label = None
    if line.endswith(':') and ' ' not in line and ',' not in line:
        label = line[:-1]
        return ParsedLine(line_no, label, None, [])

    pred = 0xFF
    pred_neg = False
    if line.startswith('@'):
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
    return ParsedLine(line_no, label, mnemonic, operands,
                      pred=pred, pred_neg=pred_neg)


# ============================================================
# OPERAND ENCODING
# ============================================================

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
# MNEMONIC -> ENCODING
# ============================================================

# operand layouts: R=reg, I=imm, L=label
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


# ============================================================
# ASSEMBLER
# ============================================================

class Assembler:
    def __init__(self):
        self.labels: dict[str, int] = {}
        self.pc = 0
        self.org = 0

    def assemble(self, source: str) -> bytes:
        lines = source.splitlines()
        parsed = []
        for i, raw in enumerate(lines, 1):
            p = parse_line(raw, i)
            if p:
                parsed.append(p)

        # Pass 1: labels
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

        # Pass 2: emit
        out = bytearray()
        self.pc = 0
        for p in parsed:
            if p.mnemonic == ".org":
                target = imm_value(p.operands[0])
                while len(out) < target:
                    out.append(0)
                self.pc = target
            elif p.mnemonic == ".word":
                w = imm_value(p.operands[0])
                out += w.to_bytes(8, "little")
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
                word = self._encode_instruction(p)
                out += word.to_bytes(8, "little")
                self.pc += 8
        return bytes(out)

    def _encode_instruction(self, p: ParsedLine) -> int:
        if p.mnemonic not in INSTR_FORMATS:
            raise SyntaxError(f"line {p.line_no}: unknown mnemonic {p.mnemonic}")
        op, fmt = INSTR_FORMATS[p.mnemonic]
        if len(p.operands) != len(fmt):
            raise SyntaxError(
                f"line {p.line_no}: {p.mnemonic} expects {len(fmt)} operands, "
                f"got {len(p.operands)}")

        rd = ra = rb = rc = pd = 0
        imm = 0
        imm2 = 0

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
                imm = imm_value(tok) & 0xFFFF
            elif kind == 'L':
                if tok not in self.labels:
                    raise SyntaxError(f"line {p.line_no}: undefined label {tok}")
                target = self.labels[tok]
                # PC-relative offset in instruction words
                word_pc = (self.pc - self.org) // 8
                target_words = (target - self.org) // 8
                offset = target_words - word_pc
                imm = offset & 0xFFFF

        return encode(op, rd, ra, rb, rc, pd,
                      pred=(p.pred != 0xFF), imm=imm, imm2=imm2)


# ============================================================
# CONVENIENCE
# ============================================================

def assemble(source: str) -> bytes:
    return Assembler().assemble(source)


# ============================================================
# SMOKE TEST
# ============================================================

if __name__ == "__main__":
    src = """
        .org 0x1000
    start:
        MOVI  r1, 10
        MOVI  r2, 20
        IADD  r3, r1, r2
        @P0 IADD r4, r3, r1
        BRA   done
        MOVI  r5, 99       ; skipped
    done:
        HALT
    """
    code = assemble(src)
    print(f"assembled {len(code)} bytes")
    print(code.hex())

    # Verify with disassembler if you have it
    from cpu import Opcode
    for off in range(0, len(code), 8):
        w = int.from_bytes(code[off:off + 8], "little")
        op = (w >> 58) & 0x3F
        try:
            name = Opcode(op).name
        except ValueError:
            name = f"<{op:#x}>"
        print(f"  {off:#06x}: {name}  {w:016x}")