"""
rasterizer.py - Software rasterizer for Re:G.

Pipeline:
  1. Vertex input: screen-space float2 positions + per-vertex attributes
  2. Triangle setup: edge equations, bounding box, winding
  3. Tile dispatch: split the bounding box into 8x8 tiles and hand
     each tile to a warp as a work item
  4. Fragment shading: each lane of the warp covers one pixel in the tile,
     runs the fragment program, and writes to the framebuffer

The rasterizer itself is Python-driven; the per-pixel work is expected
to be done by a SIMT kernel (a fragment shader) running on the cores.
The rasterizer's job is to *enumerate* fragments efficiently and pack
them into warp-sized work items.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Optional

TILE_W = 8
TILE_H = 8
LANES = 32


# ============================================================
# VERTEX / TRIANGLE
# ============================================================

@dataclass(slots=True)
class Vertex:
    x: float
    y: float
    # arbitrary attributes, interleaved: r, g, b, a, u, v, ...
    attrs: tuple = ()


@dataclass(slots=True)
class Triangle:
    v0: Vertex
    v1: Vertex
    v2: Vertex


# ============================================================
# EDGE EQUATIONS
# ============================================================

@dataclass(slots=True)
class EdgeEq:
    """Edge function: E(x,y) = A*x + B*y + C, positive = inside."""
    a: float
    b: float
    c: float

    def eval(self, x: float, y: float) -> float:
        return self.a * x + self.b * y + self.c


def edge_eq(v0: Vertex, v1: Vertex) -> EdgeEq:
    a = v0.y - v1.y
    b = v1.x - v0.x
    c = v0.x * v1.y - v1.x * v0.y
    return EdgeEq(a, b, c)


# ============================================================
# TRIANGLE SETUP
# ============================================================

@dataclass(slots=True)
class SetupTriangle:
    v0: Vertex
    v1: Vertex
    v2: Vertex
    area: float
    e0: EdgeEq
    e1: EdgeEq
    e2: EdgeEq
    min_x: int
    min_y: int
    max_x: int
    max_y: int
    backface: bool


def setup_triangle(tri: Triangle, fb_w: int, fb_h: int) -> Optional[SetupTriangle]:
    """Compute edge equations and bounding box. Returns None if culled."""
    v0, v1, v2 = tri.v0, tri.v1, tri.v2

    area = (v1.x - v0.x) * (v2.y - v0.y) - (v2.x - v0.x) * (v1.y - v0.y)
    if area == 0.0:
        return None

    # Snap to pixel centers (+0.5 for fill rules)
    min_x = max(0, int(min(v0.x, v1.x, v2.x)))
    min_y = max(0, int(min(v0.y, v1.y, v2.y)))
    max_x = min(fb_w - 1, int(max(v0.x, v1.x, v2.x)) + 1)
    max_y = min(fb_h - 1, int(max(v0.y, v1.y, v2.y)) + 1)

    if min_x > max_x or min_y > max_y:
        return None

    e0 = edge_eq(v1, v2)
    e1 = edge_eq(v2, v0)
    e2 = edge_eq(v0, v1)

    return SetupTriangle(
        v0=v0, v1=v1, v2=v2,
        area=area, e0=e0, e1=e1, e2=e2,
        min_x=min_x, min_y=min_y, max_x=max_x, max_y=max_y,
        backface=(area < 0),
    )


# ============================================================
# TILE WORK ITEM
# ============================================================

@dataclass(slots=True)
class TileWork:
    """One tile handed to a warp. One lane per pixel."""
    tri: SetupTriangle
    tile_x: int     # tile origin in pixels
    tile_y: int
    tile_w: int
    tile_h: int
    # precomputed per-lane edge values at pixel centers (float[32])
    lanes: list = field(default_factory=list)

    def __post_init__(self):
        tri = self.tri
        px0 = self.tile_x
        py0 = self.tile_y
        lanes = []
        for ly in range(self.tile_h):
            for lx in range(self.tile_w):
                px = px0 + lx + 0.5
                py = py0 + ly + 0.5
                if len(lanes) >= LANES:
                    break
                inside = (tri.e0.eval(px, py) >= 0 and
                          tri.e1.eval(px, py) >= 0 and
                          tri.e2.eval(px, py) >= 0)
                lanes.append((px, py, inside))
            if len(lanes) >= LANES:
                break
        # pad to LANES with inactive
        while len(lanes) < LANES:
            lanes.append((0.0, 0.0, False))
        self.lanes = lanes


# ============================================================
# RASTERIZER
# ============================================================

class Rasterizer:
    def __init__(self, fb_w: int, fb_h: int):
        self.fb_w = fb_w
        self.fb_h = fb_h
        self.tiles_emitted = 0
        self.triangles = 0
        self.fragments = 0

    def rasterize(self, triangles: list[Triangle]) -> list[TileWork]:
        """Turn a triangle list into a flat list of TileWork items."""
        work: list[TileWork] = []
        for tri in triangles:
            st = setup_triangle(tri, self.fb_w, self.fb_h)
            if st is None:
                continue
            self.triangles += 1
            for ty in range(st.min_y, st.max_y + 1, TILE_H):
                for tx in range(st.min_x, st.max_x + 1, TILE_W):
                    tw = min(TILE_W, st.max_x - tx + 1)
                    th = min(TILE_H, st.max_y - ty + 1)
                    tw_work = TileWork(st, tx, ty, tw, th)
                    # quick reject: if no lane inside, skip
                    if not any(inside for _, _, inside in tw_work.lanes):
                        continue
                    work.append(tw_work)
                    self.tiles_emitted += 1
                    self.fragments += sum(1 for _, _, inside in tw_work.lanes if inside)
        return work


# ============================================================
# SMOKE TEST
# ============================================================

if __name__ == "__main__":
    tri = Triangle(
        Vertex(10, 10, (1.0, 0.0, 0.0, 1.0)),
        Vertex(200, 10, (0.0, 1.0, 0.0, 1.0)),
        Vertex(100, 200, (0.0, 0.0, 1.0, 1.0)),
    )
    rast = Rasterizer(320, 240)
    work = rast.rasterize([tri])
    print(f"triangles: {rast.triangles}, tiles: {rast.tiles_emitted}, "
          f"fragments: {rast.fragments}")
    assert rast.tiles_emitted > 0
    print("all tests passed")