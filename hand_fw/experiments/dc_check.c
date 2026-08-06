/* dc_check - why does this hand refuse OPERATIONAL with Sync0 armed?
 *
 * The 2026-08-06 direct-link run killed the standing explanation. On a
 * cable straight into the hand (100 Mb/s, no switch, one slave)
 * ecat_persistent_probe with dc_cycle_us=1000 still came back
 * SAFE_OP+ERROR / AL=0x002d, and pdelay was still 0 - which handd calls
 * the signature of a switch. It is not: for a bus with one slave, that
 * slave IS the reference clock, so a propagation delay of zero is the
 * correct measurement, not a missing one.
 *
 * That leaves the master's own arming sequence as the suspect. Both
 * handd and the probe arm Sync0 and then request OPERATIONAL while
 * sending process data only once per statecheck timeout - roughly 20 Hz
 * against a Sync0 running at 1 kHz. An SSC slave counts the sync events
 * it gets no process data for, and "no sync" is exactly what AL=0x002d
 * says. So this tool separates the two variables the probe conflates:
 *
 *   dc_check eth1 0            1 kHz PD loop, DC off      - control
 *   dc_check eth1 1000         1 kHz PD loop, Sync0 1 ms  - the fix
 *   dc_check eth1 1000 --slow  20 Hz during the OP request - the bug
 *
 * It reads the DC registers around each step, so a failure says which
 * half of the clock is missing: whether the slave's clock runs at all,
 * whether Sync0 was armed with a start time in the future, and what the
 * slave's own time deviation looks like once it is running.
 *
 * It cannot move the hand: the enable word stays 0 and every target is
 * HS_TGT_HOLD from before the first frame goes out. Reaching OPERATIONAL
 * with enable=0 drives nothing.
 *
 * Usage: dc_check <iface> [dc_cycle_us] [settle_ms] [--slow]
 */
#include "soem/soem.h"
#include "hand_safety.h"
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>
#include <time.h>

static ecx_contextt ctx;
static uint8 IOmap[4096];
static int16_t *out;

static void pd(void)
{
   ecx_send_processdata(&ctx);
   ecx_receive_processdata(&ctx, EC_TIMEOUTRET);
}

/* PD at 1 kHz, the cadence every working binary here uses */
static void cyc(int ms)
{
   int i;
   for (i = 0; i < ms; i++) { pd(); osal_usleep(1000); }
}

static uint16 adr(void) { return ctx.slavelist[1].configadr; }

static int64_t rd64(uint16 reg)
{
   int64_t v = 0;
   ecx_FPRD(&ctx.port, adr(), reg, sizeof(v), &v, EC_TIMEOUTRET);
   return (int64_t)etohll(v);
}

static uint32_t rd32(uint16 reg)
{
   uint32_t v = 0;
   ecx_FPRD(&ctx.port, adr(), reg, sizeof(v), &v, EC_TIMEOUTRET);
   return etohl(v);
}

static uint8 rd8(uint16 reg)
{
   uint8 v = 0;
   ecx_FPRD(&ctx.port, adr(), reg, sizeof(v), &v, EC_TIMEOUTRET);
   return v;
}

/* 0x092C is sign-and-magnitude, not two's complement: bit 31 is the sign */
static int32_t sysdiff(void)
{
   uint32_t raw = rd32(ECT_REG_DCSYSDIFF);
   int32_t mag = (int32_t)(raw & 0x7fffffffu);
   return (raw & 0x80000000u) ? -mag : mag;
}

static const char *state_name(uint16 s)
{
   switch (s & 0x0f)
   {
      case EC_STATE_INIT: return "INIT";
      case EC_STATE_PRE_OP: return "PRE_OP";
      case EC_STATE_BOOT: return "BOOT";
      case EC_STATE_SAFE_OP: return "SAFE_OP";
      case EC_STATE_OPERATIONAL: return "OP";
   }
   return "?";
}

int main(int argc, char **argv)
{
   const char *iface;
   int dc_us = 1000, settle_ms = 500, slow = 0;
   int i, n, lock_fd, reached = 0;
   int64_t t_a, t_b, sys, start0;
   int32_t diff_before, diff_after;

   if (argc < 2)
   {
      printf("usage: dc_check <iface> [dc_cycle_us] [settle_ms] [--slow]\n"
             "  dc_cycle_us  0 = DC off (control), else Sync0 period\n"
             "  settle_ms    PD cycles at 1 kHz before the OP request\n"
             "  --slow       request OP at ~20 Hz, as handd/the probe do\n");
      return 1;
   }
   iface = argv[1];
   for (i = 2; i < argc; i++)
   {
      if (!strcmp(argv[i], "--slow")) slow = 1;
      else if (dc_us == 1000 && i == 2) dc_us = atoi(argv[i]);
      else settle_ms = atoi(argv[i]);
   }
   if (dc_us < 0 || dc_us > 100000)
   { printf("dc_cycle_us must be 0..100000\n"); return 1; }
   if (settle_ms < 0 || settle_ms > 10000)
   { printf("settle_ms must be 0..10000\n"); return 1; }

   lock_fd = hs_lock(20);
   if (lock_fd < 0) { printf("bus busy: another master holds the hand\n"); return 3; }

   if (!ecx_init(&ctx, iface))
   { printf("ecx_init failed on %s (need CAP_NET_RAW)\n", iface); return 2; }
   n = ecx_config_init(&ctx);
   if (n <= 0) { printf("no slave on %s\n", iface); ecx_close(&ctx); return 2; }
   ctx.slavelist[1].mbx_proto = 0;   /* NOT because CoE is dead - it answers
      every SDO. Zeroing it makes SOEM size the output image from the SII
      at 38 bytes, the only layout this firmware accepts; mapping over CoE
      yields 18 and is refused with AL=0x001e. */

   printf("slaves=%d hasdc=%d\n", n, ctx.slavelist[1].hasdc);

   /* 1. does the slave's own clock run at all? asked before configdc, so
         nothing the master does can be credited for the answer. */
   t_a = rd64(ECT_REG_DCSYSTIME);
   osal_usleep(100000);
   t_b = rd64(ECT_REG_DCSYSTIME);
   printf("clock: t0=%lld t1=%lld d=%lld ns over ~100 ms -> %s\n",
          (long long)t_a, (long long)t_b, (long long)(t_b - t_a),
          (t_b - t_a) > 50000000LL ? "RUNNING" : "STOPPED (Sync0 can never fire)");

   /* 2. map, with the outputs parked before the first frame. enable stays
         0 for the whole run: this tool never drives an axis. */
   ecx_config_map_group(&ctx, IOmap, 0);
   out = (int16_t *)ctx.slavelist[1].outputs;
   memset(out, 0, ctx.slavelist[1].Obytes);
   for (i = 0; i < 6; i++) out[HS_OUT_TARGET + i] = HS_TGT_HOLD;
   out[0] = 0;
   ecx_statecheck(&ctx, 0, EC_STATE_SAFE_OP, EC_TIMEOUTSTATE * 4);

   /* 3. configdc, then look at what it measured */
   printf("configdc=%d pdelay=%d sysoffset=%lld sysdelay=%u\n",
          ecx_configdc(&ctx) ? 1 : 0, ctx.slavelist[1].pdelay,
          (long long)rd64(ECT_REG_DCSYSOFFSET), rd32(ECT_REG_DCSYSDELAY));
   printf("  (pdelay=0 is CORRECT for a one-slave bus: it is the reference)\n");

   /* 4. let the PDO loop run before anything is armed - the slave sees a
         steady 1 kHz of process data first, which is the state an SSC
         application expects to already be in when Sync0 starts. */
   cyc(settle_ms);
   diff_before = sysdiff();

   if (dc_us > 0)
   {
      ecx_dcsync0(&ctx, 1, TRUE, (uint32_t)dc_us * 1000u, 0);
      sys = rd64(ECT_REG_DCSYSTIME);
      start0 = rd64(ECT_REG_DCSTART0);
      printf("sync0: armed cycle=%uns act=0x%02x start0=%lld sys=%lld "
             "(start is %lld ms %s)\n",
             rd32(ECT_REG_DCCYCLE0), rd8(ECT_REG_DCSYNCACT),
             (long long)start0, (long long)sys,
             (long long)((start0 - sys) / 1000000), start0 > sys ? "ahead" : "BEHIND");
      /* 5. and keep the process data flowing while Sync0 runs. This is
            the step handd and the probe skip. */
      cyc(settle_ms);
   }
   else
      printf("sync0: not armed (DC off control run)\n");

   diff_after = sysdiff();
   printf("sysdiff: %d -> %d ns\n", diff_before, diff_after);

   /* 6. the OP request itself. Fast keeps the 1 kHz loop running through
         the transition; slow reproduces the ~20 Hz the probe requests at. */
   ctx.slavelist[0].state = EC_STATE_OPERATIONAL;
   pd();
   ecx_writestate(&ctx, 0);
   if (slow)
   {
      for (i = 0; i < 40 && ctx.slavelist[0].state != EC_STATE_OPERATIONAL; i++)
      { pd(); ecx_statecheck(&ctx, 0, EC_STATE_OPERATIONAL, 50000); }
   }
   else
   {
      for (i = 0; i < 2000; i++)
      {
         pd();
         if (i % 100 == 0)
         {
            ecx_statecheck(&ctx, 0, EC_STATE_OPERATIONAL, 0);
            if (ctx.slavelist[0].state == EC_STATE_OPERATIONAL) break;
         }
         osal_usleep(1000);
      }
   }
   ecx_readstate(&ctx);
   reached = (ctx.slavelist[1].state == EC_STATE_OPERATIONAL);
   printf("state=0x%02x (%s) AL=0x%04x  op_request=%s\n",
          ctx.slavelist[1].state, state_name(ctx.slavelist[1].state),
          ctx.slavelist[1].ALstatuscode, slow ? "20 Hz" : "1 kHz");

   if (reached)
   {
      /* hold it, still with enable=0, and watch the slave's own view of
         its clock. A slave that keeps OP for a second under Sync0 is the
         thing that has never happened on this hand. */
      cyc(1000);
      ecx_readstate(&ctx);
      printf("held 1 s: state=0x%02x AL=0x%04x sysdiff=%d\n",
             ctx.slavelist[1].state, ctx.slavelist[1].ALstatuscode, sysdiff());
   }

   if (dc_us > 0) ecx_dcsync0(&ctx, 1, FALSE, 0, 0);
   ecx_close(&ctx);
   hs_unlock(lock_fd);

   if (reached)
      printf("verdict: OPERATIONAL reached with dc_cycle_us=%d at %s\n",
             dc_us, slow ? "20 Hz" : "1 kHz");
   else
      printf("verdict: refused - dc_cycle_us=%d at %s, AL=0x%04x\n",
             dc_us, slow ? "20 Hz" : "1 kHz", ctx.slavelist[1].ALstatuscode);
   return reached ? 0 : 1;
}
