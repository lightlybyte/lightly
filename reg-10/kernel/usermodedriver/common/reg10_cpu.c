/*
 * reg10_cpu.c - SIMT core execution engine for Re:G ISA v2.
 * Mirrors cpu.py from the reference ISA implementation.
 */

#include "reg10_cpu.h"
#include "reg10_isa.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <math.h>

static int32_t sext32(uint32_t v)
{
    return (int32_t)v;
}

reg10_core_t *reg10_core_create(uint32_t core_id,
                                reg10_memory_system_t *mem)
{
    reg10_core_t *c = calloc(1, sizeof(*c));
    if (!c) return NULL;
    c->core_id = core_id;
    c->mem = mem;
    c->scratchpad = &mem->scratchpads[core_id % mem->num_cores];
    c->num_active_warps = 0;
    c->scheduler_rr = 0;
    c->cycles = 0;
    for (int i = 0; i < REG10_NUM_WARPS_PER_CORE; i++) {
        c->warps[i].warp_id = i;
        c->warps[i].state = 2; /* DONE */
        c->warps[i].active_mask = 0;
        c->warps[i].call_stack_depth = 0;
        c->warps[i].reconv_depth = 0;
        c->warps[i].cycles_run = 0;
        for (int l = 0; l < REG10_LANES; l++)
            c->warps[i].gp[0][l] = 0;
        for (int p = 0; p < REG10_NUM_PRED; p++)
            for (int l = 0; l < REG10_LANES; l++)
                c->warps[i].pred[p][l] = 0;
    }
    return c;
}

void reg10_core_destroy(reg10_core_t *core)
{
    free(core);
}

void reg10_core_launch_warp(reg10_core_t *core, int warp_id, uint32_t pc)
{
    reg10_warp_t *w = &core->warps[warp_id];
    w->pc = pc;
    w->active_mask = 0xFFFFFFFFu;
    w->call_stack_depth = 0;
    w->reconv_depth = 0;
    for (int p = 0; p < REG10_NUM_PRED; p++)
        for (int l = 0; l < REG10_LANES; l++)
            w->pred[p][l] = 0;
    w->state = 0; /* READY */
    w->cycles_run = 0;
    int found = 0;
    for (int i = 0; i < core->num_active_warps; i++) {
        if (core->active_warps[i] == warp_id) { found = 1; break; }
    }
    if (!found && core->num_active_warps < REG10_NUM_WARPS_PER_CORE)
        core->active_warps[core->num_active_warps++] = warp_id;
}

int reg10_core_all_done(reg10_core_t *core)
{
    return core->num_active_warps == 0;
}


static int warp_get_gp(reg10_warp_t *w, int reg, int lane)
{
    if (reg == 0) return 0;
    return w->gp[reg][lane];
}

static void warp_set_gp(reg10_warp_t *w, int reg, int lane, uint32_t val)
{
    if (reg != 0)
        w->gp[reg][lane] = val;
}

static float warp_get_fp(reg10_warp_t *w, int reg, int lane)
{
    return w->fp[reg][lane];
}

static void warp_set_fp(reg10_warp_t *w, int reg, int lane, float val)
{
    w->fp[reg][lane] = val;
}

static int warp_get_pred(reg10_warp_t *w, int pred_reg, int lane)
{
    if (pred_reg < 8)
        return w->pred[pred_reg][lane];
    return !w->pred[pred_reg - 8][lane];
}

static void warp_set_pred(reg10_warp_t *w, int pred_reg, int lane, int val)
{
    w->pred[pred_reg][lane] = val ? 1 : 0;
}

static int active_lanes(reg10_warp_t *w, int *out, int max_out)
{
    int n = 0;
    for (int i = 0; i < REG10_LANES && n < max_out; i++) {
        if ((w->active_mask >> i) & 1u)
            out[n++] = i;
    }
    return n;
}



int reg10_core_step(reg10_core_t *core)
{
    int n = core->num_active_warps;
    if (n == 0) return 0;

    reg10_warp_t *w = NULL;
    for (int i = 0; i < n; i++) {
        int wid = core->active_warps[core->scheduler_rr % n];
        core->scheduler_rr = (core->scheduler_rr + 1) % n;
        if (core->warps[wid].state == 0) {
            w = &core->warps[wid];
            break;
        }
    }
    if (!w) return 0;

    w->state = 1;

    if (w->pc + 7 >= mem_ram_size(core->mem)) {
        w->state = 2;
        for (int i = 0; i < core->num_active_warps; i++) {
            if (core->active_warps[i] == (int)w->warp_id) {
                core->active_warps[i] = core->active_warps[core->num_active_warps - 1];
                core->num_active_warps--;
                break;
            }
        }
        return 0;
    }
    uint64_t word = mem_read_u64(core->mem, w->pc);
    w->pc += 8;

    uint32_t op       = (uint32_t)((word >> 57) & 0x7F);
    uint32_t rd       = (uint32_t)((word >> 52) & 0x1F);
    uint32_t ra       = (uint32_t)((word >> 47) & 0x1F);
    uint32_t rb       = (uint32_t)((word >> 42) & 0x1F);
    uint32_t pred_reg = (uint32_t)((word >> 37) & 0x1F);
    uint32_t pd       = (uint32_t)((word >> 33) & 0x0F);
    int      use_imm  = (int)((word >> 32) & 1u);
    int32_t  imm      = sext32((uint32_t)(word & 0xFFFFFFFFu));

    int exec_lanes[REG10_LANES];
    int num_exec = 0;

    if (pred_reg == REG10_PRED_NONE) {
        num_exec = active_lanes(w, exec_lanes, REG10_LANES);
    } else {
        int all_lanes[REG10_LANES];
        int n_all = active_lanes(w, all_lanes, REG10_LANES);
        num_exec = 0;
        for (int i = 0; i < n_all; i++) {
            int l = all_lanes[i];
            if (warp_get_pred(w, (int)pred_reg, l))
                exec_lanes[num_exec++] = l;
        }
    }

    if (num_exec == 0) {
        w->state = 0;
        return 1;
    }
    switch (op) {
    case REG10_OP_NOP:
        break;

    case REG10_OP_HALT:
        w->state = 2;
        for (int i = 0; i < core->num_active_warps; i++) {
            if (core->active_warps[i] == (int)w->warp_id) {
                core->active_warps[i] = core->active_warps[core->num_active_warps - 1];
                core->num_active_warps--;
                break;
            }
        }
        break;

    case REG10_OP_RET:
        if (w->call_stack_depth > 0) {
            w->pc = w->call_stack[--w->call_stack_depth];
        } else {
            w->state = 2;
            for (int i = 0; i < core->num_active_warps; i++) {
                if (core->active_warps[i] == (int)w->warp_id) {
                    core->active_warps[i] = core->active_warps[core->num_active_warps - 1];
                    core->num_active_warps--;
                    break;
                }
            }
        }
        break;

    case REG10_OP_LDG:
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            uint32_t addr = warp_get_gp(w, (int)ra, l) + (uint32_t)imm;
            warp_set_gp(w, (int)rd, l, mem_read_u32(core->mem, addr));
        }
        break;
    case REG10_OP_STG:
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            uint32_t addr = warp_get_gp(w, (int)ra, l) + (uint32_t)imm;
            mem_write_u32(core->mem, addr, warp_get_gp(w, (int)rd, l));
        }
        break;
    case REG10_OP_LDGB:
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            uint32_t addr = warp_get_gp(w, (int)ra, l) + (uint32_t)imm;
            warp_set_gp(w, (int)rd, l, mem_read_u8(core->mem, addr));
        }
        break;
    case REG10_OP_LDGH:
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            uint32_t addr = warp_get_gp(w, (int)ra, l) + (uint32_t)imm;
            warp_set_gp(w, (int)rd, l, mem_read_u16(core->mem, addr));
        }
        break;
    case REG10_OP_LDS:
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            uint32_t addr = warp_get_gp(w, (int)ra, l) + (uint32_t)imm;
            warp_set_gp(w, (int)rd, l, sp_read_u32(core->scratchpad, addr));
        }
        break;
    case REG10_OP_STS:
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            uint32_t addr = warp_get_gp(w, (int)ra, l) + (uint32_t)imm;
            sp_write_u32(core->scratchpad, addr, warp_get_gp(w, (int)rd, l));
        }
        break;

    case REG10_OP_MOV:
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            warp_set_gp(w, (int)rd, l, warp_get_gp(w, (int)ra, l));
        }
        break;
    case REG10_OP_MOVI:
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            warp_set_gp(w, (int)rd, l, (uint32_t)imm);
        }
        break;
    case REG10_OP_IADD:
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            uint32_t b = use_imm ? (uint32_t)imm : warp_get_gp(w, (int)rb, l);
            warp_set_gp(w, (int)rd, l, warp_get_gp(w, (int)ra, l) + b);
        }
        break;
    case REG10_OP_ISUB:
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            uint32_t b = use_imm ? (uint32_t)imm : warp_get_gp(w, (int)rb, l);
            warp_set_gp(w, (int)rd, l, warp_get_gp(w, (int)ra, l) - b);
        }
        break;
    case REG10_OP_IMUL:
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            uint32_t b = use_imm ? (uint32_t)imm : warp_get_gp(w, (int)rb, l);
            warp_set_gp(w, (int)rd, l, warp_get_gp(w, (int)ra, l) * b);
        }
        break;
    case REG10_OP_IAND:
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            uint32_t b = use_imm ? (uint32_t)imm : warp_get_gp(w, (int)rb, l);
            warp_set_gp(w, (int)rd, l, warp_get_gp(w, (int)ra, l) & b);
        }
        break;
    case REG10_OP_IOR:
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            uint32_t b = use_imm ? (uint32_t)imm : warp_get_gp(w, (int)rb, l);
            warp_set_gp(w, (int)rd, l, warp_get_gp(w, (int)ra, l) | b);
        }
        break;
    case REG10_OP_IXOR:
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            uint32_t b = use_imm ? (uint32_t)imm : warp_get_gp(w, (int)rb, l);
            warp_set_gp(w, (int)rd, l, warp_get_gp(w, (int)ra, l) ^ b);
        }
        break;
    case REG10_OP_SHL:
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            uint32_t b = use_imm ? (uint32_t)imm : warp_get_gp(w, (int)rb, l);
            warp_set_gp(w, (int)rd, l, warp_get_gp(w, (int)ra, l) << (b & 31));
        }
        break;
    case REG10_OP_SHR:
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            uint32_t b = use_imm ? (uint32_t)imm : warp_get_gp(w, (int)rb, l);
            warp_set_gp(w, (int)rd, l, warp_get_gp(w, (int)ra, l) >> (b & 31));
        }
        break;
    case REG10_OP_ISGT:
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            int32_t a = (int32_t)warp_get_gp(w, (int)ra, l);
            int32_t b = use_imm ? imm : (int32_t)warp_get_gp(w, (int)rb, l);
            warp_set_gp(w, (int)rd, l, a > b ? 1u : 0u);
        }
        break;
    case REG10_OP_ISLT:
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            int32_t a = (int32_t)warp_get_gp(w, (int)ra, l);
            int32_t b = use_imm ? imm : (int32_t)warp_get_gp(w, (int)rb, l);
            warp_set_gp(w, (int)rd, l, a < b ? 1u : 0u);
        }
        break;
    case REG10_OP_ISEQ:
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            uint32_t b = use_imm ? (uint32_t)imm : warp_get_gp(w, (int)rb, l);
            warp_set_gp(w, (int)rd, l,
                        warp_get_gp(w, (int)ra, l) == b ? 1u : 0u);
        }
        break;


    case REG10_OP_FADD:
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            warp_set_fp(w, (int)rd, l,
                        warp_get_fp(w, (int)ra, l) + warp_get_fp(w, (int)rb, l));
        }
        break;
    case REG10_OP_FSUB:
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            warp_set_fp(w, (int)rd, l,
                        warp_get_fp(w, (int)ra, l) - warp_get_fp(w, (int)rb, l));
        }
        break;
    case REG10_OP_FMUL:
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            warp_set_fp(w, (int)rd, l,
                        warp_get_fp(w, (int)ra, l) * warp_get_fp(w, (int)rb, l));
        }
        break;
    case REG10_OP_FFMA:
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            warp_set_fp(w, (int)rd, l,
                        warp_get_fp(w, (int)ra, l) * warp_get_fp(w, (int)rb, l) +
                        warp_get_fp(w, (int)rd, l));
        }
        break;
    case REG10_OP_FMIN:
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            float a = warp_get_fp(w, (int)ra, l);
            float b = warp_get_fp(w, (int)rb, l);
            warp_set_fp(w, (int)rd, l, a < b ? a : b);
        }
        break;
    case REG10_OP_FMAX:
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            float a = warp_get_fp(w, (int)ra, l);
            float b = warp_get_fp(w, (int)rb, l);
            warp_set_fp(w, (int)rd, l, a > b ? a : b);
        }
        break;
    case REG10_OP_I2F:
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            warp_set_fp(w, (int)rd, l,
                        (float)(int32_t)warp_get_gp(w, (int)ra, l));
        }
        break;
    case REG10_OP_F2I:
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            warp_set_gp(w, (int)rd, l,
                        (uint32_t)(int32_t)warp_get_fp(w, (int)ra, l));
        }
        break;

    case REG10_OP_SETP: {
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            int32_t a = (int32_t)warp_get_gp(w, (int)ra, l);
            int32_t b = (int32_t)warp_get_gp(w, (int)rb, l);
            int res = 0;
            switch ((int)imm) {
            case REG10_SETP_LT:  res = a < b; break;
            case REG10_SETP_EQ:  res = a == b; break;
            case REG10_SETP_NE:  res = a != b; break;
            case REG10_SETP_ULT: res = (uint32_t)a < (uint32_t)b; break;
            default:             res = 0; break;
            }
            warp_set_pred(w, (int)pd, l, res);
        }
        break;
    }
    case REG10_OP_NOTP:
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            warp_set_pred(w, (int)pd, l, !warp_get_pred(w, (int)ra, l));
        }
        break;

    case REG10_OP_BRA:
        if (pred_reg == REG10_PRED_NONE) {
            w->pc = (w->pc + (uint32_t)imm);
        } else {
            int all_agree = 1;
            int first = warp_get_pred(w, (int)pred_reg, exec_lanes[0]);
            for (int i = 1; i < num_exec; i++) {
                if (warp_get_pred(w, (int)pred_reg, exec_lanes[i]) != first) {
                    all_agree = 0; break;
                }
            }
            if (all_agree && first)
                w->pc = (w->pc + (uint32_t)imm);
        }
        break;

    case REG10_OP_BRA_DIV: {
        if (pred_reg == REG10_PRED_NONE) {
            w->pc = (w->pc + (uint32_t)imm);
        } else {
            int taken[REG10_LANES], n_taken = 0;
            int not_taken[REG10_LANES], n_not = 0;
            for (int i = 0; i < num_exec; i++) {
                int l = exec_lanes[i];
                if (warp_get_pred(w, (int)pred_reg, l))
                    taken[n_taken++] = l;
                else
                    not_taken[n_not++] = l;
            }
            if (n_taken == 0) {
                /* stay */
            } else if (n_not == 0) {
                w->pc = (w->pc + (uint32_t)imm);
            } else {
                if (w->reconv_depth < 64) {
                    w->reconv_pc[w->reconv_depth] = w->pc;
                    w->reconv_mask[w->reconv_depth] = 0;
                    for (int i = 0; i < n_not; i++)
                        w->reconv_mask[w->reconv_depth] |= (1u << not_taken[i]);
                    w->reconv_depth++;
                }
                w->active_mask = 0;
                for (int i = 0; i < n_taken; i++)
                    w->active_mask |= (1u << taken[i]);
                w->pc = (w->pc + (uint32_t)imm);
            }
        }
        break;
    }

    case REG10_OP_CALL:
        if (w->call_stack_depth < 64) {
            w->call_stack[w->call_stack_depth++] = w->pc;
        }
        w->pc = (w->pc + (uint32_t)imm);
        break;

    case REG10_OP_JR:
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            w->pc = warp_get_gp(w, (int)ra, l);
            break;
        }
        break;

    case REG10_OP_VOTE: {
        uint32_t mask = 0;
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            if (w->pred[0][l]) mask |= (1u << l);
        }
        int mode = (int)imm;
        if (mode == 0) {
            for (int i = 0; i < num_exec; i++) {
                int l = exec_lanes[i];
                warp_set_gp(w, (int)rd, l, mask);
            }
        } else if (mode == 1) {
            uint32_t v = mask ? 1u : 0u;
            for (int i = 0; i < num_exec; i++) {
                int l = exec_lanes[i];
                warp_set_gp(w, (int)rd, l, v);
            }
        } else if (mode == 2) {
            uint32_t v = (mask == w->active_mask) ? 1u : 0u;
            for (int i = 0; i < num_exec; i++) {
                int l = exec_lanes[i];
                warp_set_gp(w, (int)rd, l, v);
            }
        }
        break;
    }

    case REG10_OP_SHFL: {
        int src = (int)imm & 31;
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            warp_set_gp(w, (int)rd, l, warp_get_gp(w, (int)ra, src));
        }
        break;
    }

    case REG10_OP_LANEID:
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            warp_set_gp(w, (int)rd, l, (uint32_t)l);
        }
        break;

    case REG10_OP_TENSOR: {
        int unit = (int)imm & 3;
        if (unit >= REG10_NUM_TENSOR_UNITS) {
            w->state = 2;
            break;
        }
        if (core->tensor_units[unit].busy_cycles > 0) {
            w->state = 3;
            snprintf(w->wait_reason, sizeof(w->wait_reason), "tensor%d", unit);
            w->pc -= 8;
            break;
        }
        core->tensor_units[unit].busy_cycles = 4;
        core->tensor_units[unit].dest_fp = (int)rd;
        core->tensor_units[unit].dest_warp = (int)w->warp_id;
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            core->tensor_units[unit].results[l] = warp_get_fp(w, (int)ra, l) * warp_get_fp(w, (int)rb, l)
                           + warp_get_fp(w, (int)rd, l);
        }
        break;
    }

    case REG10_OP_SYSCALL:
        if (imm == 1) {
            for (int i = 0; i < num_exec; i++) {
                int l = exec_lanes[i];
                printf("SYSCALL[gp] warp=%u lane=%d val=%u\n",
                       w->warp_id, l, warp_get_gp(w, (int)ra, l));
            }
        } else if (imm == 2) {
            for (int i = 0; i < num_exec; i++) {
                int l = exec_lanes[i];
                printf("SYSCALL[fp] warp=%u lane=%d val=%f\n",
                       w->warp_id, l, warp_get_fp(w, (int)ra, l));
            }
        }
        break;

    case REG10_OP_BAR:
        break;

    case REG10_OP_TEX: {
        int filter = (int)imm & 3;
        (void)filter;
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            float u = warp_get_fp(w, (int)ra, l);
            float v = warp_get_fp(w, (int)rb, l);
            int tu = (int)(u * 16.0f) & 1;
            int tv = (int)(v * 16.0f) & 1;
            uint32_t texel = ((tu ^ tv) ? 0xFFFFFFFFu : 0xFF000000u);
            warp_set_gp(w, (int)rd, l, texel);
        }
        break;
    }

    case REG10_OP_DDX:
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            int right = (l + 1) & 31;
            float val = warp_get_fp(w, (int)ra, l);
            float nxt = warp_get_fp(w, (int)ra, right);
            warp_set_fp(w, (int)rd, l, nxt - val);
        }
        break;

    case REG10_OP_DDY:
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            float val = warp_get_fp(w, (int)ra, l);
            warp_set_fp(w, (int)rd, l, 0.0f);
        }
        break;

    case REG10_OP_KILL:
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            if (warp_get_gp(w, (int)ra, l) != 0) {
                w->active_mask &= ~(1u << l);
            }
        }
        num_exec = 0;
        if (pred_reg == REG10_PRED_NONE) {
            num_exec = active_lanes(w, exec_lanes, REG10_LANES);
        }
        if (num_exec == 0) {
            w->state = 0;
            return 1;
        }
        break;

    case REG10_OP_LERP:
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            float v0 = warp_get_fp(w, (int)ra, l);
            float v1 = warp_get_fp(w, (int)rb, l);
            float w0 = warp_get_fp(w, 31, l);
            float w1 = warp_get_fp(w, 30, l);
            float w2 = 1.0f - w0 - w1;
            float v2 = *(float*)&imm;
            warp_set_fp(w, (int)rd, l, w0 * v0 + w1 * v1 + w2 * v2);
        }
        break;

    case REG10_OP_DEPTH:
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            float depth = warp_get_fp(w, (int)ra, l);
            uint32_t db_addr = warp_get_gp(w, (int)rb, l);
            uint32_t stored_u32 = mem_read_u32(core->mem, db_addr);
            float stored;
            memcpy(&stored, &stored_u32, sizeof(float));
            if (depth > stored) {
                w->active_mask &= ~(1u << l);
            } else {
                mem_write_u32(core->mem, db_addr, *(uint32_t*)&depth);
            }
        }
        num_exec = active_lanes(w, exec_lanes, REG10_LANES);
        if (num_exec == 0) {
            w->state = 0;
            return 1;
        }
        break;

    case REG10_OP_STENCIL:
        for (int i = 0; i < num_exec; i++) {
            int l = exec_lanes[i];
            uint32_t ref = (uint32_t)imm;
            uint32_t st_addr = warp_get_gp(w, (int)rb, l);
            uint32_t st_val = mem_read_u32(core->mem, st_addr);
            uint32_t cmp = warp_get_gp(w, (int)ra, l);
            int pass = 0;
            switch ((int)(cmp & 7)) {
            case 0: pass = (st_val == ref); break;
            case 1: pass = (st_val != ref); break;
            case 2: pass = (st_val <  ref); break;
            case 3: pass = (st_val <= ref); break;
            case 4: pass = (st_val >  ref); break;
            case 5: pass = (st_val >= ref); break;
            case 6: pass = 1; break;
            case 7: pass = 0; break;
            }
            if (!pass) {
                w->active_mask &= ~(1u << l);
            }
        }
        num_exec = active_lanes(w, exec_lanes, REG10_LANES);
        if (num_exec == 0) {
            w->state = 0;
            return 1;
        }
        break;

    default:
        w->state = 2;
        break;
    }

    /* Tensor unit tick / completion */
    for (int i = 0; i < REG10_NUM_TENSOR_UNITS; i++) {
        if (core->tensor_units[i].busy_cycles > 0) {
            core->tensor_units[i].busy_cycles--;
            if (core->tensor_units[i].busy_cycles == 0) {
                int dw = core->tensor_units[i].dest_warp;
                if (dw >= 0 && dw < REG10_NUM_WARPS_PER_CORE) {
                    reg10_warp_t *dw_ptr = &core->warps[dw];
                    for (int lane = 0; lane < REG10_LANES; lane++) {
                        if ((dw_ptr->active_mask >> lane) & 1u)
                            warp_set_fp(dw_ptr, core->tensor_units[i].dest_fp,
                                        lane, core->tensor_units[i].results[lane]);
                    }
                    if (dw_ptr->state == 3) {
                        char wr[32];
                        snprintf(wr, sizeof(wr), "tensor%d", i);
                        if (strcmp(dw_ptr->wait_reason, wr) == 0) {
                            dw_ptr->state = 0;
                            dw_ptr->wait_reason[0] = 0;
                        }
                    }
                }
            }
        }
    }

    /* Reconvergence check */
    if (w->reconv_depth > 0 && w->pc == w->reconv_pc[w->reconv_depth - 1]) {
        w->active_mask |= w->reconv_mask[w->reconv_depth - 1];
        w->pc = w->reconv_pc[w->reconv_depth - 1];
        w->reconv_depth--;
    }

    if (w->state == 1)
        w->state = 0;

    w->cycles_run++;
    core->cycles++;
    return 1;
}

