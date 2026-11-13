; gradient.s - Re:G 1010 gradient fill kernel
; Renders a horizontal/vertical gradient: red varies with X, green with Y, blue fixed 0x80.
;
; Registers:
;   r10 = fb_base
;   r11 = width (320)
;   r12 = height (240)
;   r13 = y
;   r14 = x_base
;   r15 = lane id
;   r16 = x = x_base + lane
;   r17-19 = color components
;   r20 = scratch
;
start:
    MOVI r10, 0x10000        ; fb_base
    MOVI r11, 320            ; width
    MOVI r12, 240            ; height

    MOVI r13, 0              ; y = 0
yloop:
    MOVI r14, 0              ; x_base = 0
xloop:
    LANEID r15               ; lane 0..31
    IADD r16, r14, r15       ; x = x_base + lane

    ; color = 0xFF8080 | (x & 0xFF) | ((y & 0xFF) << 16)
    MOVI r19, 0xFF
    IAND r17, r16, r19       ; r = x & 0xFF
    IAND r18, r13, r19       ; g = y & 0xFF
    SHLI r18, r18, 16        ; g << 16
    IOR  r17, r17, r18
    MOVI r18, 0xFF8080
    IOR  r17, r17, r18       ; final color

    ; byte offset = y * 1280 + x * 4 + fb_base
    MOVI r19, 1280
    IMUL r18, r13, r19       ; y * 1280
    SHLI r19, r16, 2         ; x * 4
    IADD r18, r18, r19
    IADD r18, r10, r18       ; + fb_base

    STG r17, r18, 0

    ADDI r14, r14, 32        ; x_base += 32
    SETP p0, r14, r11, 0     ; p0 = (r14 < width)
    @P0 BRA xloop

    ADDI r13, r13, 1         ; y += 1
    SETP p0, r13, r12, 0     ; p0 = (r13 < height)
    @P0 BRA yloop

    HALT