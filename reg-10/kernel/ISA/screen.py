"""
screen.py - Non-blocking tkinter display for Re:G.

Performance strategy:
  - The framebuffer is BGRA in memory. tkinter's PhotoImage accepts a PPM
    byte string via .configure(data=...). One allocation per frame is the
    floor for tkinter's API, but we avoid per-pixel Python loops by using
    a preallocated RGB staging buffer and strided slice assignment.
  - The RGB staging buffer is sized once at construction, so per-frame
    work is O(pixels) memcpy, not O(pixels) Python iteration.
  - The BGRA->RGB swap is done with slice copies: three strided copies
    (one per channel). CPython implements these as C-level memmove loops.

Expect ~2-3 ms per 640x480 frame on a modern machine.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ram import Framebuffer


class Screen:
    def __init__(self, fb: "Framebuffer", title: str = "Re:G"):
        import tkinter as tk

        self.fb = fb
        self.tk = tk
        self.running = True

        self.w = fb.width
        self.h = fb.height
        self.pixels = self.w * self.h

        self.root = tk.Tk()
        self.root.title(title)
        self.root.resizable(False, False)

        self.canvas = tk.Canvas(
            self.root,
            width=self.w, height=self.h,
            highlightthickness=0, bd=0)
        self.canvas.pack()

        self._img = tk.PhotoImage(width=self.w, height=self.h)
        self.canvas.create_image(0, 0, anchor="nw", image=self._img)

        # PPM header, allocated once
        self._header = f"P6\n{self.w} {self.h}\n255\n".encode()

        # RGB staging buffer, allocated once
        self._rgb = bytearray(self.pixels * 3)

        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Escape>", lambda e: self.close())

    def close(self) -> None:
        self.running = False
        try:
            self.root.destroy()
        except Exception:
            pass

    def refresh(self) -> None:
        src = self.fb.view()          # BGRA bytes (memoryview)
        rgb = self._rgb               # RGB bytes (bytearray)

        # BGRA -> RGB via strided slice assignment.
        # src[0::4] = B, src[1::4] = G, src[2::4] = R, src[3::4] = A
        # Destination: rgb[0::3]=R, rgb[1::3]=G, rgb[2::3]=B
        rgb[0::3] = src[2::4]   # R
        rgb[1::3] = src[1::4]   # G
        rgb[2::3] = src[0::4]   # B

        # PPM is immutable bytes; build it once with header + rgb.
        self._img.configure(data=self._header + bytes(rgb), format="ppm")

    def pump(self) -> bool:
        if not self.running:
            return False
        try:
            self.refresh()
            self.root.update_idletasks()
            self.root.update()
        except Exception:
            self.running = False
            return False
        return True


def show_nonblocking(fb: "Framebuffer", title: str = "Re:G") -> Screen:
    return Screen(fb, title=title)


if __name__ == "__main__":
    import time
    from ram import MemorySystem

    mem = MemorySystem(num_cores=1, fb_width=640, fb_height=480)
    fb = mem.framebuffer
    screen = Screen(fb, title="Re:G - non-blocking")

    frame = 0
    t0 = time.perf_counter()
    while screen.running and frame < 300:
        shift = frame * 2
        for y in range(fb.height):
            for x in range(fb.width):
                r = (x + shift) & 0xFF
                g = (y + shift) & 0xFF
                b = 128
                fb.write_pixel(x, y, (0xFF << 24) | (b << 16) | (g << 8) | r)
        screen.pump()
        frame += 1
        time.sleep(1 / 60)
    dt = time.perf_counter() - t0
    print(f"{frame} frames in {dt:.2f}s ({frame/dt:.1f} fps)")
    screen.close()