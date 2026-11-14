/*
 * reg10_isa.h - Re:G ISA v2 constants, encoding, and opcode definitions.
 * Common to all platforms and frontends.
 */

#ifndef REG10_ISA_H
#define REG10_ISA_H

#include <stdint.h>

/* =========================================================================
 * Machine model
 * ========================================================================= */

#define REG10_LANES           32   /* lanes per warp */
#define REG10_NUM_GP          32   /* general-purpose registers r0-r31 */
#define REG10_NUM_FP          32   /* floating-point registers f0-f31 */
#define REG10_NUM_PRED        8    /* predicate registers p0-p7 */
#define REG10_INSTR_SIZE      8    /* 64-bit fixed width */
#define REG10_WARP_INVALID    0xFF

/* Register classes */
#define REG10_REG_GP          0
#define REG10_REG_FP          1
#define REG10_REG_PRED        2

/* Predicate guard field */
#define REG10_PRED_NONE       31   /* no predicate guard - all lanes */
#define REG10_PRED_NEG_BASE   8    /* 8-15 = negated P0-P7 */

/* =========================================================================
 * Instruction encoding (64 bits, little-endian)
 *
 *   [63:57] opcode     7 bits
 *   [56:52] rd         5 bits
 *   [51:47] ra         5 bits
 *   [46:42] rb         5 bits
 *   [41:37] pred_reg   5 bits
 *   [36:33] pd         4 bits   (SETP/NOTP predicate destination)
 *   [32]    use_imm    1 bit
 *   [31:0]  imm32      32 bits
 * ========================================================================= */

#define REG10_OP_SHIFT  57
#define REG10_RD_SHIFT  52
#define REG10_RA_SHIFT  47
#define REG10_RB_SHIFT  42
#define REG10_PRED_SHIFT 37
#define REG10_PD_SHIFT   33
#define REG10_IMM_SHIFT  32

#define REG10_OP_MASK    0x7Fu
#define REG10_REG_MASK   0x1Fu
#define REG10_PD_MASK    0x0Fu

#define REG10_ENCODE(op, rd, ra, rb, pred_reg, pd, use_imm, imm) \
    ( ((uint64_t)((op) & REG10_OP_MASK)     << REG10_OP_SHIFT)  | \
      ((uint64_t)((rd) & REG10_REG_MASK)    << REG10_RD_SHIFT)  | \
      ((uint64_t)((ra) & REG10_REG_MASK)    << REG10_RA_SHIFT)  | \
      ((uint64_t)((rb) & REG10_REG_MASK)    << REG10_RB_SHIFT)  | \
      ((uint64_t)((pred_reg) & REG10_REG_MASK) << REG10_PRED_SHIFT) | \
      ((uint64_t)((pd) & REG10_PD_MASK)     << REG10_PD_SHIFT)  | \
      ((uint64_t)((use_imm) ? 1 : 0)        << REG10_IMM_SHIFT) | \
      ((uint64_t)((imm) & 0xFFFFFFFFu)) )

/* =========================================================================
 * Opcodes
 * ========================================================================= */

typedef enum reg10_opcode {
    REG10_OP_NOP      = 0x00,
    REG10_OP_HALT     = 0x01,
    REG10_OP_RET      = 0x02,

    REG10_OP_LDG      = 0x10,
    REG10_OP_STG      = 0x11,
    REG10_OP_LDGB     = 0x12,
    REG10_OP_LDGH     = 0x13,
    REG10_OP_LDS      = 0x14,
    REG10_OP_STS      = 0x15,

    REG10_OP_MOV      = 0x20,
    REG10_OP_MOVI     = 0x21,
    REG10_OP_IADD     = 0x22,
    REG10_OP_ISUB     = 0x23,
    REG10_OP_IMUL     = 0x24,
    REG10_OP_IAND     = 0x25,
    REG10_OP_IOR      = 0x26,
    REG10_OP_IXOR     = 0x27,
    REG10_OP_SHL      = 0x28,
    REG10_OP_SHR      = 0x29,
    REG10_OP_ISGT     = 0x2A,
    REG10_OP_ISLT     = 0x2B,
    REG10_OP_ISEQ     = 0x2C,

    REG10_OP_FADD     = 0x30,
    REG10_OP_FSUB     = 0x31,
    REG10_OP_FMUL     = 0x32,
    REG10_OP_FFMA     = 0x33,
    REG10_OP_FMIN     = 0x34,
    REG10_OP_FMAX     = 0x35,
    REG10_OP_I2F      = 0x36,
    REG10_OP_F2I      = 0x37,

    REG10_OP_SETP     = 0x40,
    REG10_OP_NOTP     = 0x41,

    REG10_OP_BRA      = 0x50,
    REG10_OP_BRA_DIV  = 0x51,
    REG10_OP_CALL     = 0x52,
    REG10_OP_JR       = 0x53,

    REG10_OP_VOTE     = 0x60,
    REG10_OP_SHFL     = 0x61,
    REG10_OP_LANEID   = 0x62,

    REG10_OP_TENSOR   = 0x70,
    REG10_OP_SYSCALL  = 0x7E,
    REG10_OP_BAR      = 0x7F,
} reg10_opcode_t;

/* SETP immediate modes */
#define REG10_SETP_LT      0   /* signed ra < rb */
#define REG10_SETP_EQ      1   /* ra == rb */
#define REG10_SETP_NE      2   /* ra != rb */
#define REG10_SETP_ULT     3   /* unsigned ra < rb */

/* =========================================================================
 * Command ring opcodes (host -> GPU)
 * ========================================================================= */

#define REG10_CMD_NOP       0
#define REG10_CMD_DISPATCH  1
#define REG10_CMD_COPY      2
#define REG10_CMD_FENCE     3
#define REG10_CMD_SET_FB    4
#define REG10_CMD_PRESENT   5

/* DISPATCH command: 7 u32 words
 *   word 0: opcode = 1
 *   word 1: grid  (grid_x | grid_y << 16)
 *   word 2: block (block_x | block_y << 16)
 *   word 3: warps_per_block
 *   word 4: kernel_pc
 *   word 5: args_addr
 *   word 6: fence_id
 */
#define REG10_DISPATCH_WORDS 7

/* COPY command: 6 u32 words */
#define REG10_COPY_WORDS 6

/* Framebuffer base (per ISA doc) */
#define REG10_FB_BASE 0x10000u

/* Global RAM size (64 MB, matching ram.py) */
#define REG10_GLOBAL_RAM_SIZE (1u << 26)

/* Scratchpad size per core (64 KB) */
#define REG10_SCRATCHPAD_SIZE (64 * 1024)

/* Maximum framebuffer dimensions */
#define REG10_FB_MAX_W 3840
#define REG10_FB_MAX_H 2160

#endif /* REG10_ISA_H */