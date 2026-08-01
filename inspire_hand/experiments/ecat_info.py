import pysoem, time

m = pysoem.Master()
m.open("enp59s0f1")
n = m.config_init()
print("slaves:", n)
s = m.slaves[0]

def rd(idx, sub, label):
    try:
        v = s.sdo_read(idx, sub)
        print(f"  0x{idx:04X}:{sub} {label}: {v.hex(chr(32)) if isinstance(v,(bytes,bytearray)) else v}")
        return v
    except Exception as e:
        print(f"  0x{idx:04X}:{sub} {label}: ERR {type(e).__name__} {e}")
        return None

print("== CoE identity ==")
rd(0x1000, 0, "DeviceType")
rd(0x1008, 0, "DeviceName")
rd(0x1009, 0, "HwVersion")
rd(0x100A, 0, "SwVersion")
for sub in (1,2,3,4):
    rd(0x1018, sub, f"Identity[{sub}]")

print("== PDO assign ==")
rd(0x1C12, 0, "RxPDO count")
rd(0x1C12, 1, "RxPDO[1]")
rd(0x1C13, 0, "TxPDO count")
rd(0x1C13, 1, "TxPDO[1]")
rd(0x1600, 0, "RxPDO map entries")
rd(0x1A00, 0, "TxPDO map entries")
for sub in range(1, 9):
    rd(0x1600, sub, f"RxMap[{sub}]")
for sub in range(1, 9):
    rd(0x1A00, sub, f"TxMap[{sub}]")

print("== config_map / SAFEOP ==")
io = m.config_map()
print("iomap bytes:", io)
print("outputs:", len(s.output), "B, inputs:", len(s.input), "B")
m.state_check(pysoem.SAFEOP_STATE, 2000000)
m.read_state()
print("AL state:", hex(s.state))
time.sleep(0.3)
m.send_processdata(); m.receive_processdata(2000)
print("input PDO:", bytes(s.input).hex(chr(32)) if len(s.input) else "(empty)")
m.close()
