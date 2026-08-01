#!/usr/bin/env python3
"""Read back Inspire RH56 register map to diagnose all -1 angleAct."""
import serial, struct, time

HAND_ID = 1
REGS = [  # (name, addr, nbytes, fmt: h=int16 list, b=byte list)
    ('HAND_ID/redu', 1000, 2, 'b'),
    ('ANGLE_SET',    1486, 12, 'h'),
    ('FORCE_SET',    1498, 12, 'h'),
    ('SPEED_SET',    1522, 12, 'h'),
    ('POS_ACT',      1534, 12, 'h'),
    ('ANGLE_ACT',    1546, 12, 'h'),
    ('FORCE_ACT',    1582, 12, 'h'),
    ('CURRENT',      1594, 12, 'h'),
    ('ERROR',        1606, 6,  'b'),
    ('STATUS',       1612, 6,  'b'),
    ('TEMP',         1618, 6,  'b'),
]


def frame_read(addr, nbytes):
    b = bytearray([0xEB, 0x90, HAND_ID, 4, 0x11,
                   addr & 0xFF, (addr >> 8) & 0xFF, nbytes])
    b.append(sum(b[2:]) & 0xFF)
    return bytes(b)


ser = serial.Serial('/dev/ttyUSB0', 115200, timeout=0.5)
for name, addr, n, fmt in REGS:
    ser.reset_input_buffer()
    ser.write(frame_read(addr, n))
    ser.flush()
    time.sleep(0.12)
    r = ser.read(64)
    i = r.find(b'\x90\xeb')
    if i < 0 or len(r) < i + 7 + n:
        print(f"{name:10s} @{addr}: no/short reply  RX {r.hex(' ') if r else '(none)'}")
        continue
    payload = r[i + 7:i + 7 + n]
    if fmt == 'h':
        vals = list(struct.unpack(f'<{n//2}h', payload))
    else:
        vals = list(payload)
    print(f"{name:10s} @{addr}: {vals}")
ser.close()
