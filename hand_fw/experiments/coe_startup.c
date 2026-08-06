/* coe_startup - do the startup configuration a real master does.
 *
 * Every tool in this tree, this one's predecessors included, has skipped
 * the same step. A conformant EtherCAT master goes
 *
 *   INIT -> mailbox SMs -> PRE_OP -> [CoE startup writes] -> SM/FMMU
 *        -> SAFE_OP -> valid outputs -> OP
 *
 * and we have never performed the bracketed step even once. The contents
 * of it come from the vendor's ESI XML, from its <InitCmd> entries, which
 * TwinCAT replays on every PreOP->SafeOP transition. Those entries live in
 * the XML file only - they are NOT in the EEPROM, and the EEPROM is the
 * only thing we can read off the wire. So the one configuration this hand
 * has never received is precisely the one we have no way to discover.
 *
 * One number says the omission is real rather than theoretical:
 *
 *   0x1C32:02 cycle time = 26981000 ns  ~= 27 ms
 *
 * That is what the slave believes its cycle is. We drive it at 1 ms. The
 * master is supposed to write that object during startup; nobody ever
 * did, so it holds whatever it held. Next to it, 0x1C32:12 - the cycle
 * exceeded counter - reads 8258 rather than zero. The slave has been
 * complaining about its cycle the whole time and no run ever read the
 * complaint.
 *
 * So this tool writes the startup objects and then runs the same hold
 * test, with the counters read on both sides of it. If a correct cycle
 * time is what SM-Synchron was waiting for, the axis moves with the link
 * up and the "watchdog is the only trigger" conclusion is wrong.
 *
 * Ordering, same constraint as syncmode_test: SDO needs the mailbox, so
 * mbx_proto stays as advertised while the writes happen; the process
 * image needs it zeroed, or SOEM maps 18 bytes and the slave rejects that
 * with AL=0x001e. Writes first, then clear the flag, then map.
 *
 * Everything written is restored before exit, and all of it is volatile
 * anyway - a power cycle undoes it.
 *
 * Usage: coe_startup <iface> [cycle_ns] [axis] [hold_s] [--assign]
 *   cycle_ns  what to write to 0x1C32:02 / 0x1C33:02 (default 1000000)
 *             0 skips the write, so the run is a pure control
 *   --assign  also try writing the PDO assignment (0x1C12:01=0x1601,
 *             0x1C13:01=0x1A00). The EEPROM claims PDO assign is not
 *             supported, but that same byte also claims SDO is not
 *             supported and it answers every SDO, so the claim is worth
 *             nothing and the attempt is worth making.
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

static void drain(void)
{
   int i;
   for (i = 0; i < 6; i++)
   {
      char *e = ecx_elist2string(&ctx);
      if (!e || !*e) break;
      printf("      soem: %s", e);
   }
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

static int wr32(uint16 idx, uint8 sub, uint32 v, const char *what)
{
   uint32 rb = 0;
   int wkc = ecx_SDOwrite(&ctx, 1, idx, sub, FALSE, sizeof v, &v,
                          EC_TIMEOUTRXM);
   printf("  write 0x%04X:%02d = %-10u %-22s wkc=%d", idx, sub, v, what, wkc);
   if (wkc <= 0) { printf("  REFUSED\n"); drain(); return 0; }
   rd32(idx, sub, &rb);
   printf("  reads back %u%s\n", rb, rb == v ? "" : "  <== NOT TAKEN");
   return rb == v;
}

static int wr16(uint16 idx, uint8 sub, uint16 v, const char *what)
{
   uint16 rb = 0;
   int wkc = ecx_SDOwrite(&ctx, 1, idx, sub, FALSE, sizeof v, &v,
                          EC_TIMEOUTRXM);
   printf("  write 0x%04X:%02d = 0x%04x     %-22s wkc=%d", idx, sub, v, what,
          wkc);
   if (wkc <= 0) { printf("  REFUSED\n"); drain(); return 0; }
   rd16(idx, sub, &rb);
   printf("  reads back 0x%04x%s\n", rb, rb == v ? "" : "  <== NOT TAKEN");
   return rb == v;
}

/* 0x1C32:11/12/13 are the slave's own complaint counters. Reading them on
   both sides of the run turns "it did nothing" into "it did nothing and
   here is what it thought was wrong". */
static void counters(const char *when)
{
   uint16 missed = 0, exceeded = 0, shortshift = 0;
   rd16(0x1C32, 11, &missed);
   rd16(0x1C32, 12, &exceeded);
   rd16(0x1C32, 13, &shortshift);
   printf("  counters %-7s SM-event-missed=%u  cycle-exceeded=%u  "
          "shift-too-short=%u\n", when, missed, exceeded, shortshift);
}

int main(int argc, char **argv)
{
   uint32 cycle_ns = 1000000;
   int axis = AX_MIDDLE, hold_s = 8, assign = 0;
   int i, lock_fd, chk, max_dev = 0, max_cur = 0, high = 0;
   uint32 c32_before = 0, c33_before = 0;
   uint16 mode32 = 0xFFFF;
   int16_t ang_start, tgt_hi, tgt_lo, cur_tgt;
   long t0, t, last_flip = 0, last_log = -LOG_MS;

   if (argc < 2)
   {
      printf("usage: coe_startup <iface> [cycle_ns] [axis] [hold_s] "
             "[--assign]\n");
      return 1;
   }
   for (i = 2; i < argc; i++)
   {
      if (!strcmp(argv[i], "--assign")) { assign = 1; continue; }
      if (i == 2) cycle_ns = (uint32)strtoul(argv[i], NULL, 10);
      else if (i == 3) axis = atoi(argv[i]);
      else if (i == 4) hold_s = atoi(argv[i]);
   }
   if (axis < 0 || axis > 5) { printf("axis must be 0-5\n"); return 1; }
   if (hold_s < 1 || hold_s > 30) { printf("hold_s 1..30\n"); return 1; }

   lock_fd = hs_lock(20);
   if (lock_fd < 0) { printf("bus busy\n"); return 3; }
   if (!ecx_init(&ctx, argv[1]))
   { printf("ecx_init failed\n"); hs_unlock(lock_fd); return 2; }
   if (ecx_config_init(&ctx) <= 0)
   { printf("no slave\n"); ecx_close(&ctx); hs_unlock(lock_fd); return 2; }

   ctx.slavelist[0].state = EC_STATE_PRE_OP;
   ecx_writestate(&ctx, 0);
   ecx_statecheck(&ctx, 0, EC_STATE_PRE_OP, EC_TIMEOUTSTATE * 2);
   ecx_readstate(&ctx);
   printf("=== PRE_OP, mailbox live: state=0x%02x mbx_proto=0x%04x ===\n",
          ctx.slavelist[1].state, ctx.slavelist[1].mbx_proto);

   rd16(0x1C32, 1, &mode32);
   rd32(0x1C32, 2, &c32_before);
   rd32(0x1C33, 2, &c33_before);
   printf("  as found: 0x1C32:01=%u (%s)  0x1C32:02=%u ns  0x1C33:02=%u ns\n",
          mode32, mode32 == 0 ? "Free Run" : mode32 == 1 ? "SM-Synchron"
                                                         : "other",
          c32_before, c33_before);
   counters("before");

   printf("\n=== the startup writes nobody has ever sent ===\n");
   if (cycle_ns > 0)
   {
      wr32(0x1C32, 2, cycle_ns, "SM2 cycle time ns");
      wr32(0x1C33, 2, cycle_ns, "SM3 cycle time ns");
   }
   else
      printf("  (cycle_ns=0: skipped, this run is a control)\n");

   if (assign)
   {
      /* PDO assignment normally requires SM2/SM3 disabled first; try the
         documented order and report rather than assume either way. */
      wr16(0x1C12, 0, 0, "RxPDO assign count=0");
      wr16(0x1C12, 1, 0x1601, "RxPDO assign[1]");
      wr16(0x1C12, 0, 1, "RxPDO assign count=1");
      wr16(0x1C13, 0, 0, "TxPDO assign count=0");
      wr16(0x1C13, 1, 0x1A00, "TxPDO assign[1]");
      wr16(0x1C13, 0, 1, "TxPDO assign count=1");
   }

   /* --- process data: needs mbx_proto cleared or the map is refused --- */
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
   printf("\nmapped: Obytes=%u Ibytes=%u\n",
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
      printf("verdict: refused OP after the startup writes\n");
      ecx_close(&ctx); hs_unlock(lock_fd); return 6;
   }

   cyc(300);
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
      printf("axis %d stalled - relieve first\n", axis);
      ecx_close(&ctx); hs_unlock(lock_fd); return 7;
   }

   ang_start = in[IN_ANG + axis];
   tgt_hi = (int16_t)(ang_start + AMP);
   tgt_lo = (int16_t)(ang_start - AMP);
   if (tgt_hi > ANG_HI) tgt_hi = ANG_HI;
   if (tgt_lo < ANG_LO) tgt_lo = ANG_LO;
   if (tgt_hi - tgt_lo < 80) { tgt_lo = ANG_LO; tgt_hi = ANG_LO + 2 * AMP; }

   printf("ang_start=%d  swinging %d <-> %d, enable=1, link held %d s at "
          "1 kHz\n", ang_start, tgt_lo, tgt_hi, hold_s);

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
            samples[nsamp].t = t; samples[nsamp].tgt = cur_tgt;
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

   /* back to PRE_OP to read the counters and undo the writes */
   ctx.slavelist[0].state = EC_STATE_PRE_OP;
   ecx_writestate(&ctx, 0);
   ecx_statecheck(&ctx, 0, EC_STATE_PRE_OP, EC_TIMEOUTSTATE * 2);
   ctx.slavelist[1].mbx_proto = ECT_MBXPROT_COE;
   printf("\n");
   counters("after");
   if (cycle_ns > 0)
   {
      wr32(0x1C32, 2, c32_before, "SM2 cycle restored");
      wr32(0x1C33, 2, c33_before, "SM3 cycle restored");
   }

   ecx_close(&ctx);
   hs_unlock(lock_fd);

   printf("\nmax_dANG=%d (moved > %d)  max_CUR=%d mA\n",
          max_dev, MOVED, max_cur);
   if (max_dev > MOVED)
      printf("verdict: MOVED IN OP. The startup configuration was the "
             "missing piece and the watchdog was never the trigger.\n");
   else if (max_cur > 20)
      printf("verdict: ENERGISED BUT STILL - outputs reached the motor "
             "without travel\n");
   else
      printf("verdict: NOTHING - the cycle time was not what it was "
             "waiting for. Check whether the counters above moved.\n");

   printf("t_ms,target,angleact,cur,sta\n");
   for (i = 0; i < nsamp; i++)
      printf("%ld,%d,%d,%d,%d\n", samples[i].t, samples[i].tgt,
             samples[i].ang, samples[i].cur, samples[i].sta);
   return 0;
}
