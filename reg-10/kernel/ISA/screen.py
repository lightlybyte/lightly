"""
screen.py - Non-blocking tkinter display for Re:G.

Usage:
    screen = Screen(fb)
    while running:
        render_into(fb)
        screen.pump()      # one refresh + event poll, returns immediately
    screen.close()
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

        self.root = tk.Tk()
        self.root.title(title)
        self.root.resizable(False, False)

        self.canvas = tk.Canvas(
            self.root,
            width=fb.width,
            height=fb.height,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack()

        self._img = tk.PhotoImage(width=fb.width, height=fb.height)
        self.canvas.create_image(0, 0, anchor="nw", image=self._img)

        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Escape>", lambda e: self.close())

    def close(self) -> None:
        self.running = False
        try:
            self.root.destroy()
        except Exception:
            pass

    def refresh(self) -> None:
        """Copy the framebuffer into the PhotoImage."""
        w, h = self.fb.width, self.fb.height
        buf = self.fb.buffer

        header = f"P6\n{w} {h}\n255\n".encode()
        rgb = bytearray(w * h * 3)
        j = 0
        for i in range(0, len(buf), 4):
            rgb[j]     = buf[i + 2]
            rgb[j + 1] = buf[i + 1]
            rgb[j + 2] = buf[i]
            j += 3
        self._img.configure(data=header + bytes(rgb), format="ppm")

    def pump(self) -> bool:
        """
        Non-blocking: refresh the image and process pending tk events.
        Returns False when the window has been closed.
        """
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
    """Open a window and return immediately. Call screen.pump() each frame."""
    return Screen(fb, title=title)


if __name__ == "__main__":
    import time
    from ram import MemorySystem

    mem = MemorySystem(num_cores=1, fb_width=640, fb_height=480)
    fb = mem.framebuffer
    screen = Screen(fb, title="Re:G - non-blocking test")

    t0 = time.perf_counter()
    frame = 0
    while screen.running and frame < 300:
        # Animate a moving gradient
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
    print(f"ran {frame} frames in {dt:.2f}s ({frame/dt:.1f} fps)")
    screen.close()