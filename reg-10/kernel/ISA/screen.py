"""
screen.py - Non-blocking tkinter display for Re:G.
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
            width=fb.width, height=fb.height,
            highlightthickness=0, bd=0)
        self.canvas.pack()

        self._img = tk.PhotoImage(width=fb.width, height=fb.height)
        self.canvas.create_image(0, 0, anchor="nw", image=self._img)

        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Escape>", lambda e: self.close())

    def close(self):
        self.running = False
        try:
            self.root.destroy()
        except Exception:
            pass

    def refresh(self):
        w, h = self.fb.width, self.fb.height
        src = self.fb.view()
        header = f"P6\n{w} {h}\n255\n".encode()
        rgb = bytearray(w * h * 3)
        j = 0
        for i in range(0, len(src), 4):
            rgb[j]     = src[i + 2]
            rgb[j + 1] = src[i + 1]
            rgb[j + 2] = src[i]
            j += 3
        self._img.configure(data=header + bytes(rgb), format="ppm")

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