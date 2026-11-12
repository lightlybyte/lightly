; kernel.s - gradient fill
;
; Registers:
;   r1  = x_base         r2  = y
;   r10 = fb_base        r11 = lane index
;   r12 = constant 1     r13 = width (320)
;   r14 = height (240)   r15 = x
;   r16 = color          r17 = scratch
;   r18 = lane table base (0x200)
;   r19 = scratch        r20 = lane counter
;   r21 = lane pointer   r22 = lane limit

        .org 0x1000

start:
        MOVI  r10, 0x10000
        MOVI  r12, 1
        MOVI  r13, 320
        MOVI  r14, 240
        MOVI  r18, 0x200

        ; write lane indices 0..31 to 0x200..0x27C
        MOVI  r20, 0
        MOV   r21, r18
        MOVI  r22, 32
lane_init:
        STG   r20, r21, 0
        ADDI  r21, r21, 4
        ADDI  r20, r20, 1
        SETP  p0, r20, r22
        @P0   BRA lane_init

        MOVI  r2, 0
yloop:
        MOVI  r1, 0
xloop:
        LDG   r11, r18, 0
        IADD  r15, r1, r11

        MOVI  r19, 0xFF
        IAND  r16, r15, r19
        IAND  r17, r2, r19
        SHLI  r17, r17, 16
        IOR   r16, r16, r17
        MOVI  r17, 0xFF8080
        IOR   r16, r16, r17

        MOVI  r19, 1280
        IMUL  r17, r2, r19
        SHLI  r19, r15, 2
        IADD  r17, r17, r19
        IADD  r17, r10, r17
        STG   r16, r17, 0

        ADDI  r1, r1, 32
        SETP  p0, r1, r13
        @P0   BRA xloop

        ADDI  r2, r2, 1
        SETP  p0, r2, r14
        @P0   BRA yloop

        HALT