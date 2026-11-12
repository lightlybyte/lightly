        .org 0x1000
    start:
        MOVI  r1, 0            ; y
    yloop:
        MOVI  r2, 0            ; x
    xloop:
        ; compute color = (x << 16) | (y << 8) | 0xFF
        SHL   r3, r2, 16
        SHL   r4, r1, 8
        IOR   r3, r3, r4
        MOVI  r4, 0xFF
        IOR   r3, r3, r4
        ; fb offset = (y * W + x) * 4 = y*W*4 + x*4
        MOVI  r5, 1280         ; W*4 for 320-wide
        IMUL  r6, r1, r5
        SHL   r7, r2, 2
        IADD  r6, r6, r7
        ; STG [fb_base + r6], r3  (fb_base = 0x10000)
        MOVI  ra, 0x10000      ; note: ra is r0? use r10
        ; -- actually use r10 as base
        MOVI  r10, 0x10000
        IADD  r10, r10, r6
        STG   r3, r10, 0
        MOVI  r8, 1
        IADD  r2, r2, r8
        MOVI  r9, 320
        SETP  p0, r2, r9
        @P1  BRA xloop
        MOVI  r8, 1
        IADD  r1, r1, r8
        MOVI  r9, 240
        SETP  p0, r1, r9
        @P1  BRA yloop
        HALT