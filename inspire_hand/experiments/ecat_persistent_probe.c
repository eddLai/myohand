/* ecat_persistent_probe - does the RH56F1 need a disconnect to move?
 *
 * hand_ctl / hand_set write a pose and exit, on the assumption that the
 * firmware only applies the target once the master disconnects (SM
 * watchdog). hand_op3..hand_op6 in this directory already held the link
 * open and saw no motion, but every one of them predates the STATUS=7
 * standby discovery and so never woke the axes first - their "no motion"
 * may only mean "the hand was asleep". This probe reruns that test with
 * hand_ctl's wake sequence in front of it, which is the one variable the
 * earlier runs were missing.
 *
 * Sequence: bring-up -> wake (verbatim from hand_ctl) -> oscillate one
 * axis while holding OPERATIONAL -> park a target clearly away from the
 * start pose -> close -> wait -> reconnect and read ANGLEACT again. Both
 * outcomes are therefore observable in a single run: motion during the
 * open window, or motion that only appears after the disconnect.
 *
 * Every sample is buffered in RAM and dumped after the link is down -
 * printing inside the PDO loop can stall a cycle past the SM watchdog,
 * which is the very mechanism under test.
 *
 * Usage: ecat_persistent_probe <iface> [axis 0-5] [duration_s]
 *   axis: 0=pinky 1=ring 2=middle(default) 3=index 4=thumb_bend 5=thumb_rot
 * See PERSISTENT_OP_PLAN.md for how to read the output.
 */
#include "soem/soem.h"
#include "hand_safety.h"
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>
#include <time.h>

#define WAKE_MS_MAX  12000   /* same budget hand_ctl gives the wake wiggle */
#define OSC_HALF_MS  500     /* dwell at each end of the oscillation */
#define LOG_EVERY_MS 20      /* sample cadence; PDO still cycles at 1 kHz */
#define AMP          300     /* +-300 targets ~= +-144 ANGLEACT counts */
#define PARK_MS      500     /* hold the away-from-start target before close */
#define POSTCLOSE_S  4       /* pose costs 2-3 s per README; give it slack */
#define MOVED        30      /* ANGLEACT delta hand_op3 used to call motion */
#define MAX_SAMPLES  6000
#define MAX_DURATION 60

/* input layout, matching hand_ctl's telemetry dump */
#define IN_POS 0
#define IN_ANG 6
#define IN_CUR 18
#define IN_ERR 24
#define IN_STA 30

static ecx_contextt ctx;
static uint8 IOmap[4096];
static int16_t *out, *in;

typedef struct { long t; int16_t tgt, ang, pos, sta, cur, err; } sample_t;
static sample_t samples[MAX_SAMPLES];
static int nsamp;
static const char *phase_of[MAX_SAMPLES];

static void pd(void)
{
   ecx_send_processdata(&ctx);
   ecx_receive_processdata(&ctx, EC_TIMEOUTRET);
}

static void cyc(int ms)
{
   int i;
   for (i = 0; i < ms; i++) { pd(); osal_usleep(1000); }
}

static long now_ms(void)
{
   struct timespec ts;
   clock_gettime(CLOCK_MONOTONIC, &ts);
   return ts.tv_sec * 1000L + ts.tv_nsec / 1000000L;
}

static void record(const char *phase, long t, int16_t tgt, int axis)
{
   sample_t *s;
   if (nsamp >= MAX_SAMPLES) return;
   s = &samples[nsamp];
   s->t   = t;
   s->tgt = tgt;
   s->ang = in[IN_ANG + axis];
   s->pos = in[IN_POS + axis];
   s->sta = in[IN_STA + axis];
   s->cur = in[IN_CUR + axis];
   s->err = in[IN_ERR + axis];
   phase_of[nsamp++] = phase;
}

/* init -> config -> OPERATIONAL. Returns 0, or a nonzero failure code. */
static int bringup(const char *iface)
{
   int chk, i;

   if (!ecx_init(&ctx, iface)) return 1;
   if (ecx_config_init(&ctx) <= 0) return 2;
   ctx.slavelist[1].mbx_proto = 0;   /* dead CoE mailbox on this SSC build */
   ecx_config_map_group(&ctx, IOmap, 0);
   if (ctx.slavelist[1].Ibytes < 36 * 2 || ctx.slavelist[1].Obytes < 19 * 2)
      return 4;

   out = (int16_t *)ctx.slavelist[1].outputs;
   in  = (int16_t *)ctx.slavelist[1].inputs;
   /* park no-change targets before the first frame goes out: a zeroed
      output buffer reads as "close every axis", and that pattern should
      never reach the wire even while the enable word is still 0. */
   memset(out, 0, ctx.slavelist[1].Obytes);
   for (i = 1; i <= 6; i++) out[i] = -1;

   ecx_statecheck(&ctx, 0, EC_STATE_SAFE_OP, EC_TIMEOUTSTATE * 4);
   ctx.slavelist[0].state = EC_STATE_OPERATIONAL;
   pd();
   ecx_writestate(&ctx, 0);
   chk = 200;
   do { pd(); ecx_statecheck(&ctx, 0, EC_STATE_OPERATIONAL, 50000); }
   while (chk-- && (ctx.slavelist[0].state != EC_STATE_OPERATIONAL));
   ecx_readstate(&ctx);
   if (ctx.slavelist[1].state != EC_STATE_OPERATIONAL) return 3;
   return 0;
}

static const char *bringup_err(int rc)
{
   switch (rc)
   {
      case 1: return "ecx_init failed (need CAP_NET_RAW, or wrong iface)";
      case 2: return "no EtherCAT slave (check link/power/iface)";
      case 3: return "slave refused OPERATIONAL";
      case 4: return "PDO map smaller than the RH56F1 layout expects";
   }
   return "unknown";
}

/* hand_ctl's wake wiggle, verbatim: boot lands every axis in STATUS=7 and
   a pose is ignored until they leave it. Reproduced rather than corrected
   on purpose - note it feeds raw ANGLEACT (890..1850) in where a target
   (0..2000) belongs, so the "wiggle" is really a small move toward mid
   travel. That quirk is part of the known-good path; changing it here
   would add a second variable to the experiment. Returns ms spent, and
   sets *ok to whether every axis actually left STATUS=7. */
static int wake(int *ok)
{
   int asleep = 1, t, i;
   for (t = 0; t < WAKE_MS_MAX && asleep; t++)
   {
      int16_t base;
      for (i = 0; i < 6; i++)
      {
         base = in[IN_ANG + i];
         if (base < 200) base = 200;
         if (base > 1800) base = 1800;
         out[HS_OUT_TARGET + i] = base + (((t / 400) % 2) ? 60 : -60);
      }
      pd();
      if (t % 200 == 0)
      {
         asleep = 0;
         for (i = 0; i < 6; i++) if (in[IN_STA + i] == 7) asleep = 1;
      }
      osal_usleep(1000);
   }
   *ok = !asleep;
   return t;
}

int main(int argc, char **argv)
{
   int axis = 2, duration_s = 10;
   int i, rc, lock_fd, wake_ms, wake_ok = 0, reconnected = 0;
   int16_t center, ang_start, ang_preclose = 0, ang_postclose = 0;
   int16_t park_tgt;
   int max_dev = 0, dev_postclose = 0;
   long t0, t, last_flip = 0, last_log = -LOG_EVERY_MS;
   int high = 0;
   char why[256] = {0};

   if (argc < 2)
   {
      printf("usage: ecat_persistent_probe <iface> [axis 0-5] [duration_s]\n");
      return 1;
   }
   if (argc > 2) axis = atoi(argv[2]);
   if (argc > 3) duration_s = atoi(argv[3]);
   if (axis < 0 || axis > 5) { printf("axis must be 0-5\n"); return 1; }
   if (duration_s < 1 || duration_s > MAX_DURATION)
   { printf("duration_s must be 1..%d\n", MAX_DURATION); return 1; }

   lock_fd = hs_lock(20);
   if (lock_fd < 0) { printf("bus busy: another master holds the hand\n"); return 3; }

   rc = bringup(argv[1]);
   if (rc) { printf("%s\n", bringup_err(rc)); hs_unlock(lock_fd); return 2; }

   /* neutral frame: enable + no-change targets, gentle profile */
   out[0] = 1;
   for (i = 1; i <= 6; i++) out[i] = -1;
   hs_profile(out, 500, 500);
   cyc(300);

   /* refuse to push an axis that a previous run left stalled - the probe
      skips hs_stall_relief, so the only safe answer here is to stop. */
   if (in[IN_STA + axis] == 5 || in[IN_STA + axis] == 6 ||
       in[IN_CUR + axis] > 400)
   {
      printf("axis %d is stalled (sta=%d cur=%dmA) - relieve it first "
             "(hand_ctl pose with that axis opened) before probing\n",
             axis, in[IN_STA + axis], in[IN_CUR + axis]);
      ecx_close(&ctx);
      hs_unlock(lock_fd);
      return 4;
   }

   wake_ms = wake(&wake_ok);
   for (i = 1; i <= 6; i++) out[i] = -1;
   cyc(200);

   /* Both swing endpoints have to sit in free travel. Clamping the centre
      into [AMP, 2000-AMP] is not enough: an axis parked against a stop
      (fingers rest at ANGLEACT ~896, i.e. target ~12) would still get
      commanded to 0 on the low half of the swing, pressing into the stop,
      drawing current and heating an actuator for no experimental gain.
      So when the axis starts near a limit, swing one-way off it instead:
      endpoints become [here, here + 2*AMP] near closed, and
      [here - 2*AMP, here] near open. */
   ang_start = in[IN_ANG + axis];
   center = hs_ang_to_target(ang_start);
   if (center < AMP)             center = (int16_t)(center + AMP);
   else if (center > 2000 - AMP) center = (int16_t)(center - AMP);

   /* oscillate at 1 kHz - same PDO cadence as every working binary here,
      since cycle rate is entangled with the watchdog we are testing */
   t0 = now_ms();
   while ((t = now_ms() - t0) < (long)duration_s * 1000)
   {
      if (t - last_flip >= OSC_HALF_MS)
      {
         int16_t tgt[6] = {-1, -1, -1, -1, -1, -1};
         high = !high;
         last_flip = t;
         tgt[axis] = (int16_t)(high ? center + AMP : center - AMP);
         if (axis >= AX_INDEX)
         {
            /* index/thumb axes have interlock partners, so this wiggle
               goes through the same gate every other caller uses - and
               the whole guarded vector is written, since the guard's
               answer to a clash is to move a DIFFERENT axis clear */
            hs_interlock(tgt, &in[IN_ANG], why, sizeof why);
            for (i = 0; i < 6; i++) out[HS_OUT_TARGET + i] = tgt[i];
         }
         else
            out[HS_OUT_TARGET + axis] = tgt[axis];   /* strictly one axis */
      }
      pd();
      if (t - last_log >= LOG_EVERY_MS)
      {
         int d = in[IN_ANG + axis] - ang_start;
         if (d < 0) d = -d;
         if (d > max_dev) max_dev = d;
         record("open", t, out[HS_OUT_TARGET + axis], axis);
         last_log = t;
      }
      osal_usleep(1000);
   }

   /* Park a target that is unambiguously away from where the axis started,
      and carry it through the close. Zeroing to -1 here would make the
      disconnect latch "no change" and the post-close branch of the
      experiment could never fire. center+AMP is the open-ward end. */
   park_tgt = (int16_t)(center + AMP);
   out[HS_OUT_TARGET + axis] = park_tgt;
   for (i = 0; i < PARK_MS; i++)
   {
      pd();
      if (i % LOG_EVERY_MS == 0)
         record("park", now_ms() - t0, park_tgt, axis);
      osal_usleep(1000);
   }
   ang_preclose = in[IN_ANG + axis];
   record("preclose", now_ms() - t0, park_tgt, axis);

   ecx_close(&ctx);

   /* the disconnect-to-execute hypothesis predicts motion starts HERE */
   for (i = 0; i < POSTCLOSE_S; i++) osal_usleep(1000000);

   rc = bringup(argv[1]);
   if (rc == 0)
   {
      reconnected = 1;
      out[0] = 1;
      for (i = 1; i <= 6; i++) out[i] = -1;
      hs_profile(out, 500, 500);
      cyc(200);
      ang_postclose = in[IN_ANG + axis];
      record("postclose", now_ms() - t0, -1, axis);
      dev_postclose = ang_postclose - ang_preclose;
      if (dev_postclose < 0) dev_postclose = -dev_postclose;
      ecx_close(&ctx);
   }

   hs_unlock(lock_fd);

   /* everything below runs with the link already down */
   printf("axis=%d center=%d amp=%d duration_s=%d\n",
          axis, center, AMP, duration_s);
   printf("wake=%s after %dms\n", wake_ok ? "ok" : "FAILED (axes still STA=7)",
          wake_ms);
   if (why[0]) printf("interlock: %s\n", why);
   printf("ang_start=%d ang_preclose=%d max_dANG_open=%d (moved > %d)\n",
          ang_start, ang_preclose, max_dev, MOVED);
   if (reconnected)
      printf("ang_postclose=%d dANG_postclose=%d\n",
             ang_postclose, dev_postclose);
   else
      printf("ang_postclose=unknown (reconnect after close failed: %s)\n",
             bringup_err(rc));

   if (max_dev > MOVED)
      printf("verdict: LIVE - the axis tracked the target with the link "
             "open; the disconnect-to-execute assumption is false\n");
   else if (reconnected && dev_postclose > MOVED)
      printf("verdict: DISCONNECT-GATED - flat while open, moved only "
             "after ecx_close\n");
   else if (!wake_ok)
      printf("verdict: INCONCLUSIVE - wake never cleared STATUS=7, so a "
             "flat trace says nothing about the disconnect question\n");
   else
      printf("verdict: NO MOTION EITHER WAY - check the sta/err/cur "
             "columns below before drawing any conclusion\n");

   printf("phase,t_ms,target,angleact,pos,sta,cur,err\n");
   for (i = 0; i < nsamp; i++)
      printf("%s,%ld,%d,%d,%d,%d,%d,%d\n", phase_of[i], samples[i].t,
             samples[i].tgt, samples[i].ang, samples[i].pos,
             samples[i].sta, samples[i].cur, samples[i].err);
   if (nsamp >= MAX_SAMPLES)
      printf("# sample buffer full at %d rows - trace is truncated\n", nsamp);
   return 0;
}
