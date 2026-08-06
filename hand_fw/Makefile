SOEM = soem_build/SOEM
BUILD = soem_build/build
CFLAGS = -O2 -I $(SOEM)/include -I $(BUILD)/include -I $(SOEM)/osal -I $(SOEM)/osal/linux -I $(SOEM)/oshw/linux

all: hand_ctl hand_set

hand_ctl: hand_ctl.c hand_safety.c hand_safety.h $(BUILD)/libsoem.a
	gcc $(CFLAGS) hand_ctl.c hand_safety.c -o hand_ctl $(BUILD)/libsoem.a -lpthread -lrt

hand_set: soem_build/hand_set.c hand_safety.c hand_safety.h $(BUILD)/libsoem.a
	gcc $(CFLAGS) -I . soem_build/hand_set.c hand_safety.c -o soem_build/hand_set $(BUILD)/libsoem.a -lpthread -lrt

cap: hand_ctl hand_set
	sudo setcap cap_net_raw,cap_net_admin+eip hand_ctl
	sudo setcap cap_net_raw,cap_net_admin+eip soem_build/hand_set

.PHONY: all cap hand_set

test: test_safety.c hand_safety.c hand_safety.h
	gcc -I . test_safety.c hand_safety.c -o test_safety

.PHONY: test
