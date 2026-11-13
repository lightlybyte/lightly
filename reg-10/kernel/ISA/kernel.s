; kernel.s - gradient fill using ISA v2
start:
    MOVI r10, 0x10000       ; fb_base (32-bit immediate, no truncation)
    MOVI r13, 320           ; width
    MOVI r14, 240           ; height

    MOVI r2, 0              ; y = 0
yloop:
    MOVI r1, 0              ; x_base = 0
xloop:
    LANEID r11              ; r11 = lane index (0..31)
    IADD r15, r1, r11       ; x = x_base + lane

    ; color = 0xFF8080 | (x & 0xFF) | ((y & 0xFF) << 16)
    MOVI r19, 0xFF
    IAND r16, r15, r19      ; r = x & 0xFF
    IAND r17, r2, r19       ; g = y & 0xFF
    SHLI r17, r17, 16       ; g << 16
    IOR r16, r16, r17
    MOVI r17, 0xFF8080      ; 32-bit immediate, fits
    IOR r16, r16, r17

    ; byte offset = y * 1280 + x * 4 + fb_base
    MOVI r19, 1280
    IMUL r17, r2, r19
    SHLI r19, r15, 2
    IADD r17, r17, r19
    IADD r17, r10, r17
    STG r16, r17, 0

    ADDI r1, r1, 32         ; x_base += 32
    SETP p0, r1, r13, 0     ; p0 = (r1 < 320)
    @P0 BRA xloop

    ADDI r2, r2, 1          ; y += 1
    SETP p0, r2, r14, 0     ; p0 = (r2 < 240)
    @P0 BRA yloop

    HALT