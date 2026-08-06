import pysoem

m = pysoem.Master()
m.open("enp59s0f1")
m.config_init()
s = m.slaves[0]
print("slave attrs:", [a for a in dir(s) if not a.startswith("__")])

raw = bytearray()
for w in range(0, 0x200, 2):
    raw += s.eeprom_read(w)

off = 0x80
while off + 4 <= len(raw):
    ctype = int.from_bytes(raw[off:off+2], "little")
    clen = int.from_bytes(raw[off+2:off+4], "little")
    if ctype == 0xFFFF:
        break
    name = {10:"STRINGS",20:"DTYPES",30:"GENERAL",40:"FMMU",41:"SYNCM",50:"TXPDO",51:"RXPDO"}.get(ctype, str(ctype))
    print(f"cat {name}({ctype}) len {clen*2}B @byte {off+4}")
    if ctype == 41:
        d = raw[off+4:off+4+clen*2]
        for i in range(0, len(d), 8):
            e = d[i:i+8]
            if len(e) < 8: break
            start = int.from_bytes(e[0:2], "little")
            ln = int.from_bytes(e[2:4], "little")
            print(f"  SM{i//8}: start=0x{start:04X} len={ln} ctrl=0x{e[4]:02X} en={e[6]} type={e[7]}")
    if ctype == 40:
        print("  FMMU:", raw[off+4:off+4+clen*2].hex(chr(32)))
    off += 4 + clen*2
m.close()
