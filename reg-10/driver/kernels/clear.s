; clear.s - Clear the framebuffer to a single BGRA32 color
; Re:G 1010 GPU ISA kernel
;
; Registers:
;   r10 = fb_base
;   r11 = width (pixels)
;   r12 = height (pixels)
;   r13 = stride = width * 4
;   r14 = y
;   r15 = x
;   r16 = pixel count remaining in row
;   r17 = color (BGRA32)
;   r18 = byte offset
;
; Args (in global RAM at args_addr):
;   [0] = color (BGRA32)
start:
    MOVI r10, 0x10000        ; fb_base
    MOVI r11, 320            ; width
    MOVI r12, 240            ; height
    MOVI r13, 1280           ; stride

    ; Clear color: cornflower blue BGRA32 = 0xFF6495ED
    MOVI r17, 0xFF6495ED

    MOVI r14, 0              ; y = 0
yloop:
    MOVI r15, 0              ; x = 0
xloop:
    ; byte offset = y * stride + x * 4 + fb_base
    IMUL r18, r14, r13       ; y * stride
    SHLI r19, r15, 2         ; x * 4
    IADD r18, r18, r19
    IADD r18, r10, r18       ; + fb_base

    STG r17, r18, 0          ; store color

    ADDI r15, r15, 1         ; x += 1
    SETP p1, r15, r11, 0     ; p1 = (x < width)
    @P1 BRA xloop

    ADDI r14, r14, 1         ; y += 1
    SETP p1, r14, r12, 0     ; p1 = (y < height)
    @P1 BRA yloop

    HALT