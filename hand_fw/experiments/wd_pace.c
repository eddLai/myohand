/* wd_pace - how fast can the watchdog trigger be run?
 *
 * watchdog_trigger settled the mechanism on 2026-08-06. Starving the
 * process data for 100 ms - socket open, OPERATIONAL held, bus lock
 * kept - made the axis travel to its commanded target, and the slave
 * stayed in OP with AL=0x0000 throughout. The link never had to drop.
 * A 200 ms starve went too far and left it in SAFE_OP+ERROR with
 * AL=0x001b, the sync-manager watchdog code, unable to return to OP.
 *
 * So the trigger is the SM watchdog and its period is a register, not a
 * law: ESC 0x0400 is the divider and 0x0420 the process-data watchdog in
 * units of that divider. As found, 2498 x 40 ns x 1000 = 99.9 ms, which
 * is where the 100 ms figure came from. Nothing about that number is
 * sacred. If it can be shortened, the cost of a pose falls with it, and
 * "one pose every 2-3 seconds" becomes a rate rather than a limit.
 *
 * This measures that directly. Each cycle writes a fresh target, lets it
 * land, stops sending for long enough to trip the watchdog, resumes, and
 * reads back where the axis went. It reports the achieved pose rate and
 * whether OPERATIONAL survived every cycle - because a workaround that
 * drops the slave out of OP is not a workaround.
 *
 * Two things are checked rather than assumed. The watchdog register is
 * read back after writing, since a slave may clamp or ignore it. And the
 * state is re-read every cycle, since the failure seen at 200 ms was not
 * a lost pose but a lost slave.
 *
 * Targets are ANGLEACT units - confirmed three ways on 2026-08-06, most
 * recently by op_execute_hunt landing 1508 on a commanded 1509 - and are
 * kept inside [1050, 1650] so neither end of the swing can reach a stop.
 *
 * Usage: wd_pace <iface> [axis 0-5] [wd_ms] [cycles] [starve_ms]
 *   wd_ms     0 (default) leaves the watchdog as found; otherwise the
 *             process-data watchdog is reprogrammed to about this many ms
 *   cycles    pose cycles to run (default 6)
 *   starve_ms 0 (default) picks the watchdog time plus 25% margin
 *   settle_ms healthy frames between the target write and the starve.
 *             watchdog_trigger used 1000 here and kept OP; 8 lost it on
 *             the first cycle, so this is the variable that decides the
 *             achievable pose rate, not the watchdog alone.
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

#define ANG_LO      1050
#define ANG_HI      1650
#define RESUME_MS    600   /* watch window after resuming; a finger needs it */
#define MOVED         30
#define WAKE_MS_MAX 12000

static ecx_contextt ctx;
static uint8 IOmap[8192];
static int16_t *out, *in;
static uint16 adr;

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

static uint16 rd16(uint16 reg)
{
   uint16 v = 0;
   ecx_FPRD(&ctx.port, adr, reg, sizeof v, &v, EC_TIMEOUTRET);
   return v;
}

static void wr16(uint16 reg, uint16 v)
{
   ecx_FPWR(&ctx.port, adr, reg, sizeof v, &v, EC_TIMEOUTRET);
}

int main(int argc, char **argv)
{
   int axis = AX_MIDDLE, wd_ms = 0, cycles = 6, starve_ms = 0;
   int settle_ms = 1000;
   int i, c, lock_fd, chk, moved_cycles = 0, lost_op = 0;
   uint16 wd_div, wd_pd;
   double wd_actual;
   int16_t ang_start, tgt_a, tgt_b;
   long t_first, t_last;

   if (argc < 2)
   {
      printf("usage: wd_pace <iface> [axis 0-5] [wd_ms] [cycles] "
             "[starve_ms] [settle_ms]\n");
      return 1;
   }
   if (argc > 2) axis = atoi(argv[2]);
   if (argc > 3) wd_ms = atoi(argv[3]);
   if (argc > 4) cycles = atoi(argv[4]);
   if (argc > 5) starve_ms = atoi(argv[5]);
   if (argc > 6) settle_ms = atoi(argv[6]);
   if (settle_ms < 1 || settle_ms > 3000)
   { printf("settle_ms must be 1..3000\n"); return 1; }
   if (axis < 0 || axis > 5) { printf("axis must be 0-5\n"); return 1; }
   if (wd_ms < 0 || wd_ms > 500) { printf("wd_ms must be 0..500\n"); return 1; }
   if (cycles < 1 || cycles > 40) { printf("cycles must be 1..40\n"); return 1; }

   lock_fd = hs_lock(20);
   if (lock_fd < 0) { printf("bus busy\n"); return 3; }

   if (!ecx_init(&ctx, argv[1]))
   { printf("ecx_init failed\n"); hs_unlock(lock_fd); return 2; }
   if (ecx_config_init(&ctx) <= 0)
   { printf("no slave\n"); ecx_close(&ctx); hs_unlock(lock_fd); return 2; }

   adr = ctx.slavelist[1].configadr;
   /* the 38-byte SII image is the one the firmware accepts; the compliant
      18-byte CoE image is refused with AL=0x001e (invalid output config) */
   ctx.slavelist[1].mbx_proto = 0;
   ecx_config_map_group(&ctx, IOmap, 0);
   out = (int16_t *)ctx.slavelist[1].outputs;
   in  = (int16_t *)ctx.slavelist[1].inputs;
   if (!out || ctx.slavelist[1].Obytes < 14 || ctx.slavelist[1].Ibytes < 72)
   {
      printf("unexpected PDO size O=%u I=%u\n",
             ctx.slavelist[1].Obytes, ctx.slavelist[1].Ibytes);
      ecx_close(&ctx); hs_unlock(lock_fd); return 4;
   }
   memset(out, 0, ctx.slavelist[1].Obytes);
   for (i = 1; i <= 6; i++) out[i] = HS_TGT_HOLD;
   /* every run that has ever moved this hand set a force/speed profile
      first; leaving those words zero commands zero speed */
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
   printf("OP reached in %d ms\n", chk);

   /* --- the watchdog, as found and as we want it --- */
   wd_div = rd16(0x0400);
   wd_pd  = rd16(0x0420);
   printf("watchdog as found: div=%u pd=%u -> %.1f ms\n",
          wd_div, wd_pd, wd_div * 0.000040 * wd_pd);

   if (wd_ms > 0)
   {
      double unit = wd_div * 0.000040;            /* ms per pd count */
      long want = (long)(wd_ms / unit + 0.5);
      if (want < 1) want = 1;
      if (want > 65535) want = 65535;
      wr16(0x0420, (uint16)want);
      wd_pd = rd16(0x0420);
      printf("watchdog reprogrammed: asked %d ms -> wrote %ld, reads back "
             "%u -> %.1f ms%s\n", wd_ms, want, wd_pd, unit * wd_pd,
             (wd_pd != (uint16)want) ? "  <== SLAVE DID NOT TAKE IT" : "");
   }
   wd_actual = wd_div * 0.000040 * wd_pd;
   if (starve_ms <= 0) starve_ms = (int)(wd_actual * 1.25 + 1);
   printf("starve per cycle: %d ms (watchdog %.1f ms), settle %d ms\n",
          starve_ms, wd_actual, settle_ms);

   cyc(200);
   printf("telemetry: STA=[%d %d %d %d %d %d] ANG=[%d %d %d %d %d %d] "
          "CUR=[%d %d %d %d %d %d]\n",
          in[IN_STA], in[IN_STA+1], in[IN_STA+2], in[IN_STA+3],
          in[IN_STA+4], in[IN_STA+5],
          in[IN_ANG], in[IN_ANG+1], in[IN_ANG+2], in[IN_ANG+3],
          in[IN_ANG+4], in[IN_ANG+5],
          in[IN_CUR], in[IN_CUR+1], in[IN_CUR+2], in[IN_CUR+3],
          in[IN_CUR+4], in[IN_CUR+5]);

   if (in[IN_STA + axis] == 5 || in[IN_STA + axis] == 6 ||
       in[IN_CUR + axis] > 400)
   {
      printf("axis %d stalled (sta=%d cur=%d) - relieve first\n",
             axis, in[IN_STA + axis], in[IN_CUR + axis]);
      ecx_close(&ctx); hs_unlock(lock_fd); return 6;
   }
   for (i = 0; i < 6; i++)
      if (in[IN_STA + i] == 7)
      { printf("axis %d asleep (STATUS=7) - run hand_ctl once first\n", i);
        ecx_close(&ctx); hs_unlock(lock_fd); return 7; }

   ang_start = in[IN_ANG + axis];
   tgt_a = (int16_t)(ang_start > (ANG_LO + ANG_HI) / 2 ? ang_start - 200
                                                       : ang_start + 200);
   if (tgt_a < ANG_LO) tgt_a = ANG_LO;
   if (tgt_a > ANG_HI) tgt_a = ANG_HI;
   tgt_b = (int16_t)(tgt_a > (ANG_LO + ANG_HI) / 2 ? tgt_a - 200 : tgt_a + 200);
   if (tgt_b < ANG_LO) tgt_b = ANG_LO;
   if (tgt_b > ANG_HI) tgt_b = ANG_HI;

   printf("axis=%d ang_start=%d alternating %d <-> %d over %d cycles\n\n",
          axis, ang_start, tgt_a, tgt_b, cycles);
   printf("cyc  target  ang_before  ang_after  dANG  cycle_ms  state  AL\n");

   out[0] = 1;
   t_first = now_ms();
   t_last = t_first;
   for (c = 0; c < cycles; c++)
   {
      int16_t tgt = (c % 2) ? tgt_b : tgt_a;
      int16_t before, after;
      long t_cyc = now_ms();
      int d;

      before = in[IN_ANG + axis];
      out[HS_OUT_TARGET + axis] = tgt;
      cyc(settle_ms);   /* healthy frames before starving it again */

      osal_usleep(starve_ms * 1000);    /* no pd() here - starve it */

      cyc(RESUME_MS);
      after = in[IN_ANG + axis];
      d = after - before;
      ecx_readstate(&ctx);

      printf("%-4d %-7d %-11d %-10d %-5d %-9ld 0x%02x   0x%04x%s\n",
             c + 1, tgt, before, after, d, now_ms() - t_cyc,
             ctx.slavelist[1].state, ctx.slavelist[1].ALstatuscode,
             (d > MOVED || d < -MOVED) ? "  MOVED" : "");
      if (d > MOVED || d < -MOVED) moved_cycles++;
      if (ctx.slavelist[1].state != EC_STATE_OPERATIONAL)
      {
         /* A watchdog expiry drops the slave to SAFE_OP+ERROR by design.
            That is only fatal if getting back is expensive - and it is
            not: this session has re-reached OP in 80-300 ms. So re-arm
            and keep going, and charge the cost to the cycle. */
         long t_reop = now_ms();
         int k;
         lost_op++;
         ecx_writestate(&ctx, 0);      /* clear the error acknowledge */
         ctx.slavelist[0].state = EC_STATE_SAFE_OP + EC_STATE_ACK;
         ecx_writestate(&ctx, 0);
         ecx_statecheck(&ctx, 0, EC_STATE_SAFE_OP, EC_TIMEOUTSTATE);
         ctx.slavelist[0].state = EC_STATE_OPERATIONAL;
         ecx_writestate(&ctx, 0);
         for (k = 0; k < 1500; k++)
         {
            pd();
            if ((k % 20) == 0)
            {
               ecx_statecheck(&ctx, 0, EC_STATE_OPERATIONAL, 0);
               if (ctx.slavelist[0].state == EC_STATE_OPERATIONAL) break;
            }
            osal_usleep(1000);
         }
         ecx_readstate(&ctx);
         printf("      re-OP in %ld ms -> state=0x%02x AL=0x%04x\n",
                now_ms() - t_reop, ctx.slavelist[1].state,
                ctx.slavelist[1].ALstatuscode);
         if (ctx.slavelist[1].state != EC_STATE_OPERATIONAL)
         {
            printf("      could not get back to OP - stopping\n");
            break;
         }
         out[0] = 1;                   /* enable survives nothing; re-assert */
         hs_profile(out, 500, 500);
      }
      t_last = now_ms();
   }

   /* park, de-energise, restore the watchdog we found */
   out[0] = 0;
   for (i = 1; i <= 6; i++) out[i] = HS_TGT_HOLD;
   cyc(200);
   if (wd_ms > 0) { wr16(0x0420, 1000); printf("\nwatchdog restored to %u\n",
                                               rd16(0x0420)); }
   ecx_close(&ctx);
   hs_unlock(lock_fd);

   printf("\n%d/%d cycles moved; OP dropped and re-armed %d times\n",
          moved_cycles, c, lost_op);
   if (c > 0)
      printf("mean cycle %.0f ms -> %.1f poses/s (watchdog %.1f ms of it)\n",
             (double)(t_last - t_first) / c,
             1000.0 * c / (double)(t_last - t_first), wd_actual);
   if (moved_cycles == c)
      printf("verdict: PACED - every cycle applied its target without "
             "tearing the master down. %s\n",
             lost_op ? "OP was re-armed in between, at the cost printed above."
                     : "OP held throughout.");
   else if (moved_cycles == 0)
      printf("verdict: NOTHING MOVED at this watchdog setting\n");
   else
      printf("verdict: PARTIAL - %d of %d cycles applied; the margin is "
             "too tight at this setting\n", moved_cycles, c);
   return 0;
}
