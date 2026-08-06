/* why_1khz - what exactly breaks at one millisecond?
 *
 * rate_sweep established the fact on 2026-08-06: at a 2 ms process-data
 * period this hand tracks its target and draws current, and at 1 ms it
 * does neither, with OPERATIONAL held and the watchdog nowhere near.
 * That is the what. This is the why, and it matters for two reasons -
 * a mechanism explains whether 500 Hz is a safe default or a lucky one,
 * and the answer is what the vendor needs in order to fix or document it.
 *
 * The standing explanation is that the application cannot finish a cycle
 * before the next SM2 event starts another. If that is right, the
 * evidence is in the sync manager itself rather than in the axis. An
 * EtherCAT SM in buffered mode holds three buffers; the ESC hands the
 * master one to write and the PDI - the slave's own microcontroller -
 * takes another to read. Register 0x0815, SM2's status byte, reports
 * which buffer was last written and whether either side currently holds
 * one open:
 *
 *     bits 4-5   buffer status: 0,1,2 = which buffer was written last,
 *                3 = no buffer written since the last read
 *     bit 6      read buffer in use  - the PDI is reading right now
 *     bit 7      write buffer in use - the master is writing right now
 *
 * So sample that byte every frame and count what is seen. A PDI that
 * keeps up shows read-in-use transiently and the buffer status rotating.
 * A PDI that never completes a read shows something else, and whatever it
 * shows at 1 ms but not at 2 ms is the mechanism.
 *
 * Three other things are read per step because each one could carry the
 * answer instead. 0x1C32:02 is what the slave measures the SM2 interval
 * to be - if it reports 2 ms while we send at 1 ms, half our frames are
 * not producing events at all, which would be a different fault from a
 * slow application. 0x1C32:06 is the slave's own calculation and copy
 * time, the closest thing to a datasheet number for how long a cycle
 * costs it. And 0x1C32:11/12/13 are its three complaint counters, of
 * which 12, cycle exceeded, is the one already known to move.
 *
 * The period list is in microseconds so the boundary can be walked at
 * finer than millisecond resolution. Default steps across it: 1000, 1100,
 * 1200, 1300, 1500, 1800, 2000, 3000. Everything is far below the 99.9 ms
 * watchdog, so no step here can be a timeout.
 *
 * Usage: why_1khz <iface> [axis 0-5, or -1 for all six] [secs_per_step]
 *                  [p1,p2,... in us]
 *   axis -1 drives all six through hs_interlock, which is the load the
 *   daemon actually puts on the slave.
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
#define IN_STA 30

#define SM2_BASE 0x0810          /* SM registers are 0x0800 + 8n */
#define SM2_STATUS (SM2_BASE + 5)

#define ANG_LO   1100
#define ANG_HI   1600
#define AMP       180
#define MOVED      30

static int periods_us[24] = {1000, 1100, 1200, 1300, 1500, 1800, 2000, 3000};
static int nper = 8;

static ecx_contextt ctx;
static uint8 IOmap[8192];
static int16_t *out, *in;
static uint16 adr;

static void pd(void)
{
   ecx_send_processdata(&ctx);
   ecx_receive_processdata(&ctx, EC_TIMEOUTRET);
}

static long now_us(void)
{
   struct timespec ts;
   clock_gettime(CLOCK_MONOTONIC, &ts);
   return ts.tv_sec * 1000000L + ts.tv_nsec / 1000;
}

static uint8 rd8(uint16 reg)
{
   uint8 v = 0;
   ecx_FPRD(&ctx.port, adr, reg, sizeof v, &v, EC_TIMEOUTRET);
   return v;
}

static int sdo32(uint16 idx, uint8 sub, uint32 *v)
{
   int size = 4;
   *v = 0;
   return ecx_SDOread(&ctx, 1, idx, sub, FALSE, &size, v, EC_TIMEOUTRXM) > 0;
}

static int sdo16(uint16 idx, uint8 sub, uint16 *v)
{
   int size = 2;
   *v = 0;
   return ecx_SDOread(&ctx, 1, idx, sub, FALSE, &size, v, EC_TIMEOUTRXM) > 0;
}

int main(int argc, char **argv)
{
   int axis = AX_MIDDLE, secs = 4, all = 0;
   int i, s, lock_fd, chk;
   uint32 calccopy = 0, mincyc = 0;

   if (argc < 2)
   { printf("usage: why_1khz <iface> [axis] [secs] [p1,p2,... us]\n"); return 1; }
   if (argc > 2) axis = atoi(argv[2]);
   if (argc > 3) secs = atoi(argv[3]);
   if (argc > 4)
   {
      char *tok = strtok(argv[4], ",");
      nper = 0;
      while (tok && nper < 24)
      {
         int v = atoi(tok);
         if (v < 200 || v > 90000)
         { printf("period %d us out of 200..90000\n", v); return 1; }
         periods_us[nper++] = v;
         tok = strtok(NULL, ",");
      }
      if (!nper) { printf("empty period list\n"); return 1; }
   }
   if (axis == -1) { all = 1; axis = AX_MIDDLE; }
   else if (axis < 0 || axis > 5)
   { printf("axis 0-5, or -1 for all six\n"); return 1; }
   if (secs < 1 || secs > 15) { printf("secs 1..15\n"); return 1; }

   lock_fd = hs_lock(20);
   if (lock_fd < 0) { printf("bus busy\n"); return 3; }
   if (!ecx_init(&ctx, argv[1]))
   { printf("ecx_init failed\n"); hs_unlock(lock_fd); return 2; }
   if (ecx_config_init(&ctx) <= 0)
   { printf("no slave\n"); ecx_close(&ctx); hs_unlock(lock_fd); return 2; }
   adr = ctx.slavelist[1].configadr;

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
   { printf("never reached OP: AL=0x%04x\n", ctx.slavelist[1].ALstatuscode);
     ecx_close(&ctx); hs_unlock(lock_fd); return 5; }
   ctx.slavelist[1].mbx_proto = ECT_MBXPROT_COE;   /* mailbox works in OP */

   for (i = 0; i < 200; i++) { pd(); osal_usleep(1000); }

   sdo32(0x1C32, 5, &mincyc);
   sdo32(0x1C32, 6, &calccopy);
   printf("SM2 control 0x%02x  status 0x%02x at rest\n",
          rd8(SM2_BASE + 4), rd8(SM2_STATUS));
   printf("0x1C32:05 min cycle    = %u ns  (what it advertises)\n", mincyc);
   printf("0x1C32:06 calc+copy    = %u ns  (what it says a cycle costs it)\n",
          calccopy);
   printf("STA=[%d %d %d %d %d %d] ANG=[%d %d %d %d %d %d]\n",
          in[IN_STA], in[IN_STA+1], in[IN_STA+2], in[IN_STA+3],
          in[IN_STA+4], in[IN_STA+5],
          in[IN_ANG], in[IN_ANG+1], in[IN_ANG+2], in[IN_ANG+3],
          in[IN_ANG+4], in[IN_ANG+5]);

   if (in[IN_STA + axis] == 5 || in[IN_STA + axis] == 6 ||
       in[IN_CUR + axis] > 400)
   { printf("axis %d stalled\n", axis); ecx_close(&ctx);
     hs_unlock(lock_fd); return 6; }
   for (i = 0; i < 6; i++)
      if (in[IN_STA + i] == 7)
      { printf("axis %d asleep (STA=7)\n", i); ecx_close(&ctx);
        hs_unlock(lock_fd); return 7; }

   printf("\nSM2 status byte, sampled every frame. bufState 3 means "
          "\"nothing written since the last read\";\n0-2 name which of the "
          "three buffers was written last. rdInUse is the PDI - the hand's\n"
          "own controller - holding a buffer open to read it.\n\n");
   printf("period_us  frames  %s  maxCUR  meas_us  cycExc(+)  "
          "bufState 0/1/2/3        rdInUse  wrInUse\n",
          all ? "minD" : "dANG");

   out[0] = 1;
   for (s = 0; s < nper; s++)
   {
      int p = periods_us[s];
      int16_t before = in[IN_ANG + axis], tgt;
      int16_t before6[6], tgt6[6];
      char why[256] = {0};
      int k, max_dev = 0, max_cur = 0, frames = 0;
      for (k = 0; k < 6; k++) before6[k] = in[IN_ANG + k];
      long bs[4] = {0, 0, 0, 0}, rdin = 0, wrin = 0;
      uint32 meas = 0;
      uint16 exc0 = 0, exc1 = 0;
      long t0, t;

      sdo16(0x1C32, 12, &exc0);
      if (all)
      {
         for (k = 0; k < 6; k++)
         {
            int16_t b = before6[k];
            int16_t t = (int16_t)(b > (ANG_LO + ANG_HI) / 2 ? b - AMP
                                                            : b + AMP);
            if (t < ANG_LO) t = ANG_LO;
            if (t > ANG_HI) t = ANG_HI;
            tgt6[k] = t;
         }
         /* six axes puts the index/thumb collision back in play, so the
            whole vector goes through the same gate every caller uses */
         hs_interlock(tgt6, &in[IN_ANG], why, sizeof why);
         for (k = 0; k < 6; k++) out[HS_OUT_TARGET + k] = tgt6[k];
         tgt = tgt6[axis];
      }
      else
      {
         tgt = (int16_t)(before > (ANG_LO + ANG_HI) / 2 ? before - AMP
                                                        : before + AMP);
         if (tgt < ANG_LO) tgt = ANG_LO;
         if (tgt > ANG_HI) tgt = ANG_HI;
         out[HS_OUT_TARGET + axis] = tgt;
      }

      t0 = now_us();
      while ((t = now_us() - t0) < (long)secs * 1000000L)
      {
         uint8 st;
         int d;
         pd();
         frames++;
         /* read SM2's status straight after the frame that just wrote it,
            so the sample sits at the same point in every cycle */
         st = rd8(SM2_STATUS);
         bs[(st >> 4) & 3]++;
         if ((st >> 6) & 1) rdin++;
         if ((st >> 7) & 1) wrin++;
         if (all)
         {
            int worst = 32767;   /* the slowest axis decides */
            for (k = 0; k < 6; k++)
            {
               int dk = in[IN_ANG + k] - before6[k];
               if (dk < 0) dk = -dk;
               if (dk < worst) worst = dk;
               if (in[IN_CUR + k] > max_cur) max_cur = in[IN_CUR + k];
            }
            if (worst > max_dev) max_dev = worst;
         }
         else
         {
            d = in[IN_ANG + axis] - before;
            if (d < 0) d = -d;
            if (d > max_dev) max_dev = d;
            if (in[IN_CUR + axis] > max_cur) max_cur = in[IN_CUR + axis];
         }
         osal_usleep(p);
      }
      sdo32(0x1C32, 2, &meas);
      sdo16(0x1C32, 12, &exc1);
      ecx_readstate(&ctx);

      printf("%-10d %-7d %-5d %-7d %-8u %-10u %5ld/%5ld/%5ld/%5ld  %-8ld %-8ld%s\n",
             p, frames, max_dev, max_cur, meas / 1000,
             (unsigned)(uint16)(exc1 - exc0),
             bs[0], bs[1], bs[2], bs[3], rdin, wrin,
             max_dev > MOVED ? "  MOVED" : "");
      if (ctx.slavelist[1].state != EC_STATE_OPERATIONAL)
      { printf("           left OPERATIONAL (AL=0x%04x) - stopping\n",
               ctx.slavelist[1].ALstatuscode); break; }
   }

   out[0] = 0;
   for (i = 1; i <= 6; i++) out[i] = HS_TGT_HOLD;
   for (i = 0; i < 200; i++) { pd(); osal_usleep(1000); }
   ecx_close(&ctx);
   hs_unlock(lock_fd);

   printf("\nHow to read it: if the rows that fail differ from the rows that\n"
          "work in the bufState/rdInUse columns, the sync manager is where\n"
          "it breaks and the application never completing a read is the\n"
          "mechanism. If those columns look the same either way, the fault\n"
          "is above the SM and meas_us versus period_us is the next clue.\n");
   return 0;
}
