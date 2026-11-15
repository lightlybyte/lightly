; gradient.s - Gradient fill kernel for Re:G 1010
; Renders a horizontal/vertical gradient to the framebuffer.
; Red varies with X, green varies with Y, blue fixed at 0x80.

.org 0x1000

start:
    MOVI r10, 0x10000        ; r10 = framebuffer base
    MOVI r13, 320            ; r13 = width
    MOVI r14, 240            ; r14 = height

    MOVI r2, 0               ; r2 = y = 0
yloop:
    MOVI r1, 0               ; r1 = x_base = 0
xloop:
    LANEID r11               ; r11 = lane index 0..31
    IADD   r15, r1, r11      ; r15 = x = x_base + lane

    MOVI   r19, 0xFF
    IAND   r16, r15, r19     ; r = x & 0xFF
    IAND   r17, r2, r19      ; g = y & 0xFF
    SHL    r17, r17, 16      ; g << 16
    IOR    r16, r16, r17     ; combine r and g
    MOVI   r17, 0xFF8080
    IOR    r16, r16, r17     ; color = 0xFF8080 | r | (g << 16)

    MOVI   r19, 1280
    IMUL   r17, r2, r19      ; y * 1280
    SHL    r19, r15, 2       ; x * 4
    IADD   r17, r17, r19
    IADD   r17, r10, r17     ; fb_base + offset
    STG    r16, r17, 0       ; store pixel

    IADD   r1, r1, 32        ; x_base += 32
    SETP   p0, r1, r13, 0    ; p0 = (r1 < 320)
    @P0    BRA xloop

    IADD   r2, r2, 1         ; y += 1
    SETP   p0, r2, r14, 0    ; p0 = (r2 < 240)
    @P0    BRA yloop

    HALT
