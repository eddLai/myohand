/* compliant_op - drive the hand the way the standard says, and see.
 *
 * ecat_interrogate settled what this slave declares about itself:
 *
 *   0x1C32:01 = 1        SM-Synchron. The application applies its outputs
 *                        on the SM2 event - on data arrival - not on a
 *                        Sync0 interrupt and not on a watchdog.
 *   0x1C32:05 = 100000   minimum cycle 100 us, so 1 kHz is legal with room.
 *   0x0420    = 99.9 ms  the SM watchdog every pose has been paying for.
 *   mbx_proto = 0x0004   CoE is alive; the mailbox answers every SDO.
 *
 * A slave that says SM-Synchron should move on the frame, so the fact
 * that it does not means the frames we send are not producing the SM2
 * event it is waiting for. The suspect is size. Three sources disagree
 * about how large the output image is, and no two of them match:
 *
 *   EEPROM SM2 length             36 bytes
 *   RxPDO 0x1601, 19 x 16 bit     38 bytes
 *   SOEM mapping over CoE         18 bytes
 *
 * Every binary in this tree zeroes mbx_proto before mapping, which makes
 * SOEM ignore CoE and size the image from the SII - 38 bytes, the middle
 * answer, and the only one that has ever been tried. A sync manager
 * signals its event when the configured length has been written. If the
 * application is compiled for a different length than the one we set,
 * the event never completes and outputs are never copied - which is
 * exactly the symptom: OPERATIONAL held, telemetry live, working counter
 * fine, zero motion, zero milliamps, and then motion once the watchdog
 * trips and the firmware falls back.
 *
 * So this tool runs the same hold test three ways and lets the size be
 * the variable:
 *
 *   coe    leave mbx_proto as the slave advertised it - a compliant
 *          master, which nothing in this repo has ever been
 *   sii     mbx_proto = 0, reproducing what the drivers do (control)
 *   sm36    map compliantly, then force SM2 to the EEPROM's own 36
 *
 * It also fixes the cadence bug on the way past: process data goes out
 * every millisecond through the whole state transition, rather than once
 * per statecheck timeout as handd.c:668 and the probe do.
 *
 * Targets are commanded in ANGLEACT units, not the 0..2000 scale the
 * README claims. The 2026-08-06 traces settled that too: park 1100 gave
 * ANGLEACT 1101, park 1272 gave 1274, and park 739 drove into the closed
 * stop at 896. The axis is nudged +-150 from where it rests and clamped
 * well inside [950, 1700] so neither end can reach a stop.
 *
 * Usage: compliant_op <iface> [coe|sii|sm36] [axis 0-5] [hold_s]
 */
#include "soem/soem.h"
#include "hand_safety.h"
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>
#include <time.h>

#define IN_POS 0
#define IN_ANG 6
#define IN_CUR 18
#define IN_ERR 24
#define IN_STA 30

#define AMP        150    /* ANGLEACT counts to swing off the rest pose */
#define ANG_LO     950    /* stay clear of the closed stop near 896 */
#define ANG_HI    1700    /* stay clear of the open stop near 1790+ */
#define MOVED       30    /* the deviation every earlier tool calls motion */
#define LOG_MS      20
#define MAX_SAMPLES 4000
#define WAKE_MS_MAX 12000

static ecx_contextt ctx;
static uint8 IOmap[8192];
static int16_t *out, *in;
static uint16 adr;
static int owords;        /* how many int16 the output image actually has */

typedef struct { long t; int16_t tgt, ang, cur, sta; } sample_t;
static sample_t samples[MAX_SAMPLES];
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

static uint16 rd16(uint16 reg)
{
   uint16 v = 0;
   ecx_FPRD(&ctx.port, adr, reg, sizeof v, &v, EC_TIMEOUTRET);
   return v;
}

/* Writing a target into a word the mapped image does not contain would
   scribble past slavelist[1].outputs, so every write goes through here. */
static void put(int word, int16_t v)
{
   if (word >= 0 && word < owords) out[word] = v;
}

static void park_hold(void)
{
   int i;
   for (i = 1; i <= 6; i++) put(i, HS_TGT_HOLD);
}

int main(int argc, char **argv)
{
   const char *mode = "coe";
   int axis = AX_MIDDLE, hold_s = 6;
   int i, lock_fd, chk, wake_ms = 0, wake_ok = 1;
   int16_t ang_start, tgt_hi, tgt_lo, cur_tgt;
   int max_dev = 0, max_cur = 0;
   long t0, t, last_flip = 0, last_log = -LOG_MS;
   int high = 0;

   if (argc < 2)
   {
      printf("usage: compliant_op <iface> [coe|sii|sm36] [axis 0-5] "
             "[hold_s]\n");
      return 1;
   }
   if (argc > 2) mode = argv[2];
   if (argc > 3) axis = atoi(argv[3]);
   if (argc > 4) hold_s = atoi(argv[4]);
   if (axis < 0 || axis > 5) { printf("axis must be 0-5\n"); return 1; }
   if (hold_s < 1 || hold_s > 30) { printf("hold_s must be 1..30\n"); return 1; }
   if (strcmp(mode, "coe") && strcmp(mode, "sii") && strcmp(mode, "sm36"))
   { printf("mode must be coe, sii or sm36\n"); return 1; }

   lock_fd = hs_lock(20);
   if (lock_fd < 0) { printf("bus busy: another master holds the hand\n"); return 3; }

   if (!ecx_init(&ctx, argv[1]))
   { printf("ecx_init failed on %s\n", argv[1]); hs_unlock(lock_fd); return 2; }
   if (ecx_config_init(&ctx) <= 0)
   {
      printf("no EtherCAT slave on %s\n", argv[1]);
      ecx_close(&ctx); hs_unlock(lock_fd); return 2;
   }
   adr = ctx.slavelist[1].configadr;

   printf("=== mode=%s  iface=%s  axis=%d ===\n", mode, argv[1], axis);
   printf("slave advertises mbx_proto=0x%04x (CoE=%d)\n",
          ctx.slavelist[1].mbx_proto,
          (ctx.slavelist[1].mbx_proto & ECT_MBXPROT_COE) ? 1 : 0);

   /* THE variable. sii reproduces what every driver here does; coe and
      sm36 leave the advertisement alone and let SOEM map over CoE. */
   if (!strcmp(mode, "sii"))
   {
      ctx.slavelist[1].mbx_proto = 0;
      printf("  -> mbx_proto forced to 0 (what hand_ctl/handd/the probe do)\n");
   }
   else
      printf("  -> mbx_proto left as advertised (compliant; never tried "
             "before)\n");

   ecx_config_map_group(&ctx, IOmap, 0);

   out = (int16_t *)ctx.slavelist[1].outputs;
   in  = (int16_t *)ctx.slavelist[1].inputs;
   owords = (int)(ctx.slavelist[1].Obytes / 2);

   printf("mapped: Obytes=%u Obits=%u  Ibytes=%u Ibits=%u  (owords=%d)\n",
          ctx.slavelist[1].Obytes, ctx.slavelist[1].Obits,
          ctx.slavelist[1].Ibytes, ctx.slavelist[1].Ibits, owords);

   if (!out || !in || owords < 7 || ctx.slavelist[1].Ibytes < 36 * 2)
   {
      printf("image too small to hold enable + 6 targets, or telemetry "
             "shorter than the ANGLEACT/STATUS words this test reads. "
             "Stopping before writing anything.\n");
      ecx_close(&ctx); hs_unlock(lock_fd); return 4;
   }

   /* A zeroed output buffer reads as "close every axis". Park holds
      before the first frame leaves, with enable still 0. */
   memset(out, 0, ctx.slavelist[1].Obytes);
   park_hold();

   printf("SM2 after map: start=0x%04x len=%u   SM3: start=0x%04x len=%u\n",
          rd16(0x0810), rd16(0x0812), rd16(0x0818), rd16(0x081A));

   if (!strcmp(mode, "sm36"))
   {
      uint16 l36 = 36;
      ecx_FPWR(&ctx.port, adr, 0x0812, sizeof l36, &l36, EC_TIMEOUTRET);
      printf("  -> SM2 length forced to the EEPROM's 36; reads back %u\n",
             rd16(0x0812));
   }

   ecx_statecheck(&ctx, 0, EC_STATE_SAFE_OP, EC_TIMEOUTSTATE * 4);
   ecx_readstate(&ctx);
   printf("SAFE_OP: state=0x%02x AL=0x%04x\n",
          ctx.slavelist[1].state, ctx.slavelist[1].ALstatuscode);

   /* The cadence fix. handd.c:668 and the probe send one frame per
      statecheck timeout - about 20 Hz - through the whole transition.
      Here process data keeps flowing at 1 kHz and the state is polled
      between frames, which is what a real master does. */
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
   printf("OP request (1 kHz throughout, %d ms): state=0x%02x AL=0x%04x\n",
          chk, ctx.slavelist[1].state, ctx.slavelist[1].ALstatuscode);
   if (ctx.slavelist[1].state != EC_STATE_OPERATIONAL)
   {
      printf("verdict: NEVER REACHED OP in mode %s - nothing was driven\n",
             mode);
      ecx_close(&ctx); hs_unlock(lock_fd); return 5;
   }

   cyc(200);
   printf("telemetry: STA=[%d %d %d %d %d %d] ANG=[%d %d %d %d %d %d]\n"
          "           CUR=[%d %d %d %d %d %d] ERR=[%d %d %d %d %d %d]\n",
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
      printf("axis %d is stalled (sta=%d cur=%d mA) - relieve it before "
             "probing\n", axis, in[IN_STA + axis], in[IN_CUR + axis]);
      ecx_close(&ctx); hs_unlock(lock_fd); return 6;
   }

   /* boot leaves the axes in STATUS=7 and a pose is ignored until they
      leave it; skip the wiggle entirely when nothing is asleep */
   for (i = 0; i < 6; i++) if (in[IN_STA + i] == 7) wake_ok = 0;
   if (!wake_ok)
   {
      int asleep = 1;
      printf("axes asleep (STATUS=7) - running the wake wiggle\n");
      for (wake_ms = 0; wake_ms < WAKE_MS_MAX && asleep; wake_ms++)
      {
         for (i = 0; i < 6; i++)
         {
            int16_t base = in[IN_ANG + i];
            if (base < 1000) base = 1000;
            if (base > 1700) base = 1700;
            put(1 + i, (int16_t)(base + (((wake_ms / 400) % 2) ? 60 : -60)));
         }
         pd();
         if (wake_ms % 200 == 0)
         {
            asleep = 0;
            for (i = 0; i < 6; i++) if (in[IN_STA + i] == 7) asleep = 1;
         }
         osal_usleep(1000);
      }
      wake_ok = !asleep;
      park_hold();
      cyc(200);
      printf("wake=%s after %d ms\n", wake_ok ? "ok" : "FAILED", wake_ms);
   }

   /* endpoints in ANGLEACT units, both inside the safe band */
   ang_start = in[IN_ANG + axis];
   tgt_hi = (int16_t)(ang_start + AMP);
   tgt_lo = (int16_t)(ang_start - AMP);
   if (tgt_hi > ANG_HI) { tgt_hi = ANG_HI; }
   if (tgt_lo < ANG_LO) { tgt_lo = ANG_LO; }
   if (tgt_hi - tgt_lo < 60)
   { tgt_lo = ANG_LO; tgt_hi = (int16_t)(ANG_LO + 2 * AMP); }

   printf("ang_start=%d  swinging between %d and %d, enable=1, link held "
          "open for %d s at 1 kHz\n", ang_start, tgt_lo, tgt_hi, hold_s);

   put(0, 1);                       /* ENABLE_SET */
   cur_tgt = tgt_hi;
   put(1 + axis, cur_tgt);

   t0 = now_ms();
   while ((t = now_ms() - t0) < (long)hold_s * 1000)
   {
      if (t - last_flip >= 1500)    /* long dwell: a slow axis still shows */
      {
         high = !high;
         last_flip = t;
         cur_tgt = high ? tgt_hi : tgt_lo;
         put(1 + axis, cur_tgt);
      }
      pd();
      if (t - last_log >= LOG_MS)
      {
         int d = in[IN_ANG + axis] - ang_start;
         if (d < 0) d = -d;
         if (d > max_dev) max_dev = d;
         if (in[IN_CUR + axis] > max_cur) max_cur = in[IN_CUR + axis];
         if (nsamp < MAX_SAMPLES)
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
   printf("end of hold: state=0x%02x AL=0x%04x  "
          "WDcount(procdata)=%u WDstatus=0x%04x\n",
          ctx.slavelist[1].state, ctx.slavelist[1].ALstatuscode,
          rd16(0x0442) & 0xFF, rd16(0x0440));

   /* leave it where it started, powered down, before dropping the link */
   put(0, 0);
   park_hold();
   cyc(200);
   ecx_close(&ctx);
   hs_unlock(lock_fd);

   printf("\nmax_dANG=%d (moved > %d)  max_CUR=%d mA\n",
          max_dev, MOVED, max_cur);
   if (max_dev > MOVED)
      printf("verdict: MOVED IN OP - mode %s produces the SM2 event the "
             "slave is waiting for. The disconnect was never the trigger; "
             "the output image size was.\n", mode);
   else if (max_cur > 20)
      printf("verdict: ENERGISED BUT STILL - the outputs were applied "
             "(current flowed) but the axis did not travel. Different "
             "finding from silence; check the target scale.\n");
   else
      printf("verdict: NOTHING - flat and unpowered, same as every "
             "earlier run. Mode %s is not the missing piece.\n", mode);

   printf("t_ms,target,angleact,cur,sta\n");
   for (i = 0; i < nsamp; i++)
      printf("%ld,%d,%d,%d,%d\n", samples[i].t, samples[i].tgt,
             samples[i].ang, samples[i].cur, samples[i].sta);
   return 0;
}
