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
probe: $(EXP)/ecat_scan $(EXP)/ecat_persistent_probe $(EXP)/dc_check \
       $(EXP)/op_execute_hunt $(EXP)/watchdog_trigger \
       $(EXP)/sii_dump $(EXP)/ecat_interrogate $(EXP)/compliant_op $(EXP)/wd_pace $(EXP)/syncmode_test $(EXP)/coe_startup $(EXP)/rate_sweep

$(EXP)/ecat_scan: $(EXP)/ecat_scan.c hand_safety.c hand_safety.h $(BUILD)/libsoem.a
	gcc $(CFLAGS) -I . $(EXP)/ecat_scan.c hand_safety.c -o $@ $(LDLIBS)

$(EXP)/ecat_persistent_probe: $(EXP)/ecat_persistent_probe.c hand_safety.c hand_safety.h $(BUILD)/libsoem.a
	gcc $(CFLAGS) -I . $(EXP)/ecat_persistent_probe.c hand_safety.c -o $@ $(LDLIBS)

$(EXP)/dc_check: $(EXP)/dc_check.c hand_safety.c hand_safety.h $(BUILD)/libsoem.a
	gcc $(CFLAGS) -I . $(EXP)/dc_check.c hand_safety.c -o $@ $(LDLIBS)

$(EXP)/op_execute_hunt: $(EXP)/op_execute_hunt.c hand_safety.c hand_safety.h $(BUILD)/libsoem.a
	gcc $(CFLAGS) -I . $(EXP)/op_execute_hunt.c hand_safety.c -o $@ $(LDLIBS)

$(EXP)/watchdog_trigger: $(EXP)/watchdog_trigger.c hand_safety.c hand_safety.h $(BUILD)/libsoem.a
	gcc $(CFLAGS) -I . $(EXP)/watchdog_trigger.c hand_safety.c -o $@ $(LDLIBS)

$(EXP)/sii_dump: $(EXP)/sii_dump.c hand_safety.c hand_safety.h $(BUILD)/libsoem.a
	gcc $(CFLAGS) -I . $(EXP)/sii_dump.c hand_safety.c -o $@ $(LDLIBS)

$(EXP)/ecat_interrogate: $(EXP)/ecat_interrogate.c hand_safety.c hand_safety.h $(BUILD)/libsoem.a
	gcc $(CFLAGS) -I . $(EXP)/ecat_interrogate.c hand_safety.c -o $@ $(LDLIBS)

$(EXP)/compliant_op: $(EXP)/compliant_op.c hand_safety.c hand_safety.h $(BUILD)/libsoem.a
	gcc $(CFLAGS) -I . $(EXP)/compliant_op.c hand_safety.c -o $@ $(LDLIBS)

$(EXP)/wd_pace: $(EXP)/wd_pace.c hand_safety.c hand_safety.h $(BUILD)/libsoem.a
	gcc $(CFLAGS) -I . $(EXP)/wd_pace.c hand_safety.c -o $@ $(LDLIBS)

$(EXP)/syncmode_test: $(EXP)/syncmode_test.c hand_safety.c hand_safety.h $(BUILD)/libsoem.a
	gcc $(CFLAGS) -I . $(EXP)/syncmode_test.c hand_safety.c -o $@ $(LDLIBS)

$(EXP)/coe_startup: $(EXP)/coe_startup.c hand_safety.c hand_safety.h $(BUILD)/libsoem.a
	gcc $(CFLAGS) -I . $(EXP)/coe_startup.c hand_safety.c -o $@ $(LDLIBS)

$(EXP)/rate_sweep: $(EXP)/rate_sweep.c hand_safety.c hand_safety.h $(BUILD)/libsoem.a
	gcc $(CFLAGS) -I . $(EXP)/rate_sweep.c hand_safety.c -o $@ $(LDLIBS)

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
	sudo setcap cap_net_raw,cap_net_admin+eip $(EXP)/dc_check
	sudo setcap cap_net_raw,cap_net_admin+eip $(EXP)/op_execute_hunt
	sudo setcap cap_net_raw,cap_net_admin+eip $(EXP)/watchdog_trigger
	sudo setcap cap_net_raw,cap_net_admin+eip $(EXP)/sii_dump
	sudo setcap cap_net_raw,cap_net_admin+eip $(EXP)/ecat_interrogate
	sudo setcap cap_net_raw,cap_net_admin+eip $(EXP)/compliant_op
	sudo setcap cap_net_raw,cap_net_admin+eip $(EXP)/wd_pace
	sudo setcap cap_net_raw,cap_net_admin+eip $(EXP)/syncmode_test
	sudo setcap cap_net_raw,cap_net_admin+eip $(EXP)/coe_startup
	sudo setcap cap_net_raw,cap_net_admin+eip $(EXP)/rate_sweep

.PHONY: all cap cap-probe probe hand_set

test: test_safety.c hand_safety.c hand_safety.h
	gcc -I . test_safety.c hand_safety.c -o test_safety

.PHONY: test
