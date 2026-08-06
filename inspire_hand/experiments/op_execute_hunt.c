/* op_execute_hunt - what makes this hand execute a pose while in OP?
 *
 * "The firmware only executes after the master disconnects" is not a
 * thing an EtherCAT slave does. A slave in OPERATIONAL applies its
 * outputs; one that waits for the SM watchdog to trip is telling us we
 * are driving it wrong. ecat_persistent_probe measures WHETHER it moves;
 * this one hunts for the input that makes it.
 *
 * Everything here runs inside one OPERATIONAL session, on a link that is
 * never dropped until the end, so a phase that moves the axis has
 * answered the question by itself. The phases, in order:
 *
 *   hold    one constant target for several seconds. The probe never did
 *           this - it flipped the target every 500 ms and parked for only
 *           500 ms, so "does not execute in OP" and "takes longer than
 *           half a second to start" were never separated.
 *   enable  step ENABLE_SET through the candidate values, target held.
 *           The ops log swept these on 2026-07-29 and saw nothing, but
 *           that run predates the STATUS=7 wake, ran through a switch,
 *           and was read at the time as a power-stage problem. Today the
 *           hand demonstrably moves, so the sweep is worth redoing with
 *           a live power stage.
 *   edge    ENABLE_SET 0 -> 1 with the target already in place, in case
 *           the application latches a pose on a rising edge rather than
 *           on a level.
 *   after   write the target while ENABLE_SET is already 1, in case the
 *           order of the two writes is what matters.
 *   close   the positive control: drop the link and let the SM watchdog
 *           fire. If nothing above moved and this does, the run was set
 *           up correctly and the answer is still "not in OP".
 *
 * Each phase reports the largest ANGLEACT excursion and the largest
 * current seen during it, because a phase that draws current without
 * moving is a different finding from one that does neither.
 *
 * Usage: op_execute_hunt <iface> [axis 0-5] [target] [dc_cycle_us]
 *   target defaults to a mild bend from wherever the axis rests.
 */
#include "soem/soem.h"
#include "hand_safety.h"
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>

#define WAKE_MS_MAX  12000
#define DC_SETTLE_MS 500
#define OP_WAIT_MS   2000
#define HOLD_S       5
#define STEP_MS      800
#define POSTCLOSE_S  4
#define MOVED        30

#define IN_POS 0
#define IN_ANG 6
#define IN_CUR 18
#define IN_ERR 24
#define IN_STA 30

static ecx_contextt ctx;
static uint8 IOmap[4096];
static int16_t *out, *in;
static int dc_us, axis = AX_MIDDLE;

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

/* Run one phase for ms milliseconds and report what the axis did. The
   return is the peak |dANG| against the angle at phase entry. */
static int phase(const char *name, const char *detail, int ms)
{
   int16_t ang0 = in[IN_ANG + axis];
   int peak = 0, peak_cur = 0, i;
   for (i = 0; i < ms; i++)
   {
      int d;
      pd();
      d = in[IN_ANG + axis] - ang0;
      if (d < 0) d = -d;
      if (d > peak) peak = d;
      if (in[IN_CUR + axis] > peak_cur) peak_cur = in[IN_CUR + axis];
      osal_usleep(1000);
   }
   printf("%-8s %-28s dANG=%-5d maxCUR=%-5d ang=%d sta=%d err=%d%s\n",
          name, detail, peak, peak_cur, in[IN_ANG + axis],
          in[IN_STA + axis], in[IN_ERR + axis], peak > MOVED ? "  <== MOVED" : "");
   return peak;
}

static int bringup(const char *iface)
{
   int chk, i;

   if (!ecx_init(&ctx, iface)) return 1;
   if (ecx_config_init(&ctx) <= 0) return 2;
   ctx.slavelist[1].mbx_proto = 0;
   ecx_config_map_group(&ctx, IOmap, 0);
   if (ctx.slavelist[1].Ibytes < 36 * 2 || ctx.slavelist[1].Obytes < 19 * 2)
      return 4;
   out = (int16_t *)ctx.slavelist[1].outputs;
   in  = (int16_t *)ctx.slavelist[1].inputs;
   memset(out, 0, ctx.slavelist[1].Obytes);
   for (i = 0; i < 6; i++) out[HS_OUT_TARGET + i] = HS_TGT_HOLD;

   /* the arming order dc_check.c established: SAFE_OP, process data
      running, then arm, then ask for OP without ever dropping below the
      1 kHz cadence */
   ecx_statecheck(&ctx, 0, EC_STATE_SAFE_OP, EC_TIMEOUTSTATE * 4);
   ecx_configdc(&ctx);
   cyc(DC_SETTLE_MS);
   if (dc_us > 0)
   {
      if (!ctx.slavelist[1].hasdc) return 5;
      ecx_dcsync0(&ctx, 1, TRUE, (uint32_t)dc_us * 1000u, 0);
      cyc(DC_SETTLE_MS);
   }
   ctx.slavelist[0].state = EC_STATE_OPERATIONAL;
   pd();
   ecx_writestate(&ctx, 0);
   for (chk = 0; chk < OP_WAIT_MS; chk++)
   {
      pd();
      if (chk % 100 == 0)
      {
         ecx_statecheck(&ctx, 0, EC_STATE_OPERATIONAL, 0);
         if (ctx.slavelist[0].state == EC_STATE_OPERATIONAL) break;
      }
      osal_usleep(1000);
   }
   ecx_readstate(&ctx);
   if (ctx.slavelist[1].state != EC_STATE_OPERATIONAL) return 3;
   return 0;
}

/* hand_ctl's wake wiggle, verbatim - see ecat_persistent_probe.c */
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
   /* ENABLE_SET candidates. 1 is what every binary here sends; 0x3F and
      -1 are the other two the ops log tried; the bit patterns cover
      "one bit per axis" and "high byte matters" readings of the field. */
   static const int cand[] = {0, 1, 2, 3, 4, 7, 15, 63, 255, 256, 257, 4096, -1};
   int i, rc, lock_fd, wake_ok = 0, wake_ms, moved_in_op = 0;
   int16_t ang_start, target = -1, ang_postclose = 0;
   char label[64];

   if (argc < 2)
   {
      printf("usage: op_execute_hunt <iface> [axis 0-5] [target] "
             "[dc_cycle_us]\n");
      return 1;
   }
   if (argc > 2) axis = atoi(argv[2]);
   if (argc > 3) target = (int16_t)atoi(argv[3]);
   if (argc > 4) dc_us = atoi(argv[4]);
   if (axis < 0 || axis > 5) { printf("axis must be 0-5\n"); return 1; }

   lock_fd = hs_lock(20);
   if (lock_fd < 0) { printf("bus busy: another master holds the hand\n"); return 3; }

   rc = bringup(argv[1]);
   if (rc)
   {
      printf("bring-up failed rc=%d state=0x%02x AL=0x%04x\n", rc,
             ctx.slavelist[1].state, ctx.slavelist[1].ALstatuscode);
      ecx_close(&ctx); hs_unlock(lock_fd); return 2;
   }

   out[0] = 1;
   for (i = 0; i < 6; i++) out[HS_OUT_TARGET + i] = HS_TGT_HOLD;
   hs_profile(out, 500, 500);
   cyc(300);

   if (in[IN_STA + axis] == 5 || in[IN_STA + axis] == 6 ||
       in[IN_CUR + axis] > 400)
   {
      printf("axis %d is stalled (sta=%d cur=%dmA) - open it first\n",
             axis, in[IN_STA + axis], in[IN_CUR + axis]);
      ecx_close(&ctx); hs_unlock(lock_fd); return 4;
   }

   wake_ms = wake(&wake_ok);
   for (i = 0; i < 6; i++) out[HS_OUT_TARGET + i] = HS_TGT_HOLD;
   cyc(200);

   ang_start = in[IN_ANG + axis];
   /* A target is only useful if the axis has room to travel toward it.
      Default to a bend away from wherever it is resting, in whatever
      units the command field turns out to be in: 2026-08-06 measured
      ANGLEACT landing on the commanded number, so aim 280 counts
      closed-ward and clamp clear of the stop. */
   if (target < 0)
   {
      int t = ang_start - 280;
      if (t < 950) t = 950;
      target = (int16_t)t;
   }
   printf("axis=%d ang_start=%d target=%d dc=%dus wake=%s(%dms) "
          "state=0x%02x\n", axis, ang_start, target, dc_us,
          wake_ok ? "ok" : "FAILED", wake_ms, ctx.slavelist[1].state);
   printf("--------------------------------------------------------------"
          "----------------\n");

   /* hold: one constant target, long enough that a slow controller has
      no excuse left */
   out[HS_OUT_TARGET + axis] = target;
   moved_in_op |= phase("hold", "enable=1, target constant", HOLD_S * 1000);

   /* enable sweep, target still in place */
   for (i = 0; i < (int)(sizeof cand / sizeof cand[0]); i++)
   {
      out[0] = (int16_t)cand[i];
      snprintf(label, sizeof label, "ENABLE_SET=%d", cand[i]);
      moved_in_op |= phase("enable", label, STEP_MS);
   }

   /* edge: target already parked, enable taken 0 -> 1 */
   out[0] = 0;
   cyc(300);
   out[0] = 1;
   moved_in_op |= phase("edge", "ENABLE_SET 0->1, target held", STEP_MS * 2);

   /* after: enable settled first, target written second */
   out[HS_OUT_TARGET + axis] = HS_TGT_HOLD;
   out[0] = 1;
   cyc(300);
   out[HS_OUT_TARGET + axis] = target;
   moved_in_op |= phase("after", "target written with enable=1", STEP_MS * 2);

   /* positive control: everything above ran with the link up. Drop it. */
   printf("--------------------------------------------------------------"
          "----------------\n");
   ecx_readstate(&ctx);
   printf("state before close: 0x%02x AL=0x%04x\n",
          ctx.slavelist[1].state, ctx.slavelist[1].ALstatuscode);
   ecx_close(&ctx);
   for (i = 0; i < POSTCLOSE_S; i++) osal_usleep(1000000);

   rc = bringup(argv[1]);
   if (rc == 0)
   {
      out[0] = 1;
      for (i = 0; i < 6; i++) out[HS_OUT_TARGET + i] = HS_TGT_HOLD;
      hs_profile(out, 500, 500);
      cyc(200);
      ang_postclose = in[IN_ANG + axis];
      printf("close    link dropped, %ds wait        dANG=%-5d "
             "ang=%d (target was %d)\n", POSTCLOSE_S,
             ang_postclose - ang_start, ang_postclose, target);
      ecx_close(&ctx);
   }
   else
      printf("close    reconnect failed rc=%d\n", rc);

   hs_unlock(lock_fd);

   printf("--------------------------------------------------------------"
          "----------------\n");
   if (moved_in_op > MOVED)
      printf("verdict: something in OPERATIONAL moved it - read the phase "
             "marked MOVED above; that is the missing input\n");
   else if (rc == 0 && abs(ang_postclose - ang_start) > MOVED)
      printf("verdict: nothing in OPERATIONAL moved it, the disconnect "
             "did. Every candidate above is ruled out\n");
   else
      printf("verdict: nothing moved at all, including after the "
             "disconnect - this run proves nothing, check power and sta\n");
   return 0;
}
