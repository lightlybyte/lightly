"""
sample_app.py - Re:G 1010 sample host application.

Demonstrates the full driver flow:
  1. Open the Re:G 1010 device.
  2. Assemble a GPU ISA kernel (.s -> .bin).
  3. Upload the kernel to global RAM.
  4. DISPATCH the kernel via the command ring.
  5. Wait on the fence and present the framebuffer.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "kernel" / "ISA"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import assembler as asm  # noqa: E402
import reg1010_driver as d  # noqa: E402

KERNEL_ORIGIN = 0x4000      # where kernels live in global RAM
ARGS_ADDR     = 0x5000      # where kernel arguments live


def assemble_kernel(src_path: str, out_bin: str | None = None) -> bytes:
    src = Path(src_path).read_text(encoding="utf-8")
    code = asm.assemble(src)
    if out_bin:
        Path(out_bin).write_bytes(code)
        print(f"assembled {len(code)} bytes -> {out_bin}")
    return code


def upload_kernel(dev: d.ReG1010Device, code: bytes) -> int:
    """Copy kernel bytes into global RAM at KERNEL_ORIGIN."""
    dev.global_ram[KERNEL_ORIGIN:KERNEL_ORIGIN + len(code)] = code
    return KERNEL_ORIGIN


def write_args(dev: d.ReG1010Device, args: list[int]) -> None:
    """Write kernel argument u32s to ARGS_ADDR in global RAM."""
    for i, a in enumerate(args):
        dev.global_ram[ARGS_ADDR + i * 4:ARGS_ADDR + i * 4 + 4] = _pack_u32(a)


def run_and_present(dev: d.ReG1010Device, fb_w: int, fb_h: int) -> int:
    """Execute the command ring on the device, then present.

    Returns the number of emulator cycles executed.
    """
    cycles = dev.execute(max_cycles=20_000_000)
    print(f"  executed {cycles} emulator cycles")
    return cycles


def main() -> int:
    dev = d.open_device(0)
    print(f"Re:G 1010 device: ven=0x{dev.vendor_id:04x} dev=0x{dev.device_id:04x}")

    # Choose kernel from command line, default gradient.
    kernel_name = sys.argv[1] if len(sys.argv) > 1 else "gradient"
    kernel_path = Path(__file__).parent / "kernels" / f"{kernel_name}.s"
    if not kernel_path.exists():
        print(f"error: kernel not found: {kernel_path}", file=sys.stderr)
        return 1

    code = assemble_kernel(str(kernel_path), out_bin=str(kernel_path.with_suffix(".bin")))
    kernel_pc = upload_kernel(dev, code)
    print(f"  kernel at {kernel_pc:#x} ({len(code)} bytes)")

    # Default args: clear color if clear.s, nothing for gradient.
    if kernel_name == "clear":
        # BGRA32: cornflower blue-ish
        write_args(dev, [0xFF6495ED])

    # DISPATCH: 1x1 grid, 1x1 block, 1 warp, kernel_pc, args, no fence yet
    dev.ring.dispatch(1, 1, 1, 1, 1, kernel_pc, ARGS_ADDR, 0)
    print(f"  dispatched {kernel_name}.s")

    run_and_present(dev, dev.fb_width, dev.fb_height)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())