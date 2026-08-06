/* rate_sweep - are we simply sending too fast?
 *
 * coe_startup turned the picture over on 2026-08-06. Two readings did it.
 *
 * First, 0x1C32:02 is read-only on this slave: writing it returns SDO
 * abort 0x06010002. That is correct for SM-Synchron - in that mode the
 * object is not a setting the master imposes, it is the slave's own
 * measurement of the interval between SM2 events. So the value is not a
 * stale config nobody wrote. It is a report.
 *
 * And what it reports is 18-27 ms, while we drive the bus at 1 ms.
 *
 * Second, 0x1C32:12, the cycle-exceeded counter, went from 29507 to 34350
 * across an eight second run - about 600 a second against a 1 kHz feed.
 * The slave has been telling us on every frame that it cannot keep up,
 * and no run before this one read the complaint.
 *
 * That inverts the standing conclusion. If the application needs tens of
 * milliseconds per cycle and a new SM2 event arrives every millisecond,
 * it may never reach the end of a cycle - each frame preempting the work
 * the last one started. Outputs would never be applied, no current would
 * flow, and telemetry would keep streaming, because inputs are a
 * different sync manager. That is exactly the symptom.
 *
 * It also explains the one thing the watchdog story never did. Starving
 * the link for 100 ms was read as "the watchdog is the trigger". The
 * simpler reading is that we finally stopped interrupting it and it
 * completed a cycle. The quiet is the trigger, not the timeout.
 *
 * So: hold the same pose at a range of feed rates and see which, if any,
 * lets the axis move with the link up. If a slow rate works, the firmware
 * was never at fault - our 1 kHz was, and 0x1C32:05's claim of a 100 us
 * minimum cycle is wrong on this device.
 *
 * Every step stays under the 99.9 ms sync-manager watchdog, so nothing
 * here can trip it. Whatever moves the axis moves it with the link up,
 * OPERATIONAL held, and no timeout involved.
 *
 * Usage: rate_sweep <iface> [axis 0-5] [secs_per_step] [p1,p2,...]
 *   the period list is comma separated milliseconds; default 1,2,5,10,
 *   20,30,50,80. Every entry must stay under the 99.9 ms watchdog.
 */
#include "soem/soem.h"
#include "hand_safety.h"
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>
#include <time.h>

#define IN_ANG 6
#define IN_CUR 18
#define IN_ERR 24
#define IN_STA 30

#define ANG_LO   1100
#define ANG_HI   1600
#define AMP       180
#define MOVED      30

/* every one of these is below the 99.9 ms watchdog, so a step that moves
   the axis did it without a timeout anywhere near it */
static int periods_ms[16] = {1, 2, 5, 10, 20, 30, 50, 80};
static int nper = 8;

static ecx_contextt ctx;
static uint8 IOmap[8192];
static int16_t *out, *in;

static void pd(void)
{
   ecx_send_processdata(&ctx);
   ecx_receive_processdata(&ctx, EC_TIMEOUTRET);
}

static long now_ms(void)
{
   struct timespec ts;
   clock_gettime(CLOCK_MONOTONIC, &ts);
   return ts.tv_sec * 1000L + ts.tv_nsec / 1000000L;
}

static int rd32(uint16 idx, uint8 sub, uint32 *v)
{
   int size = 4, wkc;
   *v = 0;
   wkc = ecx_SDOread(&ctx, 1, idx, sub, FALSE, &size, v, EC_TIMEOUTRXM);
   return wkc > 0;
}

static int rd16(uint16 idx, uint8 sub, uint16 *v)
{
   int size = 2, wkc;
   *v = 0;
   wkc = ecx_SDOread(&ctx, 1, idx, sub, FALSE, &size, v, EC_TIMEOUTRXM);
   return wkc > 0;
}

int main(int argc, char **argv)
{
   int axis = AX_MIDDLE, secs = 4;
   int i, s, lock_fd, chk;
   int16_t ang_ref, tgt_a, tgt_b;
   int any_moved = 0;

   if (argc < 2)
   { printf("usage: rate_sweep <iface> [axis 0-5] [secs_per_step]\n"); return 1; }
   if (argc > 2) axis = atoi(argv[2]);
   if (argc > 3) secs = atoi(argv[3]);
   if (argc > 4)
   {
      char *tok = strtok(argv[4], ",");
      nper = 0;
      while (tok && nper < 16)
      {
         int v = atoi(tok);
         if (v < 1 || v > 90)
         { printf("period %d out of 1..90 ms (watchdog is 99.9)\n", v);
           return 1; }
         periods_ms[nper++] = v;
         tok = strtok(NULL, ",");
      }
      if (!nper) { printf("empty period list\n"); return 1; }
   }
   if (axis < 0 || axis > 5) { printf("axis must be 0-5\n"); return 1; }
   if (secs < 1 || secs > 15) { printf("secs 1..15\n"); return 1; }

   lock_fd = hs_lock(20);
   if (lock_fd < 0) { printf("bus busy\n"); return 3; }
   if (!ecx_init(&ctx, argv[1]))
   { printf("ecx_init failed\n"); hs_unlock(lock_fd); return 2; }
   if (ecx_config_init(&ctx) <= 0)
   { printf("no slave\n"); ecx_close(&ctx); hs_unlock(lock_fd); return 2; }

   ctx.slavelist[1].mbx_proto = 0;
   ecx_config_map_group(&ctx, IOmap, 0);
   out = (int16_t *)ctx.slavelist[1].outputs;
   in  = (int16_t *)ctx.slavelist[1].inputs;
   if (!out || ctx.slavelist[1].Obytes < 14 || ctx.slavelist[1].Ibytes < 72)
   { printf("unexpected PDO size\n"); ecx_close(&ctx); hs_unlock(lock_fd); return 4; }

   memset(out, 0, ctx.slavelist[1].Obytes);
   for (i = 1; i <= 6; i++) out[i] = HS_TGT_HOLD;
   hs_profile(out, 500, 500);

   ecx_statecheck(&ctx, 0, EC_STATE_SAFE_OP, EC_TIMEOUTSTATE * 4);
   ctx.slavelist[0].state = EC_STATE_OPERATIONAL;
   ecx_writestate(&ctx, 0);
   for (chk = 0; chk < 2000; chk++)
   {
      pd();
      if ((chk % 20) == 0)
      {
         ecx_statecheck(&ctx, 0, EC_STATE_OPERATIONAL, 0);
         if (ctx.slavelist[0].state == EC_STATE_OPERATIONAL) break;
      }
      osal_usleep(1000);
   }
   ecx_readstate(&ctx);
   if (ctx.slavelist[1].state != EC_STATE_OPERATIONAL)
   {
      printf("never reached OP: state=0x%02x AL=0x%04x\n",
             ctx.slavelist[1].state, ctx.slavelist[1].ALstatuscode);
      ecx_close(&ctx); hs_unlock(lock_fd); return 5;
   }
   /* the mailbox is serviced in OP too, so the slave's own view of the
      cycle can be read between steps rather than only at the ends */
   ctx.slavelist[1].mbx_proto = ECT_MBXPROT_COE;

   for (i = 0; i < 200; i++) { pd(); osal_usleep(1000); }
   printf("OP reached in %d ms.  STA=[%d %d %d %d %d %d] "
          "ANG=[%d %d %d %d %d %d] CUR=[%d %d %d %d %d %d]\n", chk,
          in[IN_STA], in[IN_STA+1], in[IN_STA+2], in[IN_STA+3],
          in[IN_STA+4], in[IN_STA+5],
          in[IN_ANG], in[IN_ANG+1], in[IN_ANG+2], in[IN_ANG+3],
          in[IN_ANG+4], in[IN_ANG+5],
          in[IN_CUR], in[IN_CUR+1], in[IN_CUR+2], in[IN_CUR+3],
          in[IN_CUR+4], in[IN_CUR+5]);

   if (in[IN_STA + axis] == 5 || in[IN_STA + axis] == 6 ||
       in[IN_CUR + axis] > 400)
   {
      printf("axis %d stalled - relieve first\n", axis);
      ecx_close(&ctx); hs_unlock(lock_fd); return 6;
   }
   for (i = 0; i < 6; i++)
      if (in[IN_STA + i] == 7)
      { printf("axis %d asleep (STA=7) - run hand_ctl pose once first\n", i);
        ecx_close(&ctx); hs_unlock(lock_fd); return 7; }

   ang_ref = in[IN_ANG + axis];
   (void)tgt_a; (void)tgt_b;

   printf("axis=%d resting at %d, each step commands %d counts away from "
          "wherever the axis actually is, %d s per step\n",
          axis, ang_ref, AMP, secs);
   printf("all periods are under the 99.9 ms watchdog, so nothing below "
          "can be a timeout\n\n");
   printf("period  target  frames  dANG  maxCUR  0x1C32:02(ns)  cyc-exceeded(+)  state\n");

   out[0] = 1;
   for (s = 0; s < nper; s++)
   {
      int p = periods_ms[s];
      int16_t before, tgt;
      int max_dev = 0, max_cur = 0, frames = 0;
      uint32 meas = 0;
      uint16 exc0 = 0, exc1 = 0;
      long t0, t;

      rd16(0x1C32, 12, &exc0);
      before = in[IN_ANG + axis];
      /* Always aim AMP away from where the axis IS, not at a fixed
         endpoint. The first version alternated between two constants, so
         a step could be commanded to the position it already held - and
         "did not move" then means nothing. Every step now has somewhere
         to go. */
      tgt = (int16_t)(before > (ANG_LO + ANG_HI) / 2 ? before - AMP
                                                     : before + AMP);
      if (tgt < ANG_LO) tgt = ANG_LO;
      if (tgt > ANG_HI) tgt = ANG_HI;
      out[HS_OUT_TARGET + axis] = tgt;

      t0 = now_ms();
      while ((t = now_ms() - t0) < (long)secs * 1000)
      {
         int d;
         pd();
         frames++;
         d = in[IN_ANG + axis] - before;
         if (d < 0) d = -d;
         if (d > max_dev) max_dev = d;
         if (in[IN_CUR + axis] > max_cur) max_cur = in[IN_CUR + axis];
         osal_usleep(p * 1000);
      }
      rd32(0x1C32, 2, &meas);
      rd16(0x1C32, 12, &exc1);
      ecx_readstate(&ctx);

      printf("%-7d %-7d %-7d %-5d %-7d %-14u %-16u 0x%02x%s\n",
             p, tgt, frames, max_dev, max_cur, meas, (unsigned)(exc1 - exc0),
             ctx.slavelist[1].state, max_dev > MOVED ? "   <== MOVED" : "");
      if (max_dev > MOVED) any_moved = 1;
      if (ctx.slavelist[1].state != EC_STATE_OPERATIONAL)
      { printf("        left OPERATIONAL - stopping\n"); break; }
   }

   out[0] = 0;
   for (i = 1; i <= 6; i++) out[i] = HS_TGT_HOLD;
   for (i = 0; i < 200; i++) { pd(); osal_usleep(1000); }
   ecx_close(&ctx);
   hs_unlock(lock_fd);

   printf("\n");
   if (any_moved)
      printf("verdict: A FEED RATE MOVED IT with the link up and OP held. "
             "The firmware was never waiting for a disconnect or a "
             "watchdog - we were sending faster than it could consume. "
             "0x1C32:05's 100 us minimum cycle is wrong on this device.\n");
   else
      printf("verdict: no rate from %d to %d ms moved it. Feed rate is not "
             "the missing piece either; the cycle-exceeded column still "
             "says something about how it copes.\n",
             periods_ms[0], periods_ms[nper - 1]);
   return 0;
}
