#!/usr/bin/env python3
"""Sweep Inspire hand registers 900-1700 in 32B chunks; show non-FF regions."""
import serial, time

HAND_ID = 1


def frame_read(addr, nbytes):
    b = bytearray([0xEB, 0x90, HAND_ID, 4, 0x11,
                   addr & 0xFF, (addr >> 8) & 0xFF, nbytes])
    b.append(sum(b[2:]) & 0xFF)
    return bytes(b)


ser = serial.Serial('/dev/ttyUSB0', 115200, timeout=0.4)
for base in range(900, 1700, 32):
    ser.reset_input_buffer()
    ser.write(frame_read(base, 32))
    ser.flush()
    time.sleep(0.08)
    r = ser.read(64)
    i = r.find(b'\x90\xeb')
    if i < 0 or len(r) < i + 7 + 32:
        print(f"{base:5d}: no/short reply ({len(r)}B)")
        continue
    payload = r[i + 7:i + 7 + 32]
    tag = '' if payload.count(0xFF) > 28 else '   <-- live data'
    print(f"{base:5d}: {payload.hex(' ')}{tag}")
ser.close()
