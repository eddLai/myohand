SOEM = soem_build/SOEM
BUILD = soem_build/build
EXP = experiments
CFLAGS = -O2 -I $(SOEM)/include -I $(BUILD)/include -I $(SOEM)/osal -I $(SOEM)/osal/linux -I $(SOEM)/oshw/linux
LDLIBS = $(BUILD)/libsoem.a -lpthread -lrt

all: hand_ctl hand_set handd

# SOEM is a vendored clone and gitignored, so a fresh checkout does not have
# it and every target below is unbuildable until it does. Without this rule
# make says "No rule to make target 'soem_build/build/libsoem.a'", which
# names the missing file and nothing about where it comes from - and the
# thing that sends people here, run_teleop.sh, used to tell them to run this
# very make. Say what to do instead.
#
# The cmake version is load-bearing: SOEM needs >= 3.28 and the system cmake
# is older than that on at least one of our machines, which is why
# requirements.txt pins one into the venv and setup.sh puts it on PATH first.
$(BUILD)/libsoem.a:
	@echo "" >&2
	@echo "SOEM is not built. It is a vendored clone and gitignored, so a" >&2
	@echo "fresh checkout never has it. From the repo root:" >&2
	@echo "" >&2
	@echo "  export PATH=\"<the venv>/bin:\$$PATH\"   # SOEM needs cmake >= 3.28" >&2
	@echo "  git clone https://github.com/OpenEtherCATsociety/SOEM.git \\" >&2
	@echo "        hand_fw/soem_build/SOEM" >&2
	@echo "  cmake -S hand_fw/soem_build/SOEM -B hand_fw/soem_build/build \\" >&2
	@echo "        -DCMAKE_BUILD_TYPE=Release -DSOEM_BUILD_SAMPLES=OFF" >&2
	@echo "  cmake --build hand_fw/soem_build/build -j\$$(nproc)" >&2
	@echo "" >&2
	@echo "then: make -C hand_fw all && sudo make -C hand_fw cap" >&2
	@echo "" >&2
	@echo "../setup.sh does all of it including the venv - but read its" >&2
	@echo "header first, it must not be run on the KD240." >&2
	@echo "" >&2
	@false

hand_ctl: hand_ctl.c hand_safety.c hand_safety.h hand_collision_table.h $(BUILD)/libsoem.a
	gcc $(CFLAGS) hand_ctl.c hand_safety.c -o hand_ctl $(LDLIBS)

handd: handd.c hand_safety.c hand_safety.h hand_collision_table.h $(BUILD)/libsoem.a
	gcc $(CFLAGS) -I . handd.c hand_safety.c -o handd $(LDLIBS)

# The file produced is soem_build/hand_set, so that is what the rule has to
# be named. A target called `hand_set` never appears on disk, so make never
# finds it and runs the recipe on every single build - and because setcap
# lives on the inode, every one of those quietly drops the binary's
# capabilities. Being declared .PHONY made that permanent rather than a bug:
# `make all` could never be a no-op, so "rebuild only if something changed"
# was not advice anyone could follow. `hand_set` stays as an alias.
hand_set: soem_build/hand_set

soem_build/hand_set: soem_build/hand_set.c hand_safety.c hand_safety.h hand_collision_table.h $(BUILD)/libsoem.a
	gcc $(CFLAGS) -I . soem_build/hand_set.c hand_safety.c -o soem_build/hand_set $(LDLIBS)

# Enumeration + the persistent-OP/DC probe. Kept out of `all` because they
# are experiments, but they need the same build flags, so they live here
# rather than in a shell line someone has to remember.
probe: $(EXP)/ecat_scan $(EXP)/ecat_persistent_probe $(EXP)/dc_check \
       $(EXP)/op_execute_hunt $(EXP)/watchdog_trigger \
       $(EXP)/sii_dump $(EXP)/ecat_interrogate $(EXP)/compliant_op $(EXP)/wd_pace $(EXP)/syncmode_test $(EXP)/coe_startup $(EXP)/rate_sweep $(EXP)/why_1khz

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

$(EXP)/why_1khz: $(EXP)/why_1khz.c hand_safety.c hand_safety.h $(BUILD)/libsoem.a
	gcc $(CFLAGS) -I . $(EXP)/why_1khz.c hand_safety.c -o $@ $(LDLIBS)

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
	sudo setcap cap_net_raw,cap_net_admin+eip $(EXP)/why_1khz

.PHONY: all cap cap-probe probe hand_set

test: test_safety.c hand_safety.c hand_safety.h hand_collision_table.h
	gcc -I . test_safety.c hand_safety.c -o test_safety

.PHONY: test
