/* hand_ctl - Inspire RH56F1 EtherCAT control core (SOEM)
 *
 * Usage:
 *   hand_ctl state
 *   hand_ctl pose P R M I TB TR [force] [speed]
 *       targets 0..2000 (0=closed, 2000=open), -1 = leave axis unchanged
 *   $ECAT_IFACE selects the NIC (default eth0). Confirm with ecat_scan
 *   before assuming - the hand has answered on a different interface on
 *   every host that has driven it.
 *
 * Behavior encodes the reverse-engineered F1 semantics:
 *   - boot lands all axes in STATUS=7 (standby); wiggle around current
 *     position wakes them before a pose is accepted
 *   - targets execute when the master disconnects (SM watchdog), so this
 *     tool writes the pose, holds briefly, then exits
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

#define WAKE_MS_MAX 12000
#define HOLD_MS 1200

static ecx_contextt ctx;
static uint8 IOmap[4096];

static void pd(void)
{
   ecx_send_processdata(&ctx);
   ecx_receive_processdata(&ctx, EC_TIMEOUTRET);
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
   int16_t tgt[6] = {-1, -1, -1, -1, -1, -1};
   int force = 500, speed = 800;
   int do_pose = 0, i, chk, t, lock_fd, guarded = 0;
   const char *iface;
   char why[256] = {0};

   if (argc < 2) fail("usage: hand_ctl state | pose P R M I TB TR [force] [speed]");
   if (!strcmp(argv[1], "pose"))
   {
      if (argc < 8) fail("pose needs 6 targets");
      do_pose = 1;
      for (i = 0; i < 6; i++)
      {
         long v = strtol(argv[2 + i], NULL, 10);
         if (v != -1 && (v < 0 || v > 2000)) fail("target out of range (0..2000 or -1)");
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
   ctx.slavelist[1].mbx_proto = 0; /* dead CoE mailbox on this SSC build */
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
   for (i = 1; i <= 6; i++)  out[i] = -1;
   hs_profile(out, force, speed);
   for (t = 0; t < 300; t++) { pd(); osal_usleep(1000); }

   if (do_pose)
   {
      /* wake axes stuck in STATUS=7 by wiggling around current position */
      int asleep = 1;
      for (t = 0; t < WAKE_MS_MAX && asleep; t++)
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
         osal_usleep(1000);
      }
      /* driver-level gate: nothing reaches the PDO unchecked */
      guarded  = hs_stall_relief(tgt, &in[18], &in[30], &in[6], why, sizeof why);
      guarded += hs_interlock(tgt, &in[6], why, sizeof why);
      /* write the requested pose; execution happens after we disconnect */
      for (i = 0; i < 6; i++) out[HS_OUT_TARGET + i] = tgt[i];
      for (t = 0; t < HOLD_MS; t++) { pd(); osal_usleep(1000); }
   }

   printf("{\"ok\":true,\"mode\":\"%s\",\"guarded\":%d,\"guard_note\":\"%s\",",
          do_pose ? "pose" : "state", guarded, why);
   jarr("pos", &in[0], 6, 0);
   jarr("ang", &in[6], 6, 0);
   jarr("frc", &in[12], 6, 0);
   jarr("cur", &in[18], 6, 0);
   jarr("err", &in[24], 6, 0);
   jarr("sta", &in[30], 6, 0);
   jarr("tmp", &in[36], 6, 1);
   printf("}\n");
   ecx_close(&ctx);
   hs_unlock(lock_fd);
   return 0;
}
