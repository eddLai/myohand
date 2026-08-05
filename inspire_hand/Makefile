SOEM = soem_build/SOEM
BUILD = soem_build/build
EXP = experiments
CFLAGS = -O2 -I $(SOEM)/include -I $(BUILD)/include -I $(SOEM)/osal -I $(SOEM)/osal/linux -I $(SOEM)/oshw/linux
LDLIBS = $(BUILD)/libsoem.a -lpthread -lrt

all: hand_ctl hand_set handd

hand_ctl: hand_ctl.c hand_safety.c hand_safety.h $(BUILD)/libsoem.a
	gcc $(CFLAGS) hand_ctl.c hand_safety.c -o hand_ctl $(LDLIBS)

handd: handd.c hand_safety.c hand_safety.h $(BUILD)/libsoem.a
	gcc $(CFLAGS) -I . handd.c hand_safety.c -o handd $(LDLIBS)

hand_set: soem_build/hand_set.c hand_safety.c hand_safety.h $(BUILD)/libsoem.a
	gcc $(CFLAGS) -I . soem_build/hand_set.c hand_safety.c -o soem_build/hand_set $(LDLIBS)

# Enumeration + the persistent-OP/DC probe. Kept out of `all` because they
# are experiments, but they need the same build flags, so they live here
# rather than in a shell line someone has to remember.
probe: $(EXP)/ecat_scan $(EXP)/ecat_persistent_probe

$(EXP)/ecat_scan: $(EXP)/ecat_scan.c hand_safety.c hand_safety.h $(BUILD)/libsoem.a
	gcc $(CFLAGS) -I . $(EXP)/ecat_scan.c hand_safety.c -o $@ $(LDLIBS)

$(EXP)/ecat_persistent_probe: $(EXP)/ecat_persistent_probe.c hand_safety.c hand_safety.h $(BUILD)/libsoem.a
	gcc $(CFLAGS) -I . $(EXP)/ecat_persistent_probe.c hand_safety.c -o $@ $(LDLIBS)

# setcap must be re-applied after every rebuild - the capability lives on
# the inode, not the path.
cap: hand_ctl hand_set handd
	sudo setcap cap_net_raw,cap_net_admin+eip hand_ctl
	sudo setcap cap_net_raw,cap_net_admin+eip soem_build/hand_set
	# handd asks for two more than the others: cap_sys_nice to take
	# SCHED_FIFO and cap_ipc_lock to mlockall. Without them it still runs,
	# it just says so and stays on the ordinary scheduler.
	sudo setcap cap_net_raw,cap_net_admin,cap_sys_nice,cap_ipc_lock+eip handd

cap-probe: probe
	sudo setcap cap_net_raw,cap_net_admin+eip $(EXP)/ecat_scan
	sudo setcap cap_net_raw,cap_net_admin+eip $(EXP)/ecat_persistent_probe

.PHONY: all cap cap-probe probe hand_set

test: test_safety.c hand_safety.c hand_safety.h
	gcc -I . test_safety.c hand_safety.c -o test_safety

.PHONY: test
