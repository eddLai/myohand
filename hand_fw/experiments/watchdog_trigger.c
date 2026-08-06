/* watchdog_trigger - is it the SM watchdog or the dead link?
 *
 * SUPERSEDED 2026-08-06, kept because its measurement is still sound and
 * only its conclusion was wrong. It found that starving process data for
 * 100 ms moves the axis with the socket open and OPERATIONAL held, and
 * read that as "the watchdog is the trigger". rate_sweep then showed the
 * simpler cause: this hand applies nothing at 1 kHz because its control
 * loop needs more than a millisecond, and any long enough quiet lets it
 * finish one. The starve was not tripping a timeout, it was getting out
 * of the way. See the vault's Execution_Trigger_Settled.
 *
 * Every execution this hand has ever performed followed ecx_close(): the
 * master tore down its socket and the physical link went quiet. Two
 * different things happen at that moment and no run so far has separated
 * them - the sync-manager watchdog expires, AND the link goes away. If
 * it is the watchdog, then a master that simply stops sending for longer
 * than the timeout can trigger a pose while keeping its socket, its
 * OPERATIONAL bring-up and its bus lock. That is a far cheaper trigger
 * than tearing the master down, and it says something specific about the
 * firmware. If it is the link, stopping the frames will do nothing.
 *
 * Each round writes a fresh target 100 counts further closed, holds it in
 * OPERATIONAL for a second (which by now we expect to do nothing), then
 * stops sending process data for an escalating interval while leaving the
 * socket open and the PHY up. Then it resumes and looks: did the axis
 * move, and did the slave fall out of OPERATIONAL. The escalation finds
 * the threshold, which should land on the slave's own watchdog time -
 * printed from its registers at the top, so the number can be checked
 * against the behaviour rather than assumed.
 *
 * Targets step gently closed-ward from wherever the axis rests and stop
 * well clear of the closed stop, so no round can push an axis into its
 * mechanical limit.
 *
 * Usage: watchdog_trigger <iface> [axis 0-5]
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
#define HOLD_MS      1000
#define RESUME_MS    2500
#define MOVED        30
#define STEP         100    /* how much further closed each round aims */
#define FLOOR_ANG    1000   /* never command below this - clear of the stop */

#define IN_ANG 6
#define IN_CUR 18
#define IN_ERR 24
#define IN_STA 30

static ecx_contextt ctx;
static uint8 IOmap[4096];
static int16_t *out, *in;
static int axis = AX_MIDDLE;

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

static uint16 rd16(uint16 reg)
{
   uint16 v = 0;
   ecx_FPRD(&ctx.port, ctx.slavelist[1].configadr, reg, sizeof(v), &v,
            EC_TIMEOUTRET);
   return etohs(v);
}

/* Ask for OPERATIONAL from inside the 1 kHz loop - see dc_check.c for why
   the cadence during the request is what decides whether it is granted. */
static int request_op(void)
{
   int i;
   ctx.slavelist[0].state = EC_STATE_OPERATIONAL;
   pd();
   ecx_writestate(&ctx, 0);
   for (i = 0; i < OP_WAIT_MS; i++)
   {
      pd();
      if (i % 100 == 0)
      {
         ecx_statecheck(&ctx, 0, EC_STATE_OPERATIONAL, 0);
         if (ctx.slavelist[0].state == EC_STATE_OPERATIONAL) break;
      }
      osal_usleep(1000);
   }
   ecx_readstate(&ctx);
   return ctx.slavelist[1].state == EC_STATE_OPERATIONAL;
}

static int bringup(const char *iface)
{
   int i;
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
   ecx_statecheck(&ctx, 0, EC_STATE_SAFE_OP, EC_TIMEOUTSTATE * 4);
   ecx_configdc(&ctx);
   cyc(DC_SETTLE_MS);
   if (!request_op()) return 3;
   return 0;
}

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
   static const int starve_ms[] = {50, 100, 200, 500, 1000, 2000};
   int i, r, rc, lock_fd, wake_ok = 0, wake_ms, nround;
   int16_t ang, target;

   if (argc < 2) { printf("usage: watchdog_trigger <iface> [axis 0-5]\n"); return 1; }
   if (argc > 2) axis = atoi(argv[2]);
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

   /* The slave's own watchdog settings, so the threshold below can be
      compared against a number the hardware reports rather than the
      100 ms everybody assumes. WD_DIV counts 40 ns ticks; WD_TIME_PD is
      in those divider units. SM2's control byte bit 6 is watchdog
      enable. */
   printf("axis=%d wake=%s(%dms) state=0x%02x\n", axis,
          wake_ok ? "ok" : "FAILED", wake_ms, ctx.slavelist[1].state);
   printf("slave watchdog: WD_DIV=%u WD_TIME_PD=%u -> %.1f ms   "
          "SM2 ctrl=0x%02x\n",
          rd16(0x0400), rd16(0x0420),
          (double)rd16(0x0400) * 0.000040 * (double)rd16(0x0420),
          rd16(0x0814) & 0xff);
   printf("-----------------------------------------------------------"
          "-------------------\n");
   printf("round  starve   target  ang_before  ang_after  dANG   state  "
          "AL      re-OP\n");

   nround = (int)(sizeof starve_ms / sizeof starve_ms[0]);
   for (r = 0; r < nround; r++)
   {
      int16_t ang_before, ang_after;
      int reop = -1, d;

      ang = in[IN_ANG + axis];
      target = (int16_t)(ang - STEP);
      if (target < FLOOR_ANG)
      {
         printf("stopping at round %d: next target %d would crowd the "
                "closed stop\n", r, target);
         break;
      }
      out[HS_OUT_TARGET + axis] = target;
      cyc(HOLD_MS);                       /* held in OP - expected: nothing */
      ang_before = in[IN_ANG + axis];

      /* the whole experiment: send nothing at all. The socket stays open,
         the PHY stays up, only the frames stop. */
      osal_usleep((unsigned)starve_ms[r] * 1000u);

      cyc(RESUME_MS);
      ang_after = in[IN_ANG + axis];
      ecx_readstate(&ctx);
      d = ang_after - ang_before;

      if (ctx.slavelist[1].state != EC_STATE_OPERATIONAL)
         reop = request_op();

      printf("%-6d %-8d %-7d %-11d %-10d %-6d 0x%02x   0x%04x  %s%s\n",
             r + 1, starve_ms[r], target, ang_before, ang_after, d,
             ctx.slavelist[1].state, ctx.slavelist[1].ALstatuscode,
             reop < 0 ? "-" : (reop ? "yes" : "NO"),
             (d > MOVED || d < -MOVED) ? "   <== MOVED" : "");

      if (reop == 0) { printf("could not get back to OP - stopping\n"); break; }
      out[HS_OUT_TARGET + axis] = HS_TGT_HOLD;
      cyc(300);
   }

   printf("-----------------------------------------------------------"
          "-------------------\n");
   printf("NOTE 2026-08-06: the reading below was superseded the same day. "
          "A round\nthat moves does NOT show the watchdog is the trigger - "
          "it shows that a\nlong enough gap in process data lets the slave "
          "finish a control cycle it\ncannot finish at 1 kHz. rate_sweep "
          "and why_1khz have the measurement;\nthe hand follows "
          "continuously at any period from about 1.6 ms.\n\n");
   printf("A round that moved means the SM watchdog is the trigger and the "
          "link never had to\ndrop. All rounds flat means the trigger needs "
          "the link itself to go away.\n");

   for (i = 0; i < 6; i++) out[HS_OUT_TARGET + i] = HS_TGT_HOLD;
   cyc(200);
   ecx_close(&ctx);
   hs_unlock(lock_fd);
   return 0;
}
