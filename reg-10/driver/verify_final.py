import sys
sys.path.insert(0, 'c:\\lightly\\reg-10\\kernel\\ISA')
sys.path.insert(0, 'c:\\lightly\\reg-10\\driver')
import reg1010_driver as d
import assembler as asm
import struct

def peek(fb, x, y, w=320, base=0x10000):
    return struct.unpack_from('<I', fb, base + y * w * 4 + x * 4)[0]

def run_kernel(dev, name, origin=0x4000):
    src = open(f'c:\\lightly\\reg-10\\driver\\kernels\\{name}.s').read()
    code = asm.assemble(src)
    dev.global_ram[origin:origin+len(code)] = code
    dev.ring.dispatch(1,1,1,1,1, origin, 0x5000, 0)
    return dev.execute(max_cycles=200_000)

dev = d.open_device(0)
print(f"Re:G 1010 dev=0x{dev.device_id:04x} fb={dev.fb_width}x{dev.fb_height} cores={dev.num_cores}")

c1 = run_kernel(dev, 'gradient')
ok = all(
    peek(dev.global_ram, x, y) == (0x00FF8080 | (x & 0xFF) | ((y & 0xFF) << 16))
    for x, y in [(0,0),(1,0),(0,1),(160,120),(319,239),(100,50)]
)
print(f"gradient: {c1} cycles -> {'PASS' if ok else 'FAIL'}")

c2 = run_kernel(dev, 'clear')
want = 0xFF6495ED
bad = sum(1 for y in range(240) for x in range(320) if peek(dev.global_ram, x, y) != want)
print(f"clear: {c2} cycles -> {'PASS' if bad == 0 else f'FAIL ({bad} bad)'}")

print("\nALL DRIVER + KERNEL TESTS PASSED" if ok and bad == 0 else "\nSOME TESTS FAILED")