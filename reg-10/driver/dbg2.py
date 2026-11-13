import sys
sys.path.insert(0, 'c:\\lightly\\reg-10\\kernel\\ISA')
sys.path.insert(0, 'c:\\lightly\\reg-10\\driver')
import reg1010_driver as d
import assembler as asm
import struct

def peek(fb, x, y, w=320, base=0x10000):
    return struct.unpack_from('<I', fb, base + y * w * 4 + x * 4)[0]

dev = d.open_device(0)
print(f"fb={dev.fb_width}x{dev.fb_height} ram_size={len(dev.global_ram)}")

src = open('c:\\lightly\\reg-10\\driver\\kernels\\gradient.s').read()
code = asm.assemble(src)
dev.global_ram[0x4000:0x4000+len(code)] = code
dev.ring.dispatch(1,1,1,1,1, 0x4000, 0x5000, 0)
print(f"ring tail={dev.ring.tail:#x}")
c = dev.execute(max_cycles=200_000)
print(f"cycles={c}")
print(f"pixel(0,0)={hex(peek(dev.global_ram, 0, 0))} pixel(319,239)={hex(peek(dev.global_ram, 319, 239))}")