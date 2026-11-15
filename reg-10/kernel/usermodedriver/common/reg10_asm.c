/*
 * reg10_asm.c - Assembler for Re:G ISA v2 (with graphics extensions).
 *
 * Two-pass assembler supporting:
 *   - Labels (name:)
 *   - Predication (@Pn / @!Pn)
 *   - Immediate values (decimal, hex 0x..., negative)
 *   - Registers: rN, fN, pN
 *   - All ISA v2 opcodes plus graphics extensions
 */

#include "reg10_asm.h"
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_LABELS 256
#define MAX_LINE   4096

typedef struct {
    char name[64];
    uint32_t addr;   /* byte address */
} label_t;

typedef struct {
    label_t labels[MAX_LABELS];
    int num_labels;
    int errors;
    char errbuf[1024];
} asm_ctx_t;

/* Opcode lookup table */
typedef struct {
    const char *name;
    uint32_t opcode;
} opcode_entry_t;

static const opcode_entry_t opcodes[] = {
    {"NOP",      0x00}, {"HALT",     0x01}, {"RET",      0x02},
    {"LDG",      0x10}, {"STG",      0x11}, {"LDGB",     0x12},
    {"LDGH",     0x13}, {"LDS",      0x14}, {"STS",      0x15},
    {"MOV",      0x20}, {"MOVI",     0x21}, {"IADD",     0x22},
    {"ISUB",     0x23}, {"IMUL",     0x24}, {"IAND",     0x25},
    {"IOR",      0x26}, {"IXOR",     0x27}, {"SHL",      0x28},
    {"SHR",      0x29}, {"ISGT",     0x2A}, {"ISLT",     0x2B},
    {"ISEQ",     0x2C}, {"FADD",     0x30}, {"FSUB",     0x31},
    {"FMUL",     0x32}, {"FFMA",     0x33}, {"FMIN",     0x34},
    {"FMAX",     0x35}, {"I2F",      0x36}, {"F2I",      0x37},
    {"SETP",     0x40}, {"NOTP",     0x41}, {"BRA",      0x50},
    {"BRA_DIV",  0x51}, {"CALL",     0x52}, {"JR",       0x53},
    {"VOTE",     0x60}, {"SHFL",     0x61}, {"LANEID",   0x62},
    {"TENSOR",   0x70}, {"SYSCALL",  0x7E}, {"BAR",      0x7F},
    /* Graphics extensions */
    {"TEX",      0x80}, {"DDX",      0x81}, {"DDY",      0x82},
    {"KILL",     0x83}, {"BLEND",    0x84}, {"LERP",     0x85},
    {"DEPTH",    0x86}, {"STENCIL",  0x87},
};

static int num_opcodes = sizeof(opcodes) / sizeof(opcodes[0]);

static void set_error(asm_ctx_t *ctx, const char *msg)
{
    if (ctx->errors == 0) {
        snprintf(ctx->errbuf, sizeof(ctx->errbuf), "%s", msg);
    }
    ctx->errors++;
}

static int find_label(asm_ctx_t *ctx, const char *name)
{
    for (int i = 0; i < ctx->num_labels; i++) {
        if (strcmp(ctx->labels[i].name, name) == 0)
            return i;
    }
    return -1;
}

static uint32_t parse_reg(const char *tok, asm_ctx_t *ctx)
{
    /* Expects rN, fN, or pN */
    if (!tok || tok[0] == 0) {
        set_error(ctx, "expected register, got empty");
        return 0;
    }
    char prefix = tok[0];
    int n = atoi(tok + 1);
    if (prefix == 'r') {
        if (n < 0 || n > 31) { set_error(ctx, "GP register out of range"); return 0; }
        return (uint32_t)n;
    } else if (prefix == 'f') {
        if (n < 0 || n > 31) { set_error(ctx, "FP register out of range"); return 0; }
        return (uint32_t)n;
    } else if (prefix == 'p') {
        if (n < 0 || n > 7) { set_error(ctx, "Predicate register out of range"); return 0; }
        return (uint32_t)n;
    }
    set_error(ctx, "invalid register prefix");
    return 0;
}

static int32_t parse_imm(const char *tok, asm_ctx_t *ctx)
{
    if (!tok || tok[0] == 0) {
        set_error(ctx, "expected immediate, got empty");
        return 0;
    }
    char *end = NULL;
    long v = strtol(tok, &end, 0);
    if (end == tok || *end != 0) {
        set_error(ctx, "invalid immediate value");
        return 0;
    }
    return (int32_t)v;
}

/* Split a line into tokens by comma, respecting parentheses for memory ops */
static int split_operands(char *line, char **tokens, int max_tokens)
{
    int count = 0;
    char *p = line;
    int depth = 0;
    char *start = p;

    while (*p) {
        if (*p == '(') depth++;
        else if (*p == ')') depth--;
        else if (*p == ',' && depth == 0) {
            *p = 0;
            tokens[count++] = start;
            start = p + 1;
            if (count >= max_tokens) break;
        }
        p++;
    }
    if (count < max_tokens) {
        tokens[count++] = start;
    }
    return count;
}

static char *trim(char *s)
{
    while (*s && isspace((unsigned char)*s)) s++;
    if (*s == 0) return s;
    char *end = s + strlen(s) - 1;
    while (end > s && isspace((unsigned char)*end)) { *end = 0; end--; }
    return s;
}

/* Find opcode by name */
static uint32_t lookup_opcode(const char *name, asm_ctx_t *ctx)
{
    for (int i = 0; i < num_opcodes; i++) {
        if (strcmp(name, opcodes[i].name) == 0)
            return opcodes[i].opcode;
    }
    set_error(ctx, "unknown opcode");
    return 0xFFFFFFFF;
}

/* Assemble a single instruction line */
static int assemble_instr(asm_ctx_t *ctx, const char *mnemonic,
                          char **operands, int num_operands,
                          int pred_reg, uint32_t pc,
                          uint64_t *out_word, int *unresolved_bra)
{
    uint32_t op = lookup_opcode(mnemonic, ctx);
    if (op == 0xFFFFFFFF) return -1;
    *unresolved_bra = 0;
    uint32_t rd=0,ra=0,rb=0,pd=0; int use_imm=0; int32_t imm=0;
    int err=0, is_branch=0; char *branch_label=NULL;

    if (op>=0x10 && op<=0x15) {
        if (num_operands!=2){set_error(ctx,"mem operand count");return -1;}
        rd=parse_reg(operands[0],ctx);
        char *memop=trim(operands[1]);
        if (memop[0]=='[') memop++;
        size_t mlen=strlen(memop);
        if (mlen>0 && memop[mlen-1]==']') memop[mlen-1]=0;
        char *plus=strchr(memop,'+');
        if (plus){*plus=0;ra=parse_reg(memop,ctx);imm=parse_imm(plus+1,ctx);}
        else {ra=parse_reg(memop,ctx);imm=0;}
    } else if (op==0x20) {
        if (num_operands!=2){set_error(ctx,"MOV operand count");return -1;}
        rd=parse_reg(operands[0],ctx);ra=parse_reg(operands[1],ctx);
    } else if (op==0x21) {
        if (num_operands!=2){set_error(ctx,"MOVI operand count");return -1;}
        rd=parse_reg(operands[0],ctx);imm=parse_imm(operands[1],ctx);use_imm=1;
    } else if (op>=0x22 && op<=0x2C) {
        if (num_operands!=3){set_error(ctx,"ALU operand count");return -1;}
        rd=parse_reg(operands[0],ctx);ra=parse_reg(operands[1],ctx);
        if (operands[2][0]=='r'||operands[2][0]=='f'){rb=parse_reg(operands[2],ctx);use_imm=0;}
        else {imm=parse_imm(operands[2],ctx);use_imm=1;}
    } else if (op>=0x30 && op<=0x35) {
        if (num_operands!=3){set_error(ctx,"FP operand count");return -1;}
        rd=parse_reg(operands[0],ctx);ra=parse_reg(operands[1],ctx);rb=parse_reg(operands[2],ctx);
    } else if (op==0x36||op==0x37) {
        if (num_operands!=2){set_error(ctx,"convert operand count");return -1;}
        rd=parse_reg(operands[0],ctx);ra=parse_reg(operands[1],ctx);
    } else if (op==0x40) {
        if (num_operands!=4){set_error(ctx,"SETP operand count");return -1;}
        pd=parse_reg(operands[0],ctx);ra=parse_reg(operands[1],ctx);rb=parse_reg(operands[2],ctx);imm=parse_imm(operands[3],ctx);
    } else if (op==0x41) {
        if (num_operands!=2){set_error(ctx,"NOTP operand count");return -1;}
        pd=parse_reg(operands[0],ctx);ra=parse_reg(operands[1],ctx);
    } else if (op==0x50||op==0x51||op==0x52) {
        if (num_operands!=1){set_error(ctx,"branch operand count");return -1;}
        is_branch=1; branch_label=trim(operands[0]);
    } else if (op==0x53) {
        if (num_operands!=1){set_error(ctx,"JR operand count");return -1;}
        ra=parse_reg(operands[0],ctx);
    } else if (op==0x60) {
        if (num_operands!=2){set_error(ctx,"VOTE operand count");return -1;}
        rd=parse_reg(operands[0],ctx);imm=parse_imm(operands[1],ctx);
    } else if (op==0x61) {
        if (num_operands!=3){set_error(ctx,"SHFL operand count");return -1;}
        rd=parse_reg(operands[0],ctx);ra=parse_reg(operands[1],ctx);imm=parse_imm(operands[2],ctx);
    } else if (op==0x62) {
        if (num_operands!=1){set_error(ctx,"LANEID operand count");return -1;}
        rd=parse_reg(operands[0],ctx);
    } else if (op==0x70) {
        if (num_operands!=4){set_error(ctx,"TENSOR operand count");return -1;}
        rd=parse_reg(operands[0],ctx);ra=parse_reg(operands[1],ctx);rb=parse_reg(operands[2],ctx);imm=parse_imm(operands[3],ctx);
    } else if (op==0x7E) {
        if (num_operands!=1){set_error(ctx,"SYSCALL operand count");return -1;}
        imm=parse_imm(operands[0],ctx);
    } else if (op==0x7F||op==0x00||op==0x01||op==0x02) {
        if (num_operands!=0){set_error(ctx,"control operand count");return -1;}
    } else if (op==0x80) {
        if (num_operands!=4){set_error(ctx,"TEX operand count");return -1;}
        rd=parse_reg(operands[0],ctx);ra=parse_reg(operands[1],ctx);rb=parse_reg(operands[2],ctx);imm=parse_imm(operands[3],ctx);
    } else if (op==0x81||op==0x82) {
        if (num_operands!=2){set_error(ctx,"DDX/DDY operand count");return -1;}
        rd=parse_reg(operands[0],ctx);ra=parse_reg(operands[1],ctx);
    } else if (op==0x83) {
        if (num_operands!=1){set_error(ctx,"KILL operand count");return -1;}
        ra=parse_reg(operands[0],ctx);
    } else if (op==0x84) {
        if (num_operands!=3){set_error(ctx,"BLEND operand count");return -1;}
        ra=parse_reg(operands[0],ctx);rb=parse_reg(operands[1],ctx);imm=parse_imm(operands[2],ctx);
    } else if (op==0x85) {
        if (num_operands!=4){set_error(ctx,"LERP operand count");return -1;}
        rd=parse_reg(operands[0],ctx);ra=parse_reg(operands[1],ctx);rb=parse_reg(operands[2],ctx);imm=parse_imm(operands[3],ctx);
    } else if (op==0x86) {
        if (num_operands!=2){set_error(ctx,"DEPTH operand count");return -1;}
        ra=parse_reg(operands[0],ctx);rb=parse_reg(operands[1],ctx);
    } else if (op==0x87) {
        if (num_operands!=3){set_error(ctx,"STENCIL operand count");return -1;}
        ra=parse_reg(operands[0],ctx);rb=parse_reg(operands[1],ctx);imm=parse_imm(operands[2],ctx);
    } else {
        set_error(ctx,"unknown opcode");return -1;
    }

    if (is_branch) {
        int lidx = find_label(ctx, branch_label);
        if (lidx >= 0) {
            int32_t target_addr = ctx->labels[lidx].addr;
            imm = target_addr - (int32_t)(pc + 8);
        } else {
            *unresolved_bra = 1;
            imm = 0;
        }
    }

    int pred_enc = (pred_reg >= 0 && pred_reg <= 15) ? pred_reg : 31;
    *out_word = REG10_ENCODE(op, rd, ra, rb, pred_enc, pd, use_imm, imm);
    return 0;
}

int reg10_assemble(const char *src, uint64_t *out, size_t max_words,
                   char *errbuf, size_t errbuf_size)
{
    asm_ctx_t ctx;
    memset(&ctx, 0, sizeof(ctx));
    uint32_t pc = 0;
    char line[4096];
    const char *p = src;
    while (*p) {
        size_t i = 0;
        while (*p && *p != '\n' && i < 4095) { line[i++] = *p++; }
        line[i] = 0;
        if (*p == '\n') p++;
        char *l = trim(line);
        if (*l == 0 || *l == ';') continue;
        for (char *c = l; *c; c++) {
            if (*c == ';' && *(c-1) != '"') { *c = 0; break; }
        }
        l = trim(l);
        if (*l == 0) continue;
        char *colon = strchr(l, ':');
        if (colon && colon == l + strlen(l) - 1) {
            *colon = 0;
            char *label_name = trim(l);
            if (find_label(&ctx, label_name) < 0 && ctx.num_labels < 256) {
                strcpy(ctx.labels[ctx.num_labels].name, label_name);
                ctx.labels[ctx.num_labels].addr = pc;
                ctx.num_labels++;
            }
            continue;
        }
        if (strncmp(l, ".org", 4) == 0) {
            char *arg = trim(l + 4);
            pc = (uint32_t)parse_imm(arg, &ctx);
            continue;
        }
        if (strncmp(l, ".fill", 5) == 0) {
            char *arg = trim(l + 5);
            uint32_t n = (uint32_t)parse_imm(arg, &ctx);
            pc += n * 8;
            continue;
        }
        pc += 8;
    }
    pc = 0;
    size_t word_index = 0;
    p = src;
    while (*p) {
        size_t i = 0;
        while (*p && *p != '\n' && i < 4095) { line[i++] = *p++; }
        line[i] = 0;
        if (*p == '\n') p++;
        char *l = trim(line);
        if (*l == 0 || *l == ';') continue;
        for (char *c = l; *c; c++) {
            if (*c == ';' && *(c-1) != '"') { *c = 0; break; }
        }
        l = trim(l);
        if (*l == 0) continue;
        char *colon = strchr(l, ':');
        if (colon && colon == l + strlen(l) - 1) continue;
        if (strncmp(l, ".org", 4) == 0) {
            char *arg = trim(l + 4);
            pc = (uint32_t)parse_imm(arg, &ctx);
            continue;
        }
        if (strncmp(l, ".fill", 5) == 0) {
            char *arg = trim(l + 5);
            uint32_t n = (uint32_t)parse_imm(arg, &ctx);
            for (uint32_t j = 0; j < n; j++) {
                if (word_index < max_words) out[word_index++] = 0;
                pc += 8;
            }
            continue;
        }
        int pred_reg = 31;
        if (l[0] == '@') {
            char *rest = l + 1;
            int neg = 0;
            if (*rest == '!') { neg = 1; rest++; }
            if (*rest == 'P') {
                rest++;
                int pidx = atoi(rest);
                pred_reg = neg ? (pidx + 8) : pidx;
            }
            l = trim(rest);
            while (*l && isspace((unsigned char)*l)) l++;
        }
        char *mn = l;
        char *sp = l;
        while (*sp && !isspace((unsigned char)*sp)) sp++;
        if (*sp) { *sp = 0; sp++; }
        char *rest_ops = trim(sp);
        char *tokens[8];
        int num_ops = split_operands(rest_ops, tokens, 8);
        for (int t = 0; t < num_ops; t++) tokens[t] = trim(tokens[t]);
        uint64_t word = 0;
        int unresolved = 0;
        if (assemble_instr(&ctx, mn, tokens, num_ops, pred_reg, pc,
                           &word, &unresolved) != 0) {
            if (errbuf && errbuf_size > 0)
                snprintf(errbuf, errbuf_size, "PC 0x%x: %s", pc, ctx.errbuf);
            return -1;
        }
        if (unresolved) {
            if (errbuf && errbuf_size > 0)
                snprintf(errbuf, errbuf_size, "unresolved label at PC 0x%x", pc);
            return -1;
        }
        if (word_index < max_words) out[word_index++] = word;
        pc += 8;
    }
    if (errbuf && errbuf_size > 0) snprintf(errbuf, errbuf_size, "%s", ctx.errbuf);
    return (int)word_index;
}

int reg10_assemble_file(const char *path, uint64_t *out, size_t max_words,
                        char *errbuf, size_t errbuf_size)
{
    FILE *f = fopen(path, "r");
    if (!f) {
        if (errbuf && errbuf_size > 0)
            snprintf(errbuf, errbuf_size, "cannot open %s", path);
        return -1;
    }
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *buf = malloc(sz + 1);
    if (!buf) { fclose(f); return -1; }
    fread(buf, 1, sz, f);
    buf[sz] = 0;
    fclose(f);
    int result = reg10_assemble(buf, out, max_words, errbuf, errbuf_size);
    free(buf);
    return result;
}


