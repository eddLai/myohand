#!/usr/bin/env python3
"""Send one ANGLE_SET command: send_angles.py p r m i tb tr"""
import serial, sys, time

vals = [int(x) for x in sys.argv[1:7]]
b = bytearray([0xEB, 0x90, 0x01, 15, 0x12, 0xCE, 0x05])
for v in vals:
    if v < 0:
        v = 0xFFFF
    b += bytes([v & 0xFF, (v >> 8) & 0xFF])
b.append(sum(b[2:]) & 0xFF)
ser = serial.Serial('/dev/ttyUSB0', 115200, timeout=0.4)
ser.write(bytes(b))
ser.flush()
time.sleep(0.15)
print('RX', ser.read(32).hex(' '))
ser.close()
