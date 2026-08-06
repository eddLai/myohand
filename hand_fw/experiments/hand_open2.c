#include "soem/soem.h"
#include <stdio.h>
#include <stdint.h>
#include <string.h>

static ecx_contextt ctx;
static uint8 IOmap[4096];

int main(void)
{
   int16_t *out, *in;
   int i, chk, t;
   if (!ecx_init(&ctx, "enp59s0f1")) { printf("init fail\n"); return 1; }
   if (ecx_config_init(&ctx) <= 0) { printf("no slaves\n"); return 1; }
   ctx.slavelist[1].mbx_proto = 0;
   ecx_config_map_group(&ctx, IOmap, 0);
   ecx_statecheck(&ctx, 0, EC_STATE_SAFE_OP, EC_TIMEOUTSTATE * 4);
   ctx.slavelist[0].state = EC_STATE_OPERATIONAL;
   ecx_send_processdata(&ctx);
   ecx_receive_processdata(&ctx, EC_TIMEOUTRET);
   ecx_writestate(&ctx, 0);
   chk = 200;
   do {
      ecx_send_processdata(&ctx);
      ecx_receive_processdata(&ctx, EC_TIMEOUTRET);
      ecx_statecheck(&ctx, 0, EC_STATE_OPERATIONAL, 50000);
   } while (chk-- && (ctx.slavelist[0].state != EC_STATE_OPERATIONAL));
   ecx_readstate(&ctx);
   if (ctx.slavelist[1].state != EC_STATE_OPERATIONAL) { printf("no OP\n"); return 2; }
   out = (int16_t *)ctx.slavelist[1].outputs;
   in  = (int16_t *)ctx.slavelist[1].inputs;

   memset(out, 0, ctx.slavelist[1].Obytes);
   out[0] = 1;
   for (i = 7; i <= 12; i++)  out[i] = 1000;  /* force */
   for (i = 13; i <= 18; i++) out[i] = 1000;  /* speed */

   /* wiggle targets to wake axes; watch STATUS */
   for (t = 0; t < 20000; t++)
   {
      int16_t tgt = ((t / 500) % 2) ? 900 : 1100;
      for (i = 1; i <= 6; i++) out[i] = tgt;
      ecx_send_processdata(&ctx);
      ecx_receive_processdata(&ctx, EC_TIMEOUTRET);
      if (t % 2000 == 0)
      {
         printf("t=%2ds STA=[%d %d %d %d %d %d] ANG=[%d %d %d %d %d %d] CUR=[%d %d %d]\n",
                t / 1000, in[30], in[31], in[32], in[33], in[34], in[35],
                in[6], in[7], in[8], in[9], in[10], in[11],
                in[18], in[19], in[20]);
         fflush(stdout);
      }
      osal_usleep(1000);
   }
   /* park target = near-full open, executes on disconnect */
   for (i = 1; i <= 6; i++) out[i] = 2000;
   for (t = 0; t < 1000; t++)
   {
      ecx_send_processdata(&ctx);
      ecx_receive_processdata(&ctx, EC_TIMEOUTRET);
      osal_usleep(1000);
   }
   printf("parked open target, exiting\n");
   ecx_close(&ctx);
   return 0;
}
