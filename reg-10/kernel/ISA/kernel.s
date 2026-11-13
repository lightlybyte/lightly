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

start:
        ; r10 = 0x10000 (built from two instructions because
        ; the immediate field is only 15 bits)
        MOVI  r10, 1
        SHLI  r10, r10, 16        ; r10 = 0x10000

        MOVI  r12, 1              ; constant 1
        MOVI  r13, 320            ; width
        MOVI  r14, 240            ; height

        ; r18 = 0x200 (lane table base) — fits in 15 bits
        MOVI  r18, 0x200

        ; write lane indices 0..31 to 0x200..0x27C
        MOVI  r20, 0              ; counter
        MOV   r21, r18            ; pointer
        MOVI  r22, 32             ; limit
lane_init:
        STG   r20, r21, 0
        ADDI  r21, r21, 4
        ADDI  r20, r20, 1
        SETP  p0, r20, r22
        @P0   BRA lane_init

        ; y = 0
        MOVI  r2, 0
yloop:
        ; x_base = 0
        MOVI  r1, 0
xloop:
        ; lane index = *r18
        LDG   r11, r18, 0
        ; x = x_base + lane
        IADD  r15, r1, r11

        ; ---- color = 0xFF8080 | (x & 0xFF) | ((y & 0xFF) << 16) ----
        MOVI  r19, 0xFF
        IAND  r16, r15, r19       ; r = x & 0xFF
        IAND  r17, r2, r19        ; g = y & 0xFF
        SHLI  r17, r17, 16        ; g << 16
        IOR   r16, r16, r17       ; r | (g << 16)

        ; build 0xFF8080 in r17 (3 instructions because 0xFF8080
        ; does not fit in a 15-bit immediate)
        MOVI  r17, 0xFF
        SHLI  r17, r17, 16        ; 0xFF0000
        MOVI  r19, 0x80
        SHLI  r19, r19, 8         ; 0x8000
        IOR   r17, r17, r19       ; 0xFF8000
        MOVI  r19, 0x80
        IOR   r17, r17, r19       ; 0xFF8080

        IOR   r16, r16, r17       ; final color

        ; ---- byte offset = y*1280 + x*4 ----
        MOVI  r19, 1280
        IMUL  r17, r2, r19        ; y * 1280
        SHLI  r19, r15, 2         ; x * 4
        IADD  r17, r17, r19
        IADD  r17, r10, r17       ; fb_base + offset
        STG   r16, r17, 0

        ; x_base += 32
        ADDI  r1, r1, 32
        SETP  p0, r1, r13
        @P0   BRA xloop

        ; y += 1
        ADDI  r2, r2, 1
        SETP  p0, r2, r14
        @P0   BRA yloop

        HALT