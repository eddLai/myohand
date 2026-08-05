#include "hand_safety.h"

#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <unistd.h>

#define LOCK_PATH "/tmp/inspire_hand.bus.lock"

/* ANGLEACT span measured on this unit: fully closed ~890, fully open ~1850 */
#define ANG_CLOSED 890
#define ANG_OPEN   1850

/* The four thresholds below are positions on the target scale, read off
   the hand at the time. They are not derived from HS_TGT_MIN/MAX, so if
   the scale question (see hand_safety.h) resolves the other way they have
   to be re-measured, not rescaled by arithmetic. */
/* index and thumb-bend below this together drive the thumb into the
   index mechanism (observed on-site: STA=5 current-protection stop) */
#define CLEAR_IDX_THUMB 600
/* a curled index also blocks the thumb's palm-ward rotation sweep */
#define ROT_BLOCKED_BELOW 800
#define ROT_SAFE          1200

#define STALL_CUR  400
#define RELIEF     250

#define THUMB_BEND_FORCE 1900   /* above the phantom force reading */
#define THUMB_BEND_SPEED 500

const char *hs_iface(void)
{
   const char *e = getenv("ECAT_IFACE");
   return (e && *e) ? e : HS_IFACE_DEFAULT;
}

static int clampi(int v, int lo, int hi)
{
   return v < lo ? lo : (v > hi ? hi : v);
}

static void note(char *why, size_t n, const char *fmt, ...)
{
   size_t len = strlen(why);
   va_list ap;
   if (len + 2 >= n) return;
   if (len) { why[len++] = ';'; why[len++] = ' '; why[len] = 0; }
   va_start(ap, fmt);
   vsnprintf(why + len, n - len, fmt, ap);
   va_end(ap);
}

int hs_lock(int timeout_s)
{
   int fd = open(LOCK_PATH, O_CREAT | O_RDWR, 0666);
   int waited = 0;
   if (fd < 0) return -1;
   while (flock(fd, LOCK_EX | LOCK_NB) != 0)
   {
      if (waited++ >= timeout_s * 10) { close(fd); return -1; }
      usleep(100000);
   }
   return fd;
}

void hs_unlock(int fd)
{
   if (fd >= 0) { flock(fd, LOCK_UN); close(fd); }
}

int16_t hs_clamp_target(int16_t tgt)
{
   if (tgt == HS_TGT_HOLD) return HS_TGT_HOLD;
   return (int16_t)clampi(tgt, HS_TGT_MIN, HS_TGT_MAX);
}

int hs_target_valid(int16_t tgt)
{
   return tgt == HS_TGT_HOLD || (tgt >= HS_TGT_MIN && tgt <= HS_TGT_MAX);
}

int16_t hs_ang_to_target(int16_t ang)
{
   int t = HS_TGT_MIN + ((int)ang - ANG_CLOSED) * (HS_TGT_MAX - HS_TGT_MIN)
                        / (ANG_OPEN - ANG_CLOSED);
   return (int16_t)clampi(t, HS_TGT_MIN, HS_TGT_MAX);
}

int16_t hs_target_to_ang(int16_t tgt)
{
   int a;
   if (tgt == HS_TGT_HOLD) return HS_TGT_HOLD;   /* no angle to name */
   a = ANG_CLOSED + ((int)hs_clamp_target(tgt) - HS_TGT_MIN)
                        * (ANG_OPEN - ANG_CLOSED) / (HS_TGT_MAX - HS_TGT_MIN);
   return (int16_t)clampi(a, ANG_CLOSED, ANG_OPEN);
}

int hs_scale_json(char *buf, size_t n)
{
   return snprintf(buf, n,
                   "{\"target_min\":%d,\"target_max\":%d,\"target_hold\":%d,"
                   "\"ang_closed\":%d,\"ang_open\":%d}",
                   HS_TGT_MIN, HS_TGT_MAX, HS_TGT_HOLD, ANG_CLOSED, ANG_OPEN);
}

/* effective value of an axis: the command if given, else where it sits now */
static int eff_of(const int16_t *tgt, const int16_t *ang, int i)
{
   return tgt[i] != HS_TGT_HOLD ? tgt[i] : hs_ang_to_target(ang[i]);
}

int hs_interlock(int16_t *tgt, const int16_t *ang, char *why, size_t n)
{
   int fixed = 0, i, idx, thb, rot;

   for (i = 0; i < 6; i++) tgt[i] = hs_clamp_target(tgt[i]);

   /* thumb yields to the fingers, matching the fingers-first doctrine */
   idx = eff_of(tgt, ang, AX_INDEX);
   thb = eff_of(tgt, ang, AX_THUMB_BEND);
   if (idx < CLEAR_IDX_THUMB && thb < CLEAR_IDX_THUMB)
   {
      tgt[AX_THUMB_BEND] = CLEAR_IDX_THUMB;
      note(why, n, "thumb_bend raised to %d (index %d would clash)",
           CLEAR_IDX_THUMB, idx);
      fixed++;
   }

   idx = eff_of(tgt, ang, AX_INDEX);
   rot = eff_of(tgt, ang, AX_THUMB_ROT);
   if (idx < ROT_BLOCKED_BELOW && rot < ROT_SAFE)
   {
      tgt[AX_THUMB_ROT] = ROT_SAFE;
      note(why, n, "thumb_rot held at %d (curled index blocks the sweep)",
           ROT_SAFE);
      fixed++;
   }
   return fixed;
}

int hs_stall_relief(int16_t *tgt, const int16_t *cur, const int16_t *sta,
                    const int16_t *ang, char *why, size_t n)
{
   int relieved = 0, i;
   for (i = 0; i < 6; i++)
   {
      int here, want;
      if (sta[i] != 5 && sta[i] != 6 && cur[i] <= STALL_CUR) continue;
      here = hs_ang_to_target(ang[i]);
      want = eff_of(tgt, ang, i);
      if (want >= here + RELIEF) continue;   /* already moving clear */
      tgt[i] = hs_clamp_target((int16_t)(here + RELIEF));
      note(why, n, "axis %d relieved to %d (sta=%d cur=%dmA)",
           i, tgt[i], sta[i], cur[i]);
      relieved++;
   }
   return relieved;
}

void hs_profile(int16_t *out, int force, int speed)
{
   int i;
   for (i = 0; i < 6; i++)
   {
      out[HS_OUT_FORCE + i] = (int16_t)force;
      out[HS_OUT_SPEED + i] = (int16_t)speed;
   }
   out[HS_OUT_FORCE + AX_THUMB_BEND] = THUMB_BEND_FORCE;
   if (speed > THUMB_BEND_SPEED)
      out[HS_OUT_SPEED + AX_THUMB_BEND] = THUMB_BEND_SPEED;
}
