import pysoem

m = pysoem.Master()
m.open("enp59s0f1")
m.config_init()
s = m.slaves[0]

raw = bytearray()
for w in range(0, 0x400, 2):
    try:
        raw += s.eeprom_read(w)
    except Exception as e:
        print("eeprom stop at", hex(w), e)
        break
print("EEPROM bytes:", len(raw))

# strings category first: build string table
strings = [""]
off = 0x80
cats = []
while off + 4 <= len(raw):
    ctype = int.from_bytes(raw[off:off+2], "little")
    clen = int.from_bytes(raw[off+2:off+4], "little")
    if ctype == 0xFFFF:
        break
    cats.append((ctype, off+4, clen*2))
    off += 4 + clen*2
for ctype, o, ln in cats:
    if ctype == 10:  # STRINGS
        d = raw[o:o+ln]
        n = d[0]; p = 1
        for i in range(n):
            L = d[p]; strings.append(d[p+1:p+1+L].decode(errors="replace")); p += 1+L
print("strings:", len(strings)-1)

def sname(i):
    return strings[i] if 0 < i < len(strings) else f"str{i}"

for ctype, o, ln in cats:
    label = {40:"GENERAL",41:"FMMU",50:"TXPDO",51:"RXPDO",30:"DTYPES",60:"SM?"}.get(ctype, str(ctype))
    if ctype in (50, 51):
        d = raw[o:o+ln]
        p = 0
        while p + 8 <= len(d):
            idx = int.from_bytes(d[p:p+2], "little")
            n_e = d[p+2]; sm = d[p+3]; nm = d[p+5] if False else d[p+5]
            # header: index(2) entries(1) SM(1) DC(1) nameidx(1) flags(2) = 8
            nameidx = d[p+5]
            print(f"[{label}] PDO 0x{idx:04X} sm={sm} entries={n_e} name={sname(nameidx)}")
            p += 8
            for e in range(n_e):
                if p + 8 > len(d): break
                ei = int.from_bytes(d[p:p+2], "little")
                esub = d[p+2]; enm = d[p+3]; edt = d[p+4]; ebl = d[p+5]
                print(f"    entry 0x{ei:04X}:{esub} name={sname(enm)} bits={ebl}")
                p += 8
    elif ctype == 60 or ctype == 41 or ctype == 40:
        pass
m.close()
