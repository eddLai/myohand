import pysoem

m = pysoem.Master()
m.open("enp59s0f1")
m.config_init()
s = m.slaves[0]
m.read_state()
code = getattr(s, "al_status", None)
print("AL state:", hex(s.state), "al_status_code:", hex(code) if code is not None else "n/a")
try:
    print("  meaning:", pysoem.al_status_code_to_string(code))
except Exception:
    pass

raw = bytearray()
for w in range(0, 0x100, 2):
    try:
        raw += s.eeprom_read(w)
    except Exception as e:
        print("eeprom read stop at word", w, e)
        break
print("EEPROM bytes:", len(raw))
print(raw.hex(chr(32)))
# extract ASCII strings >= 4 chars
cur = b""
strs = []
for b in raw:
    if 32 <= b < 127:
        cur += bytes([b])
    else:
        if len(cur) >= 4:
            strs.append(cur.decode())
        cur = b""
if len(cur) >= 4:
    strs.append(cur.decode())
print("strings:", strs)
m.close()
