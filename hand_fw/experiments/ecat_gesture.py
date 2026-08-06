import pysoem, struct, time, sys

m = pysoem.Master()
m.open("enp59s0f1")
m.config_init()
s = m.slaves[0]
m.config_map()
m.state_check(pysoem.SAFEOP_STATE, 500000)
m.read_state()
print("after map, AL state:", hex(s.state), "out", len(s.output), "B in", len(s.input), "B")

nout = len(s.output) // 2

def set_out(enable, angles, force=300, speed=400):
    vals = [enable] + list(angles) + [force]*6 + [speed]*6
    vals = vals[:nout] + [0]*(nout-len(vals) if nout>len(vals) else 0)
    packed = b"".join(struct.pack("<h", v if v>=0 else -1) for v in vals[:nout])
    s.output = packed

def show_fb(tag):
    d = bytes(s.input)
    if len(d) >= 24:
        pos = struct.unpack("<6h", d[0:12]); ang = struct.unpack("<6h", d[12:24])
        extra = ""
        if len(d) >= 84:
            err = struct.unpack("<6h", d[48:60]); st = struct.unpack("<6h", d[60:72])
            extra = f" err={list(err)} status={list(st)}"
        print(f"{tag}: POSACT={list(pos)} ANGLEACT={list(ang)}{extra}")
    else:
        print(tag, "input:", d.hex(chr(32)))

set_out(0, [-1]*6)
m.state = pysoem.OP_STATE
m.write_state()
reached = False
for i in range(500):
    m.send_processdata(); m.receive_processdata(2000)
    if i % 50 == 0:
        m.read_state()
        if s.state == pysoem.OP_STATE:
            reached = True; break
    time.sleep(0.002)
m.read_state()
print("OP reached:", reached, "AL state:", hex(s.state), "al_status:", hex(getattr(s,"al_status",0)))
if not reached:
    m.close(); sys.exit(1)

def run(label, enable, angles, secs):
    set_out(enable, angles)
    t0 = time.time()
    while time.time() - t0 < secs:
        m.send_processdata(); m.receive_processdata(2000)
        time.sleep(0.005)
    show_fb(label)

run("enable only", 1, [-1]*6, 1.0)
run("open hand  ", 1, [1000]*6, 3.0)
run("MIDDLE FNGR", 1, [0,0,1000,0,0,-1], 3.0)
m.close()
