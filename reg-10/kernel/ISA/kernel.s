; kernel.s - Re:G gradient test kernel
; Writes a gradient into the framebuffer region at 0x10000.
; Assumes 320x240 framebuffer, BGRA8, stride 1280 bytes.

        .org 0x1000

start:
        MOVI  r10, 0x10000      ; framebuffer base
        MOVI  r1, 0             ; y = 0

yloop:
        MOVI  r2, 0             ; x = 0

xloop:
        ; color = 0xFF000000 | (y << 8) | x
        SHL   r3, r1, 8         ; r3 = y << 8
        IOR   r3, r3, r2        ; r3 = (y << 8) | x
        MOVI  r4, 0xFF000000    ; note: imm is 15-bit; use shift to build
        ; Rebuild 0xFF000000 as (0xFF << 24). 0xFF fits in imm.
        MOVI  r4, 0xFF
        SHL   r4, r4, 24        ; r4 = 0xFF000000
        IOR   r3, r3, r4        ; r3 = 0xFF000000 | (y<<8) | x

        ; offset = (y * 320 + x) * 4
        MOVI  r5, 320
        IMUL  r6, r1, r5        ; r6 = y * 320
        IADD  r6, r6, r2        ; r6 = y*320 + x
        SHL   r6, r6, 2         ; r6 = offset in bytes

        IADD  r7, r10, r6       ; r7 = framebuffer address
        STG   r3, r7, 0         ; store pixel

        ; x++
        MOVI  r8, 1
        IADD  r2, r2, r8

        ; if x < 320 goto xloop
        MOVI  r9, 320
        SETP  p0, r2, r9, 0     ; p0 = (r2 < r9)
        @P0   BRA xloop

        ; y++
        MOVI  r8, 1
        IADD  r1, r1, r8

        ; if y < 240 goto yloop
        MOVI  r9, 240
        SETP  p0, r1, r9, 0     ; p0 = (r1 < r9)
        @P0   BRA yloop

        HALT