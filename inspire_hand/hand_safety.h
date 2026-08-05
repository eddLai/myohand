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

/* ---- the target scale, defined in exactly one place -----------------
 *
 * UNRESOLVED (2026-08-06): the command field may not be a 0..2000 scale
 * at all. Two observations point at it being an ANGLEACT-style setpoint
 * (~890..1850) instead: `hand_ctl pose ... 1000 ...` leaves the axis at
 * ANGLEACT 998, and a park target of 825 - below the ~890 closed end -
 * drove the axis to the closed stop at 896 rather than to 825. Settling
 * it needs the hand, so it stays open.
 *
 * Everything that converts between ANGLEACT and a target therefore goes
 * through the four calls below, and nothing else in the tree divides by
 * the span or clamps against a literal 0/2000. If the scale turns out to
 * be 890..1850, the edit is HS_TGT_MIN/HS_TGT_MAX plus the body of
 * hs_ang_to_target here, and every caller follows without being touched.
 * Python has one mirror of this, hand_scale.py, which cross-checks
 * itself against `hand_ctl scale` rather than restating the numbers.
 */
#define HS_TGT_MIN   0
#define HS_TGT_MAX   2000
#define HS_TGT_HOLD  (-1)   /* leave the axis wherever it currently is */

/* Clamp into range; HS_TGT_HOLD passes through as itself. */
int16_t hs_clamp_target(int16_t tgt);
/* Whether a caller-supplied value is a legal command at all. */
int     hs_target_valid(int16_t tgt);
/* ANGLEACT (~890 closed .. ~1850 open) onto the target scale, and back.
   hs_target_to_ang(HS_TGT_HOLD) returns HS_TGT_HOLD: a hold names no
   angle, and inventing one would silently turn it into a move. */
int16_t hs_ang_to_target(int16_t ang);
int16_t hs_target_to_ang(int16_t tgt);
/* The scale as JSON, so a client can verify its own copy instead of
   trusting that the two were edited together. Returns bytes written. */
int     hs_scale_json(char *buf, size_t n);

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
