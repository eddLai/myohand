/* hand_set p r m i tb tr  -- set RH56F1 pose via EtherCAT (0=closed, 2000=open, -1=hold)
   Auto-wakes axes stuck in STATUS=7 by wiggling targets, then parks the pose
   and exits so the SM-watchdog timeout triggers execution. */
#include "soem/soem.h"
#include "hand_safety.h"
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

static ecx_contextt ctx;
static uint8 IOmap[4096];

static void pd(void)
{
   ecx_send_processdata(&ctx);
   ecx_receive_processdata(&ctx, EC_TIMEOUTRET);
}

int main(int argc, char **argv)
{
   int16_t *out, *in, tgt[6];
   int i, chk, t, need_wake, lock_fd, guarded;
   char why[256] = {0};
   int force = 500, speed = 1000;
   if (argc < 7) {
      printf("usage: hand_set p r m i tb tr [force] [speed]  (0-2000, -1=hold)\n");
      return 1;
   }
   for (i = 0; i < 6; i++) tgt[i] = (int16_t)atoi(argv[i + 1]);
   if (argc > 7) force = atoi(argv[7]);
   if (argc > 8) speed = atoi(argv[8]);
   if (force < 0) force = 0; if (force > 1000) force = 1000;
   if (speed < 50) speed = 50; if (speed > 1000) speed = 1000;

   lock_fd = hs_lock(20);
   if (lock_fd < 0) { printf("bus busy: another master holds the hand\n"); return 3; }
   const char *ifname = getenv("HAND_IFACE");
   if (!ifname || !*ifname) ifname = "enp17s0";
   if (!ecx_init(&ctx, ifname)) { printf("init fail\n"); return 1; }
   if (ecx_config_init(&ctx) <= 0) { printf("no slaves\n"); return 1; }
   ctx.slavelist[1].mbx_proto = 0;
   ecx_config_map_group(&ctx, IOmap, 0);
   ecx_statecheck(&ctx, 0, EC_STATE_SAFE_OP, EC_TIMEOUTSTATE * 4);
   ctx.slavelist[0].state = EC_STATE_OPERATIONAL;
   pd();
   ecx_writestate(&ctx, 0);
   chk = 200;
   do { pd(); ecx_statecheck(&ctx, 0, EC_STATE_OPERATIONAL, 50000); }
   while (chk-- && (ctx.slavelist[0].state != EC_STATE_OPERATIONAL));
   ecx_readstate(&ctx);
   if (ctx.slavelist[1].state != EC_STATE_OPERATIONAL) { printf("no OP\n"); return 2; }
   out = (int16_t *)ctx.slavelist[1].outputs;
   in  = (int16_t *)ctx.slavelist[1].inputs;

   memset(out, 0, ctx.slavelist[1].Obytes);
   out[0] = 1;
   hs_profile(out, force, speed);   /* shared per-axis force/speed profile */
   for (i = 1; i <= 6; i++)   out[i] = -1;
   for (t = 0; t < 200; t++) { pd(); osal_usleep(1000); }

   need_wake = 0;
   for (i = 0; i < 6; i++) if (in[30 + i] == 7) need_wake = 1;
   if (need_wake)
   {
      printf("waking axes (STA=[%d %d %d %d %d %d])...\n",
             in[30], in[31], in[32], in[33], in[34], in[35]);
      for (t = 0; t < 15000; t++)
      {
         int16_t w = ((t / 400) % 2) ? 950 : 1050;
         for (i = 1; i <= 6; i++) out[i] = w;
         pd();
         if (t % 1000 == 0)
         {
            need_wake = 0;
            for (i = 0; i < 6; i++) if (in[30 + i] == 7) need_wake = 1;
            if (!need_wake) break;
         }
         osal_usleep(1000);
      }
   }
   /* driver-level gate, identical rules to hand_ctl */
   guarded  = hs_stall_relief(tgt, &in[18], &in[30], &in[6], why, sizeof why);
   guarded += hs_interlock(tgt, &in[6], why, sizeof why);
   for (i = 1; i <= 6; i++) out[i] = tgt[i - 1];
   for (t = 0; t < 800; t++) { pd(); osal_usleep(1000); }
   if (guarded) printf("guard: %s\n", why);
   printf("parked [%d %d %d %d %d %d] STA=[%d %d %d %d %d %d] ANG=[%d %d %d %d %d %d]\n",
          tgt[0], tgt[1], tgt[2], tgt[3], tgt[4], tgt[5],
          in[30], in[31], in[32], in[33], in[34], in[35],
          in[6], in[7], in[8], in[9], in[10], in[11]);
   ecx_close(&ctx);
   hs_unlock(lock_fd);
   return 0;
}
