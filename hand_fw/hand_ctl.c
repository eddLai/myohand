/* hand_ctl - Inspire RH56F1 EtherCAT control core (SOEM)
 *
 * Usage:
 *   hand_ctl state
 *   hand_ctl scale                          what the target numbers mean
 *   hand_ctl pose P R M I TB TR [force] [speed]
 *       targets are ANGLEACT counts: ~890=closed, ~1850=open, -1 = leave
 *       axis unchanged. The scale is defined once, in hand_safety.h
 *   $ECAT_IFACE selects the NIC (default eth0). Confirm with ecat_scan
 *   before assuming - the hand has answered on a different interface on
 *   every host that has driven it.
 *
 * Behavior encodes the reverse-engineered F1 semantics:
 *   - boot lands all axes in STATUS=7 (standby); wiggle around current
 *     position wakes them before a pose is accepted
 *   - the hand applies a pose continuously while process data arrives,
 *     provided it arrives no faster than about 625 Hz. This tool cycles
 *     at 500 Hz for that reason, so the pose executes during the hold and
 *     the telemetry printed below is the pose that actually happened
 *     rather than the one about to. It used to cycle at 1 kHz, at which
 *     this hand applies nothing, which is why it looked as though a pose
 *     needed the master to disconnect
 * Safety (shared driver layer, see hand_safety.c):
 *   - exclusive bus lock, range clamp, per-axis force/speed profile
 *   - joint interlock clamps poses that would jam index against thumb
 *   - stall relief for axes left loaded by a previous execution
 * Output: single JSON object on stdout.
 */
#include "soem/soem.h"
#include "hand_safety.h"
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>

/* Process-data period, and deliberately NOT one millisecond.
   This hand's application needs a little over 1 ms per control cycle, and
   SM-Synchron restarts that cycle on every arriving frame - so at 1 kHz it
   never finishes one and applies no output at all. That is where "the pose
   executes only when the master disconnects" came from: the pose was
   landing when we stopped interrupting it, not when the link died.
   Measured 2026-08-06 (experiments/why_1khz): nothing travels below about
   1.05 ms, and the slave's cycle-exceeded counter is exactly zero from
   1.6 ms up. 2 ms sits inside that clean band with margin and matches
   handd's default. See the vault's Execution_Trigger_Settled. */
#define CYCLE_US 2000

#define WAKE_MS_MAX 12000
/* long enough for an axis to travel now that it moves during the hold,
   rather than a wait for something to happen after the link goes away */
#define HOLD_MS 1200

static ecx_contextt ctx;
static uint8 IOmap[4096];

static void pd(void)
{
   ecx_send_processdata(&ctx);
   ecx_receive_processdata(&ctx, EC_TIMEOUTRET);
}

/* run process data for a wall-clock duration; the loops below used to
   count iterations and call them milliseconds, which was only true while
   the period happened to be 1 ms */
static void cyc(int ms)
{
   int n = (ms * 1000) / CYCLE_US, i;
   for (i = 0; i < n; i++) { pd(); osal_usleep(CYCLE_US); }
}

static void jarr(const char *k, int16_t *v, int n, int last)
{
   int i;
   printf("\"%s\":[", k);
   for (i = 0; i < n; i++) printf("%d%s", v[i], i < n - 1 ? "," : "");
   printf("]%s", last ? "" : ",");
}

static void fail(const char *msg)
{
   printf("{\"ok\":false,\"error\":\"%s\"}\n", msg);
   exit(1);
}

int main(int argc, char **argv)
{
   int16_t *out, *in;
   int16_t tgt[6] = {HS_TGT_HOLD, HS_TGT_HOLD, HS_TGT_HOLD,
                     HS_TGT_HOLD, HS_TGT_HOLD, HS_TGT_HOLD};
   int force = 500, speed = 800;
   int do_pose = 0, i, chk, t, lock_fd, guarded = 0;
   const char *iface;
   char why[256] = {0};

   if (argc < 2) fail("usage: hand_ctl state | scale | pose P R M I TB TR [force] [speed]");
   /* `scale` answers what the target numbers mean and touches no bus, so
      a Python client can check its own copy of the scale against the C
      one instead of the two drifting apart unnoticed. */
   if (!strcmp(argv[1], "scale"))
   {
      char js[160];
      hs_scale_json(js, sizeof js);
      printf("{\"ok\":true,\"scale\":%s}\n", js);
      return 0;
   }
   if (!strcmp(argv[1], "pose"))
   {
      if (argc < 8) fail("pose needs 6 targets");
      do_pose = 1;
      for (i = 0; i < 6; i++)
      {
         long v = strtol(argv[2 + i], NULL, 10);
         if (v < -32768 || v > 32767 || !hs_target_valid((int16_t)v))
            fail("target out of range (see `hand_ctl scale`; -1 holds)");
         tgt[i] = (int16_t)v;
      }
      if (argc > 8) force = atoi(argv[8]);
      if (argc > 9) speed = atoi(argv[9]);
      if (force < 0 || force > 1000) fail("force out of range (0..1000)");
      if (speed < 50 || speed > 1000) fail("speed out of range (50..1000)");
   }
   else if (strcmp(argv[1], "state"))
      fail("unknown command");

   iface = hs_iface();
   lock_fd = hs_lock(20);
   if (lock_fd < 0) fail("bus busy: another master holds the hand (20s timeout)");
   if (!ecx_init(&ctx, iface)) fail("ecx_init (need CAP_NET_RAW or root; check $ECAT_IFACE)");
   if (ecx_config_init(&ctx) <= 0) fail("no EtherCAT slave (check link/power; run ecat_scan on $ECAT_IFACE)");
   ctx.slavelist[1].mbx_proto = 0;   /* NOT because CoE is dead - it answers
      every SDO. Zeroing it makes SOEM size the output image from the SII
      at 38 bytes, the only layout this firmware accepts; mapping over CoE
      yields 18 and is refused with AL=0x001e. */
   ecx_config_map_group(&ctx, IOmap, 0);
   ecx_statecheck(&ctx, 0, EC_STATE_SAFE_OP, EC_TIMEOUTSTATE * 4);
   ctx.slavelist[0].state = EC_STATE_OPERATIONAL;
   pd();
   ecx_writestate(&ctx, 0);
   chk = 200;
   do { pd(); ecx_statecheck(&ctx, 0, EC_STATE_OPERATIONAL, 50000); }
   while (chk-- && (ctx.slavelist[0].state != EC_STATE_OPERATIONAL));
   ecx_readstate(&ctx);
   if (ctx.slavelist[1].state != EC_STATE_OPERATIONAL) fail("slave refused OPERATIONAL");

   out = (int16_t *)ctx.slavelist[1].outputs;
   in  = (int16_t *)ctx.slavelist[1].inputs;

   /* neutral frame: enable + no-change targets (never leave zeros: 0=fist!) */
   memset(out, 0, ctx.slavelist[1].Obytes);
   out[0] = 1;
   for (i = 1; i <= 6; i++)  out[i] = HS_TGT_HOLD;
   hs_profile(out, force, speed);
   cyc(300);

   if (do_pose)
   {
      /* Wake axes stuck in STATUS=7 by wiggling around current position -
         but only if one actually is. This used to enter the loop with
         asleep=1 and so always sent at least one wiggle frame before
         checking. That was invisible while the master cycled at 1 kHz,
         because the hand applies nothing at that rate; at 2 ms the same
         frame is a real command, and it moved all six axes 60 counts
         before the requested pose was written. Check first. */
      int asleep = 0;
      for (i = 0; i < 6; i++) if (in[30 + i] == 7) asleep = 1;
      for (t = 0; t < WAKE_MS_MAX && asleep; t += CYCLE_US / 1000)
      {
         int16_t base;
         for (i = 0; i < 6; i++)
         {
            base = in[6 + i];
            if (base < 200) base = 200;
            if (base > 1800) base = 1800;
            out[1 + i] = base + (((t / 400) % 2) ? 60 : -60);
         }
         pd();
         if (t % 200 == 0)
         {
            asleep = 0;
            for (i = 0; i < 6; i++) if (in[30 + i] == 7) asleep = 1;
         }
         osal_usleep(CYCLE_US);
      }
      /* driver-level gate: nothing reaches the PDO unchecked */
      guarded  = hs_stall_relief(tgt, &in[18], &in[30], &in[6], why, sizeof why);
      guarded += hs_interlock(tgt, &in[6], why, sizeof why);
      /* write the requested pose. It rides in the output buffer until the
         SM watchdog expires, which the exit below causes by going away */
      for (i = 0; i < 6; i++) out[HS_OUT_TARGET + i] = tgt[i];
      cyc(HOLD_MS);
   }

   printf("{\"ok\":true,\"mode\":\"%s\",\"guarded\":%d,\"guard_note\":\"%s\",",
          do_pose ? "pose" : "state", guarded, why);
   jarr("pos", &in[0], 6, 0);
   jarr("ang", &in[6], 6, 0);
   jarr("frc", &in[12], 6, 0);
   jarr("cur", &in[18], 6, 0);
   jarr("err", &in[24], 6, 0);
   jarr("sta", &in[30], 6, 0);
   /* T1 hands stream 34 shorts of touch sensing after the axis block:
      8 capacitive modules x 4 quantities + 2 unnamed fields. Field order
      is not yet calibrated against the real hand; this only hands the
      raw block to the caller. Absent (null) on a hand without T1, whose
      input image stops short of it. */
   if (ctx.slavelist[1].Ibytes >= (42 + 34) * 2)
   {
      jarr("tmp", &in[36], 6, 0);
      jarr("tac", &in[42], 34, 1);
   }
   else
   {
      jarr("tmp", &in[36], 6, 0);
      printf("\"tac\":null");
   }
   printf("}\n");
   ecx_close(&ctx);
   hs_unlock(lock_fd);
   return 0;
}
