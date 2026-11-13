import sys
sys.path.insert(0, 'c:\\lightly\\reg-10\\kernel\\ISA')
sys.path.insert(0, 'c:\\lightly\\reg-10\\driver')
import reg1010_driver as d
import assembler as asm
import struct

dev = d.open_device(0)
dev.fb_width = 320
dev.fb_height = 240
print(f"fb={dev.fb_width}x{dev.fb_height} ram_size={len(dev.global_ram)}")

src = open('c:\\lightly\\reg-10\\driver\\kernels\\gradient.s').read()
code = asm.assemble(src)
dev.global_ram[0x4000:0x4000+len(code)] = code
dev.ring.dispatch(1,1,1,1,1, 0x4000, 0x5000, 0)

c = dev.execute(max_cycles=200_000)
print(f"cycles={c}")

def peek(x, y, w=320, base=0x10000):
    off = base + y * w * 4 + x * 4
    return struct.unpack_from('<I', dev.global_ram, off)[0]

print(f"pixel(0,0)={hex(peek(0,0))} pixel(160,120)={hex(peek(160,120))} pixel(319,239)={hex(peek(319,239))}")