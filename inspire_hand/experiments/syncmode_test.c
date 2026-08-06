/* syncmode_test - the slave says SM-Synchron. Make it say Free Run.
 *
 * Everything measured on 2026-08-06 agrees on the symptom and disagrees
 * with the slave's own declaration. It reports 0x1C32:01 = 1, SM-Synchron,
 * which means the application copies its outputs on the SM2 event - on
 * data arrival, every frame. It reaches OPERATIONAL, holds it for ten
 * seconds at 1 kHz with AL=0x0000, answers telemetry the whole time, and
 * moves nothing and draws nothing. Then the SM watchdog expires and the
 * pose it was holding all along is applied exactly: op_execute_hunt
 * commanded 1509 and the axis landed on 1508.
 *
 * Every candidate that could explain that has now been eliminated by
 * measurement rather than argument. Not the switch - the direct link
 * reproduced it. Not distributed clocks - probe_dc2/dc3 held OP with
 * Sync0 running and DCactive=1. Not the master's cadence - process data
 * at 1 kHz through the whole transition reaches OP in 120 ms and changes
 * nothing. Not ENABLE_SET - all thirteen candidate values, plus a rising
 * edge and a write-order swap, moved nothing. Not the output image size -
 * the compliant 18-byte CoE map is refused outright with AL=0x001e,
 * invalid output configuration, so the 38-byte one is what the firmware
 * wants.
 *
 * What has never been tried is telling it to run differently. CoE turned
 * out to be alive, and 0x1C32:04 reads 0x401f: this device advertises
 * that it supports Free Run as well as SM-Synchron. In Free Run the
 * application runs on its own timer and copies the output buffer whenever
 * it likes, with no SM event required. If the missing piece is that the
 * SM2 event never reaches the application, Free Run steps around it
 * entirely. If it moves in Free Run, that is the answer and also the fix.
 *
 * The write goes to 0x1C32:01 in PRE_OP, which is where sync mode is
 * meant to be configured, and it is volatile - a power cycle restores the
 * default. It is read back before anything is driven, because a slave
 * that ignores the write would otherwise be mistaken for one that
 * obeys it and still refuses to move.
 *
 * Note the ordering constraint. SDO needs the mailbox, so mbx_proto has
 * to stay as advertised while the write happens; the process data image
 * needs mbx_proto zeroed, or SOEM maps 18 bytes and the slave rejects it.
 * So the SDO is done first and the flag is cleared only afterwards.
 *
 * Usage: syncmode_test <iface> [mode 0=FreeRun 1=SM-Synchron] [axis] [hold_s]
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

#define ANG_LO   1050
#define ANG_HI   1650
#define AMP       200
#define MOVED      30
#define LOG_MS     20
#define MAX_SAMP 3000

static ecx_contextt ctx;
static uint8 IOmap[8192];
static int16_t *out, *in;

typedef struct { long t; int16_t tgt, ang, cur, sta; } samp_t;
static samp_t samples[MAX_SAMP];
static int nsamp;

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

static int sdo_rd16(uint16 idx, uint8 sub, uint16 *v)
{
   int size = 2, wkc;
   *v = 0;
   wkc = ecx_SDOread(&ctx, 1, idx, sub, FALSE, &size, v, EC_TIMEOUTRXM);
   return wkc > 0;
}

int main(int argc, char **argv)
{
   int want = 0, axis = AX_MIDDLE, hold_s = 8;
   int i, lock_fd, chk, max_dev = 0, max_cur = 0, high = 0;
   uint16 before = 0xFFFF, after = 0xFFFF, cyct = 0;
   int16_t ang_start, tgt_hi, tgt_lo, cur_tgt;
   long t0, t, last_flip = 0, last_log = -LOG_MS;

   if (argc < 2)
   {
      printf("usage: syncmode_test <iface> [0=FreeRun 1=SM-Synchron] "
             "[axis] [hold_s]\n");
      return 1;
   }
   if (argc > 2) want = atoi(argv[2]);
   if (argc > 3) axis = atoi(argv[3]);
   if (argc > 4) hold_s = atoi(argv[4]);
   if (want != 0 && want != 1) { printf("mode must be 0 or 1\n"); return 1; }
   if (axis < 0 || axis > 5) { printf("axis must be 0-5\n"); return 1; }
   if (hold_s < 1 || hold_s > 30) { printf("hold_s 1..30\n"); return 1; }

   lock_fd = hs_lock(20);
   if (lock_fd < 0) { printf("bus busy\n"); return 3; }

   if (!ecx_init(&ctx, argv[1]))
   { printf("ecx_init failed\n"); hs_unlock(lock_fd); return 2; }
   if (ecx_config_init(&ctx) <= 0)
   { printf("no slave\n"); ecx_close(&ctx); hs_unlock(lock_fd); return 2; }

   /* --- SDO phase: mailbox needs mbx_proto intact and PRE_OP --- */
   ctx.slavelist[0].state = EC_STATE_PRE_OP;
   ecx_writestate(&ctx, 0);
   ecx_statecheck(&ctx, 0, EC_STATE_PRE_OP, EC_TIMEOUTSTATE * 2);
   ecx_readstate(&ctx);
   printf("PRE_OP for the mailbox: state=0x%02x  mbx_proto=0x%04x\n",
          ctx.slavelist[1].state, ctx.slavelist[1].mbx_proto);

   if (!sdo_rd16(0x1C32, 1, &before))
   {
      printf("cannot read 0x1C32:01 - no mailbox, nothing to configure\n");
      ecx_close(&ctx); hs_unlock(lock_fd); return 4;
   }
   sdo_rd16(0x1C32, 2, &cyct);
   printf("0x1C32:01 before = %u (%s)\n", before,
          before == 0 ? "Free Run" : before == 1 ? "SM-Synchron" : "other");

   {
      uint16 v = (uint16)want;
      int wkc = ecx_SDOwrite(&ctx, 1, 0x1C32, 1, FALSE, sizeof v, &v,
                             EC_TIMEOUTRXM);
      printf("write 0x1C32:01 = %d -> wkc=%d\n", want, wkc);
      if (wkc <= 0)
      {
         char *e;
         while ((e = ecx_elist2string(&ctx)) && *e) printf("  soem: %s", e);
      }
   }
   if (!sdo_rd16(0x1C32, 1, &after)) after = 0xFFFF;
   printf("0x1C32:01 after  = %u%s\n", after,
          after == (uint16)want ? "  <== the slave took it"
                                : "  <== SLAVE IGNORED THE WRITE");
   if (after != (uint16)want)
      printf("  a refused write means the run below tests nothing new;\n"
             "  it is the old configuration under a new name.\n");

   /* --- process data phase: needs mbx_proto cleared, or SOEM maps the
          18-byte CoE image the firmware rejects with AL=0x001e --- */
   ctx.slavelist[1].mbx_proto = 0;
   ecx_config_map_group(&ctx, IOmap, 0);
   out = (int16_t *)ctx.slavelist[1].outputs;
   in  = (int16_t *)ctx.slavelist[1].inputs;
   if (!out || ctx.slavelist[1].Obytes < 14 || ctx.slavelist[1].Ibytes < 72)
   {
      printf("unexpected PDO size O=%u I=%u\n",
             ctx.slavelist[1].Obytes, ctx.slavelist[1].Ibytes);
      ecx_close(&ctx); hs_unlock(lock_fd); return 5;
   }
   printf("mapped: Obytes=%u Ibytes=%u\n",
          ctx.slavelist[1].Obytes, ctx.slavelist[1].Ibytes);

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
   printf("OP: state=0x%02x AL=0x%04x after %d ms\n",
          ctx.slavelist[1].state, ctx.slavelist[1].ALstatuscode, chk);
   if (ctx.slavelist[1].state != EC_STATE_OPERATIONAL)
   {
      printf("verdict: sync mode %d is refused at the OP transition\n", want);
      ecx_close(&ctx); hs_unlock(lock_fd); return 6;
   }

   cyc(300);
   printf("telemetry: STA=[%d %d %d %d %d %d] ANG=[%d %d %d %d %d %d] "
          "CUR=[%d %d %d %d %d %d] ERR=[%d %d %d %d %d %d]\n",
          in[IN_STA], in[IN_STA+1], in[IN_STA+2], in[IN_STA+3],
          in[IN_STA+4], in[IN_STA+5],
          in[IN_ANG], in[IN_ANG+1], in[IN_ANG+2], in[IN_ANG+3],
          in[IN_ANG+4], in[IN_ANG+5],
          in[IN_CUR], in[IN_CUR+1], in[IN_CUR+2], in[IN_CUR+3],
          in[IN_CUR+4], in[IN_CUR+5],
          in[IN_ERR], in[IN_ERR+1], in[IN_ERR+2], in[IN_ERR+3],
          in[IN_ERR+4], in[IN_ERR+5]);

   if (in[IN_STA + axis] == 5 || in[IN_STA + axis] == 6 ||
       in[IN_CUR + axis] > 400)
   {
      printf("axis %d stalled - relieve first\n", axis);
      ecx_close(&ctx); hs_unlock(lock_fd); return 7;
   }

   ang_start = in[IN_ANG + axis];
   tgt_hi = (int16_t)(ang_start + AMP);
   tgt_lo = (int16_t)(ang_start - AMP);
   if (tgt_hi > ANG_HI) tgt_hi = ANG_HI;
   if (tgt_lo < ANG_LO) tgt_lo = ANG_LO;
   if (tgt_hi - tgt_lo < 80) { tgt_lo = ANG_LO; tgt_hi = ANG_LO + 2 * AMP; }

   printf("ang_start=%d  swinging %d <-> %d, enable=1, link held %d s "
          "at 1 kHz\n", ang_start, tgt_lo, tgt_hi, hold_s);

   out[0] = 1;
   cur_tgt = tgt_hi;
   out[HS_OUT_TARGET + axis] = cur_tgt;

   t0 = now_ms();
   while ((t = now_ms() - t0) < (long)hold_s * 1000)
   {
      if (t - last_flip >= 2000)
      {
         high = !high;
         last_flip = t;
         cur_tgt = high ? tgt_hi : tgt_lo;
         out[HS_OUT_TARGET + axis] = cur_tgt;
      }
      pd();
      if (t - last_log >= LOG_MS)
      {
         int d = in[IN_ANG + axis] - ang_start;
         if (d < 0) d = -d;
         if (d > max_dev) max_dev = d;
         if (in[IN_CUR + axis] > max_cur) max_cur = in[IN_CUR + axis];
         if (nsamp < MAX_SAMP)
         {
            samples[nsamp].t = t;
            samples[nsamp].tgt = cur_tgt;
            samples[nsamp].ang = in[IN_ANG + axis];
            samples[nsamp].cur = in[IN_CUR + axis];
            samples[nsamp].sta = in[IN_STA + axis];
            nsamp++;
         }
         last_log = t;
      }
      osal_usleep(1000);
   }

   ecx_readstate(&ctx);
   printf("end of hold: state=0x%02x AL=0x%04x\n",
          ctx.slavelist[1].state, ctx.slavelist[1].ALstatuscode);

   out[0] = 0;
   for (i = 1; i <= 6; i++) out[i] = HS_TGT_HOLD;
   cyc(200);

   /* put the sync mode back the way it was found */
   ctx.slavelist[0].state = EC_STATE_PRE_OP;
   ecx_writestate(&ctx, 0);
   ecx_statecheck(&ctx, 0, EC_STATE_PRE_OP, EC_TIMEOUTSTATE * 2);
   {
      uint16 v = before, rb = 0xFFFF;
      ctx.slavelist[1].mbx_proto = ECT_MBXPROT_COE;
      ecx_SDOwrite(&ctx, 1, 0x1C32, 1, FALSE, sizeof v, &v, EC_TIMEOUTRXM);
      sdo_rd16(0x1C32, 1, &rb);
      printf("restored 0x1C32:01 to %u (reads back %u)\n", before, rb);
   }

   ecx_close(&ctx);
   hs_unlock(lock_fd);

   printf("\nsync mode %u, cycle time was %u ns\n", after, cyct);
   printf("max_dANG=%d (moved > %d)  max_CUR=%d mA\n",
          max_dev, MOVED, max_cur);
   if (max_dev > MOVED)
      printf("verdict: MOVED IN OP under sync mode %u. The apply path was "
             "the sync mode all along, and this is the fix.\n", after);
   else if (max_cur > 20)
      printf("verdict: ENERGISED BUT STILL - outputs reached the motor "
             "without producing travel; a different problem from silence\n");
   else
      printf("verdict: NOTHING - sync mode %u behaves exactly like the "
             "one before it\n", after);

   printf("t_ms,target,angleact,cur,sta\n");
   for (i = 0; i < nsamp; i++)
      printf("%ld,%d,%d,%d,%d\n", samples[i].t, samples[i].tgt,
             samples[i].ang, samples[i].cur, samples[i].sta);
   return 0;
}
