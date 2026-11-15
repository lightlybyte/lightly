; fragment.s - Fragment shader kernel demonstrating graphics extensions
; Uses TEX (texture), BLEND, DEPTH, KILL, and LERP opcodes.
;
; Registers:
;   r10 = fb_base
;   r11 = fb_width
;   r12 = depth buffer base
;   r13 = texture coord u (fp)
;   r14 = texture coord v (fp)
;   f0  = interpolated color
;   f1  = barycentric w0
;   f2  = barycentric w1

.org 0x2000

frag_main:
    MOVI r10, 0x10000        ; r10 = framebuffer base
    MOVI r11, 320            ; r11 = width
    MOVI r12, 0x200000       ; r12 = depth buffer base

    ; Compute pixel position from lane ID
    LANEID r15               ; r15 = lane (x within warp)
    MOVI r16, 0              ; r16 = y (fixed for this warp)
    
    ; Compute framebuffer address: fb_base + y*width*4 + x*4
    SHL r17, r11, 2          ; r17 = width * 4
    IMUL r17, r16, r17       ; r17 = y * stride
    SHL r18, r15, 2          ; r18 = x * 4
    IADD r17, r17, r18       ; offset
    IADD r17, r10, r17       ; fb_addr

    ; Depth test: DEPTH ra(depth), rb(depth_buffer_addr)
    MOVI r19, 0x3F800000     ; r19 = 1.0f (depth)
    DEPTH r19, r17           ; fail kills lane

    ; Texture sample: TEX rd, ra(u), rb(v), imm(filter)
    MOVI r20, 0x30000000     ; r20 = 0.25f (u)
    MOVI r21, 0x30000000     ; r21 = 0.25f (v)
    TEX r22, r20, r21, 1     ; r22 = texel (LINEAR filter)

    ; Blend: BLEND ra(addr), rb(color), imm(mode)
    MOVI r23, 0xFF000000     ; r23 = red color with alpha
    BLEND r17, r23, 1        ; alpha blend at fb_addr

    HALT
