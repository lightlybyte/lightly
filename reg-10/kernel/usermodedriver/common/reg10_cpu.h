#ifndef REG10_CPU_H
#define REG10_CPU_H

#include "reg10_isa.h"
#include "reg10_mem.h"

#define REG10_NUM_WARPS_PER_CORE 32
#define REG10_NUM_TENSOR_UNITS   2

typedef struct reg10_warp reg10_warp_t;
typedef struct reg10_core reg10_core_t;

struct reg10_warp {
    uint32_t warp_id;
    uint32_t gp[REG10_NUM_GP][REG10_LANES];      /* per-lane GP registers */
    float    fp[REG10_NUM_FP][REG10_LANES];      /* per-lane FP registers */
    int      pred[REG10_NUM_PRED][REG10_LANES];  /* per-lane predicates */
    uint32_t pc;
    uint32_t active_mask;
    uint32_t call_stack[64];
    int      call_stack_depth;
    uint32_t reconv_pc[64];
    uint32_t reconv_mask[64];
    int      reconv_depth;
    int      state;          /* 0=READY, 1=RUNNING, 2=DONE, 3=WAITING */
    char     wait_reason[32];
    uint64_t cycles_run;
};

struct reg10_core {
    uint32_t core_id;
    reg10_warp_t warps[REG10_NUM_WARPS_PER_CORE];
    int      active_warps[REG10_NUM_WARPS_PER_CORE];
    int      num_active_warps;
    int      scheduler_rr;
    reg10_memory_system_t *mem;
    reg10_scratchpad_t *scratchpad;

    /* Tensor units */
    struct {
        int      busy_cycles;
        int      dest_fp;
        int      dest_warp;
        float    results[REG10_LANES];
    } tensor_units[REG10_NUM_TENSOR_UNITS];
    uint64_t cycles;
};

reg10_core_t *reg10_core_create(uint32_t core_id, reg10_memory_system_t *mem);
void          reg10_core_destroy(reg10_core_t *core);
void          reg10_core_launch_warp(reg10_core_t *core, int warp_id,
                                     uint32_t pc);
int           reg10_core_step(reg10_core_t *core);   /* returns 1 if issued */
int           reg10_core_all_done(reg10_core_t *core);

#endif