; triangle.s — rasterize one filled triangle
;
; Pipeline:
;   1. For each scanline y in the triangle's bounding box:
;        a. Find the left and right x extents of the triangle at that y
;        b. For each x in [left, right]:
;             compute the color, compute the byte offset, STG the pixel
;
; This kernel is scalar (one thread does everything). The SIMT version
; would have one warp per scanline and use LANEID to cover 32 pixels at a
; time. This version is slower but simpler to verify.
;
; Registers:
;   r10 = fb_base (0x10000)
;   r11 = fb_width (320)
;   r12 = fb_height (240)
;   r13 = fb_stride = width * 4 (1280)
;   r14 = y (scanline)
;   r15 = x (pixel column)
;   r16 = x_left for this scanline
;   r17 = x_right for this scanline
;   r18 = byte offset
;   r19 = color
;   r20 = scratch
;   r21 = scratch
;
; Triangle vertices (screen space, y down):
;   v0 = (160, 40)   top center
;   v1 = (40, 200)   bottom left
;   v2 = (280, 200)  bottom right
;
; The triangle is isoceles, pointing up. The bounding box is
; x in [40, 280], y in [40, 200].

start:
    MOVI r10, 0x10000          ; fb_base
    MOVI r11, 320              ; width
    MOVI r12, 240              ; height
    MOVI r13, 1280             ; stride = 320 * 4

    ; y = top of bounding box
    MOVI r14, 40
yloop:
    ; ---- compute x_left and x_right for this scanline ----
    ;
    ; Left edge goes from v0=(160,40) to v1=(40,200).
    ;   slope = (40 - 160) / (200 - 40) = -120 / 160 = -0.75
    ;   x_left = 160 + (y - 40) * (-0.75)
    ;
    ; Right edge goes from v0=(160,40) to v2=(280,200).
    ;   slope = (280 - 160) / (200 - 40) = 120 / 160 = 0.75
    ;   x_right = 160 + (y - 40) * 0.75
    ;
    ; To avoid floats, use integer math:
    ;   x_left  = 160 - (3 * (y - 40)) / 4
    ;   x_right = 160 + (3 * (y - 40)) / 4

    SUBI r20, r14, 40          ; r20 = y - 40
    MULI r20, r20, 3           ; r20 = 3 * (y - 40)
    SHRI r20, r20, 2           ; r20 = 3 * (y - 40) / 4

    SUBI r16, 160, r20         ; x_left  = 160 - r20   -- wait, SUBI takes imm not reg
    ; fix: use ISUB
    MOVI r21, 160
    ISUB r16, r21, r20         ; x_left  = 160 - r20

    MOVI r21, 160
    IADD r17, r21, r20         ; x_right = 160 + r20

    ; ---- x = x_left ----
    MOV  r15, r16
xloop:
    ; if x > x_right, goto next scanline
    SETP p1, r15, r17, 0       ; p1 = (x > x_right)? no, SETP mode 0 is <
    ; we want "x > x_right" -> use ISGT then SETP mode 2 on the result
    ISGT r21, r15, r17         ; r21 = 1 if x > x_right
    SETP p1, r21, r0, 2        ; p1 = (r21 != 0)
    @P1  BRA next_scanline

    ; ---- color: fade from red at top to green at bottom ----
    ;
    ; t = (y - 40) / 160    0 at top, 1 at bottom
    ; To avoid floats, use integer blend:
    ;   green = ((y - 40) * 255) / 160
    ;   red   = 255 - green
    ;
    SUBI r20, r14, 40          ; r20 = y - 40
    MULI r20, r20, 255         ; r20 = (y - 40) * 255
    ; divide by 160 — approximate with >> 7 (divides by 128) then * 160/128
    ; Actually just use SHRI by 7 to get roughly 0..255 range
    SHRI r20, r20, 7           ; r20 ≈ green
    ; clamp to 0..255 (SHRI won't exceed 255 if y-40 <= 200)
    ANDI r20, r20, 0xFF        ; r20 = green

    MOVI r21, 255
    ISUB r21, r21, r20         ; r21 = red = 255 - green

    ; color = 0xFF000000 | (red << 16) | (green << 8) | 0x80
    SHLI r19, r21, 16          ; red << 16
    SHLI r20, r20, 8           ; green << 8
    IOR  r19, r19, r20
    MOVI r20, 0xFF80           ; blue = 0x80, alpha = 0xFF
    IOR  r19, r19, r20         ; color = 0xFFRRGGBB... wait, alpha is high

    ; fix: color = (0xFF << 24) | (red << 16) | (green << 8) | blue
    ; rebuild from scratch
    MOVI r19, 0xFF
    SHLI r19, r19, 24          ; 0xFF000000
    SHLI r21, r21, 16          ; red << 16
    IOR  r19, r19, r21
    SHLI r20, r20, 8           ; green << 8  (r20 was shifted earlier; reload)
    ; reload green
    SUBI r20, r14, 40
    MULI r20, r20, 255
    SHRI r20, r20, 7
    ANDI r20, r20, 0xFF
    SHLI r20, r20, 8
    IOR  r19, r19, r20
    MOVI r20, 0x80
    IOR  r19, r19, r20         ; final: 0xFFRRGGBB with B=0x80

    ; ---- byte offset = y * stride + x * 4 + fb_base ----
    IMUL r18, r14, r13         ; y * 1280
    SHLI r20, r15, 2           ; x * 4
    IADD r18, r18, r20
    IADD r18, r10, r18         ; fb_base + offset

    ; ---- store pixel ----
    STG r19, r18, 0

    ; ---- x += 1 ----
    ADDI r15, r15, 1
    ; if x <= x_right, loop
    ; SETP mode 3 is unsigned <, mode 0 is signed <
    ; we want x <= x_right -> NOT (x > x_right)
    ISGT r21, r15, r17
    SETP p1, r21, r0, 2
    @!P1 BRA xloop

next_scanline:
    ; ---- y += 1 ----
    ADDI r14, r14, 1
    ; if y <= 200, loop
    MOVI r20, 200
    ISGT r21, r14, r20
    SETP p1, r21, r0, 2
    @!P1 BRA yloop

    HALT