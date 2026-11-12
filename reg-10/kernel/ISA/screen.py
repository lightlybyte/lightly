"""
screen.py - Display endpoint for Re:G.

Takes a Framebuffer and:
  - in `headless` mode, writes a PPM/PAM file for inspection
  - in `terminal` mode, renders a coarse ASCII preview to stdout
  - in `window` mode, opens a tkinter window and refreshes it
    (only if tkinter is available)

No external dependencies. Everything uses the standard library.
"""

from __future__ import annotations
import os
import sys
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ram import Framebuffer


# ============================================================
# PPM WRITER (headless)
# ============================================================

def write_ppm(fb: "Framebuffer", path: str) -> None:
    """Write the framebuffer as a binary PPM (P6). BGRA -> RGB."""
    w, h = fb.width, fb.height
    buf = fb.buffer
    header = f"P6\n{w} {h}\n255\n".encode()
    with open(path, "wb") as f:
        f.write(header)
        out = bytearray(w * h * 3)
        j = 0
        for i in range(0, len(buf), 4):
            out[j]     = buf[i + 2]  # R
            out[j + 1] = buf[i + 1]  # G
            out[j + 2] = buf[i]      # B
            j += 3
        f.write(out)


# ============================================================
# ASCII PREVIEW (terminal)
# ============================================================

_ASCII_RAMP = " .:-=+*#%@"

def render_ascii(fb: "Framebuffer", max_cols: int = 80) -> str:
    """Coarse ASCII preview. Luminance -> ramp character."""
    w, h = fb.width, fb.height
    cols = min(max_cols, w)
    rows = max(1, int(h * cols / w * 0.5))
    scale_x = w / cols
    scale_y = h / rows
    buf = fb.buffer
    lines = []
    for ry in range(rows):
        sy = int(ry * scale_y)
        line_chars = []
        for rx in range(cols):
            sx = int(rx * scale_x)
            off = (sy * w + sx) * 4
            b = buf[off]
            g = buf[off + 1]
            r = buf[off + 2]
            lum = (r * 0.299 + g * 0.587 + b * 0.114) / 255.0
            idx = int(lum * (len(_ASCII_RAMP) - 1))
            line_chars.append(_ASCII_RAMP[idx])
        lines.append("".join(line_chars))
    return "\n".join(lines)


# ============================================================
# TKINTER WINDOW
# ============================================================

class WindowScreen:
    """A tkinter window that displays the framebuffer at a target FPS."""

    def __init__(self, fb: "Framebuffer", title: str = "Re:G",
                 target_fps: int = 60, scale: int = 1):
        import tkinter as tk
        self.fb = fb
        self.tk = tk
        self.scale = scale
        self.target_fps = target_fps
        self.root = tk.Tk()
        self.root.title(title)
        self.canvas = tk.Canvas(
            self.root,
            width=fb.width * scale,
            height=fb.height * scale,
            highlightthickness=0,
        )
        self.canvas.pack()
        self._img = tk.PhotoImage(width=fb.width, height=fb.height)
        self.canvas.create_image(0, 0, anchor="nw", image=self._img,
                                 tags="fb")
        self._last = time.perf_counter()
        self.running = True
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def close(self) -> None:
        self.running = False
        self.root.destroy()

    def refresh(self) -> None:
        w, h = self.fb.width, self.fb.height
        buf = self.fb.buffer
        # Build a PPM byte string; tkinter can load it into a PhotoImage.
        header = f"P6\n{w} {h}\n255\n".encode()
        rgb = bytearray(w * h * 3)
        j = 0
        for i in range(0, len(buf), 4):
            rgb[j]     = buf[i + 2]
            rgb[j + 1] = buf[i + 1]
            rgb[j + 2] = buf[i]
            j += 3
        data = header + rgb
        self._img.configure(data=data, format="ppm")

    def run(self) -> None:
        interval = 1.0 / self.target_fps
        while self.running:
            self.refresh()
            self.root.update_idletasks()
            self.root.update()
            dt = time.perf_counter() - self._last
            if dt < interval:
                time.sleep(interval - dt)
            self._last = time.perf_counter()


# ============================================================
# PUBLIC API
# ============================================================

def present(fb: "Framebuffer", mode: str = "auto",
            path: str = "frame.ppm", target_fps: int = 60) -> None:
    """
    Present the framebuffer.
    mode: 'auto' | 'headless' | 'ascii' | 'window'
    """
    if mode == "auto":
        if sys.stdout.isatty() and _has_tk():
            mode = "window"
        elif sys.stdout.isatty():
            mode = "ascii"
        else:
            mode = "headless"

    if mode == "headless":
        write_ppm(fb, path)
        print(f"wrote {path} ({fb.width}x{fb.height})")
    elif mode == "ascii":
        print(render_ascii(fb))
    elif mode == "window":
        WindowScreen(fb, target_fps=target_fps).run()
    else:
        raise ValueError(f"unknown mode: {mode}")


def _has_tk() -> bool:
    try:
        import tkinter  # noqa: F401
        return True
    except ImportError:
        return False


# ============================================================
# SMOKE TEST
# ============================================================

if __name__ == "__main__":
    from ram import MemorySystem

    print("screen smoke test")
    mem = MemorySystem(num_cores=1, fb_width=64, fb_height=32)
    fb = mem.framebuffer

    # Draw a gradient
    for y in range(fb.height):
        for x in range(fb.width):
            r = x * 4
            g = y * 8
            b = 128
            fb.write_pixel(x, y, (0xFF << 24) | (b << 16) | (g << 8) | r)

    write_ppm(fb, "/tmp/re_g_test.ppm")
    print(render_ascii(fb, max_cols=40))
    print("all tests passed")