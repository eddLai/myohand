/* ecat_persistent_probe - does RH56F1 need a disconnect to move?
 *
 * hand_ctl / hand_set write a pose and exit, on the assumption that the
 * firmware only applies the target once the master disconnects (SM
 * watchdog). This probe holds the EtherCAT link open in OPERATIONAL and
 * oscillates one axis's target while logging ANGLEACT every cycle, so we
 * can see directly whether the axis tracks the target live or sits frozen
 * until the link closes. See PERSISTENT_OP_PLAN.md for how to read the log.
 *
 * Usage: ecat_persistent_probe <iface> [axis 0-5] [duration_s]
 *   axis: 0=pinky 1=ring 2=middle(default) 3=index 4=thumb_bend 5=thumb_rot
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

int main(int argc, char **argv)
{
   int16_t *out, *in;
   int axis = 2, duration_s = 10;
   int i, chk, lock_fd;
   int16_t center, amp = 100;
   long t0, t, last_flip = 0;
   int high = 0;

   if (argc < 2)
   {
      printf("usage: ecat_persistent_probe <iface> [axis 0-5] [duration_s]\n");
      return 1;
   }
   if (argc > 2) axis = atoi(argv[2]);
   if (argc > 3) duration_s = atoi(argv[3]);
   if (axis < 0 || axis > 5) { printf("axis must be 0-5\n"); return 1; }

   lock_fd = hs_lock(20);
   if (lock_fd < 0) { printf("bus busy: another master holds the hand\n"); return 3; }
   if (!ecx_init(&ctx, argv[1])) { printf("ecx_init fail (need CAP_NET_RAW, or wrong iface)\n"); return 1; }
   if (ecx_config_init(&ctx) <= 0) { printf("no EtherCAT slave (check link/power/iface)\n"); return 1; }
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
   if (ctx.slavelist[1].state != EC_STATE_OPERATIONAL) { printf("slave refused OPERATIONAL\n"); return 2; }

   out = (int16_t *)ctx.slavelist[1].outputs;
   in  = (int16_t *)ctx.slavelist[1].inputs;

   memset(out, 0, ctx.slavelist[1].Obytes);
   out[0] = 1;
   for (i = 1; i <= 6; i++) out[i] = -1;
   hs_profile(out, 500, 500); /* gentle: default force, slow speed */
   for (i = 0; i < 300; i++) { pd(); osal_usleep(1000); }

   center = hs_ang_to_target(in[6 + axis]);
   if (center < amp) center = amp;
   if (center > 2000 - amp) center = 2000 - amp;
   printf("axis=%d center=%d amp=%d duration_s=%d\n", axis, center, amp, duration_s);
   printf("phase,t_ms,target,angleact\n");
   fflush(stdout);

   t0 = now_ms();
   while ((t = now_ms() - t0) < (long)duration_s * 1000)
   {
      if (t - last_flip >= 500) { high = !high; last_flip = t; }
      out[HS_OUT_TARGET + axis] = high ? (int16_t)(center + amp) : (int16_t)(center - amp);
      pd();
      printf("open,%ld,%d,%d\n", t, out[HS_OUT_TARGET + axis], in[6 + axis]);
      osal_usleep(20000); /* 20ms log cadence; pd() itself still ~1kHz-capable */
      fflush(stdout);
   }

   printf("preclose,%ld,%d,%d\n", now_ms() - t0, out[HS_OUT_TARGET + axis], in[6 + axis]);

   /* return to a neutral hold before dropping the link */
   for (i = 1; i <= 6; i++) out[i] = -1;
   for (i = 0; i < 200; i++) pd();

   ecx_close(&ctx);
   /* one more read attempt is meaningless post-close (no more PDO cycles),
      so the log's last useful line is "preclose" above; compare it against
      ANGLEACT read by a fresh `hand_ctl state` call afterward to see if the
      axis moved only after this process exited. */

   hs_unlock(lock_fd);
   return 0;
}
