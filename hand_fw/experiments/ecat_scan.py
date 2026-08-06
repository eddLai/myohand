import pysoem
m = pysoem.Master()
m.open("enp59s0f1")
n = m.config_init()
print("EtherCAT slaves found:", n)
for i, s in enumerate(m.slaves):
    print(f"  slave {i}: man=0x{s.man:08x} id=0x{s.id:08x} rev=0x{s.rev:08x} name={s.name}")
m.close()
