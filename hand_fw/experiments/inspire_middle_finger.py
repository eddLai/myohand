#!/usr/bin/env python3
"""Inspire RH56F1 minimal serial control: probe -> open -> middle finger -> verify."""
import serial, struct, sys, time

HAND_ID = 1
PORTS = ['/dev/ttyUSB0', '/dev/ttyACM0']
BAUDS = [115200, 57600, 19200, 9600]

REG_ANGLE_SET = 1486
REG_FORCE_SET = 1498
REG_SPEED_SET = 1522
REG_ANGLE_ACT = 1546


def frame_write(addr, data_bytes):
    b = bytearray([0xEB, 0x90, HAND_ID, len(data_bytes) + 3, 0x12,
                   addr & 0xFF, (addr >> 8) & 0xFF])
    b += bytes(data_bytes)
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


def tx(ser, f, label, wait=0.15):
    ser.reset_input_buffer()
    ser.write(f)
    ser.flush()
    time.sleep(wait)
    r = ser.read(64)
    print(f"{label}: TX {f.hex(' ')}")
    print(f"{' ' * len(label)}  RX {r.hex(' ') if r else '(none)'}")
    return r


def parse_angles(r):
    for hdr in (b'\x90\xeb', b'\xeb\x90'):
        i = r.find(hdr)
        if i >= 0 and len(r) >= i + 7 + 12:
            return struct.unpack('<6h', r[i + 7:i + 19])
    return None


def probe():
    for port in PORTS:
        for baud in BAUDS:
            try:
                ser = serial.Serial(port, baud, timeout=0.5)
            except Exception as e:
                print(f"skip {port}: {e}")
                break
            r = tx(ser, frame_read(REG_ANGLE_ACT, 12), f"probe {port}@{baud}")
            if r:
                print(f"** hand answered on {port} @ {baud}")
                return ser
            ser.close()
    return None


ser = probe()
if ser is None:
    print("FAIL: no response on any port/baud")
    sys.exit(1)

tx(ser, frame_write(REG_SPEED_SET, angles([400] * 6)), 'speed 400')
tx(ser, frame_write(REG_FORCE_SET, angles([300] * 6)), 'force 300')

tx(ser, frame_write(REG_ANGLE_SET, angles([1000] * 6)), 'open hand')
time.sleep(2.5)

# order: [pinky, ring, middle, index, thumb_bend, thumb_rot]; -1 = leave thumb_rot
tx(ser, frame_write(REG_ANGLE_SET, angles([0, 0, 1000, 0, 0, -1])), 'MIDDLE FINGER')
time.sleep(2.5)

r = tx(ser, frame_read(REG_ANGLE_ACT, 12), 'verify angleAct')
vals = parse_angles(r) if r else None
if vals:
    names = ['pinky', 'ring', 'middle', 'index', 'thumb_bend', 'thumb_rot']
    print('actual angles:', dict(zip(names, vals)))
ser.close()
