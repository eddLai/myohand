#!/usr/bin/env python3
"""Command open<->middle-finger while sampling candidate feedback regs.
If any int16 tracks the commanded motion, that's the F1 feedback block."""
import serial, struct, time

HAND_ID = 1
REG_ANGLE_SET = 1486
BLOCKS = [(1060, 32), (1092, 32), (1546, 12)]  # candidates + legacy ANGLE_ACT


def frame_write(addr, data):
    b = bytearray([0xEB, 0x90, HAND_ID, len(data) + 3, 0x12,
                   addr & 0xFF, (addr >> 8) & 0xFF]) + bytes(data)
    b.append(sum(b[2:]) & 0xFF)
    return bytes(b)


def frame_read(addr, nbytes):
    b = bytearray([0xEB, 0x90, HAND_ID, 4, 0x11,
                   addr & 0xFF, (addr >> 8) & 0xFF, nbytes])
    b.append(sum(b[2:]) & 0xFF)
    return bytes(b)


def angles(vals):
    d = []
    for v in vals:
        if v < 0:
            v = 0xFFFF
        d += [v & 0xFF, (v >> 8) & 0xFF]
    return d


ser = serial.Serial('/dev/ttyUSB0', 115200, timeout=0.3)


def rw(f):
    ser.reset_input_buffer()
    ser.write(f)
    ser.flush()
    time.sleep(0.05)
    return ser.read(80)


def sample():
    out = {}
    for addr, n in BLOCKS:
        r = rw(frame_read(addr, n))
        i = r.find(b'\x90\xeb')
        if i >= 0 and len(r) >= i + 7 + n:
            out[addr] = list(struct.unpack(f'<{n//2}h', r[i + 7:i + 7 + n]))
        else:
            out[addr] = None
    return out


def collect(label, seconds=2.5):
    t0 = time.time()
    series = []
    while time.time() - t0 < seconds:
        series.append(sample())
    print(f"\n== {label}: last sample ==")
    last = series[-1]
    for addr in last:
        print(f"  @{addr}: {last[addr]}")
    return series


collect('baseline', 1.0)
rw(frame_write(REG_ANGLE_SET, angles([1000] * 6)))
s_open = collect('OPEN cmd', 3.0)
rw(frame_write(REG_ANGLE_SET, angles([0, 0, 1000, 0, 0, -1])))
s_mid = collect('MIDDLE FINGER cmd', 3.0)

# which int16 positions changed between end-of-open and end-of-mid?
print("\n== moved slots (|delta| > 15) ==")
a, b = s_open[-1], s_mid[-1]
for addr in a:
    if a[addr] and b[addr]:
        for k, (x, y) in enumerate(zip(a[addr], b[addr])):
            if abs(x - y) > 15:
                print(f"  addr {addr + 2*k}: {x} -> {y}")
ser.close()
