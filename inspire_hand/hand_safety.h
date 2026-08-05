/* hand_safety - driver-level joint interlock for the Inspire RH56F1.
 *
 * Every control binary routes its targets through hs_interlock() before
 * they reach the PDO, so no caller (Python, teleop, HTTP, ad-hoc script)
 * can command a pose that jams the mechanism. Guards clamp rather than
 * reject: a streaming teleop source must degrade to a safe pose, not
 * fail. Held axes (-1) are judged by live ANGLEACT, not by the absent
 * command value.
 */
#ifndef HAND_SAFETY_H
#define HAND_SAFETY_H

#include <stdint.h>
#include <stddef.h>

enum { AX_PINKY = 0, AX_RING, AX_MIDDLE, AX_INDEX, AX_THUMB_BEND, AX_THUMB_ROT };

/* PDO output layout: [0]=enable, [1..6]=targets, [7..12]=force, [13..18]=speed */
#define HS_OUT_TARGET 1
#define HS_OUT_FORCE  7
#define HS_OUT_SPEED  13

/* Exclusive bus lock: two EtherCAT masters on one NIC make the slave
   refuse OPERATIONAL. Blocks up to timeout_s. Returns fd, or -1.
   Note the lock is per-host: it cannot stop a master on another machine
   from grabbing the same slave through the lab switch. */
int  hs_lock(int timeout_s);
void hs_unlock(int fd);

/* NIC the master opens, from $ECAT_IFACE, else HS_IFACE_DEFAULT.
   The hand has answered on a different interface on every host that has
   driven it (eno1 on .28, enp17s0 on .112, eth0 or the PL-backed eth1 on
   the KD240 depending on where the cable is), so this must not be baked
   into the binary. Enumerate with ecat_scan rather than guessing. */
#ifndef HS_IFACE_DEFAULT
#define HS_IFACE_DEFAULT "eth0"
#endif
const char *hs_iface(void);

/* ANGLEACT (~890 closed .. ~1850 open) mapped onto the 0..2000 target scale. */
int16_t hs_ang_to_target(int16_t ang);

/* Clamp targets against mechanical interference. Returns the number of
   axes adjusted and appends a human-readable note to why[]. */
int hs_interlock(int16_t *tgt, const int16_t *ang, char *why, size_t n);

/* Relieve axes left stalling by a previous execution (STA 5/6 or
   sustained current). Runs at session start, so a stall clears within
   one teleop tick instead of cooking the actuator. */
int hs_stall_relief(int16_t *tgt, const int16_t *cur, const int16_t *sta,
                    const int16_t *ang, char *why, size_t n);

/* Per-axis force/speed profile: thumb-bend needs a limit above its
   1300-1857 g phantom reading or the firmware force-stops it instantly,
   and gets a lower speed to offset that headroom. */
void hs_profile(int16_t *out, int force, int speed);

#endif
