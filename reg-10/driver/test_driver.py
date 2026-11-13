import reg1010_driver as d

dev = d.open_device(0)
print('Re:G 1010 device opened')
print(f'  vendor=0x{dev.vendor_id:04x} device=0x{dev.device_id:04x} rev={dev.revision}')
print(f'  fb={dev.fb_width}x{dev.fb_height} cores={dev.num_cores} warps/core={dev.warps_per_core}')
print(f'  fb_size={dev.fb_size()} bytes')
fid = dev.new_fence()
print(f'  fence={fid} signalled={dev.is_fence_signalled(fid)}')
dev.signal_fence(fid)
print(f'  after signal: {dev.is_fence_signalled(fid)}')

# Test command ring
dev.ring.dispatch(1, 1, 1, 1, 1, 0x1000, 0x2000, 0)
dev.ring.copy(d.DMAChannel.G2F, 0x3000, 0, 1024, fid)
dev.ring.fence(fid)
dev.ring.present()
dev.ring.flush_to_device()
print(f'  ring tail={dev.ring.tail:#x}')

# Test allocator
buf = dev.allocator.alloc(4096)
dev.allocator.upload(buf, b'\\x00' * 16, 0)
print(f'  allocated buffer at {buf.addr:#x} size={buf.size}')

print('Driver OK')