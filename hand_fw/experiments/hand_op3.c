#include "soem/soem.h"
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>

static ecx_contextt ctx;
static uint8 IOmap[4096];

static void cyc(int ms)
{
   int i;
   for (i = 0; i < ms; i++)
   {
      ecx_send_processdata(&ctx);
      ecx_receive_processdata(&ctx, EC_TIMEOUTRET);
      osal_usleep(1000);
   }
}

int main(void)
{
   int16_t *out, *in;
   int i, chk, t;
   int16_t envals[12] = {0, 1, 2, 3, 15, 63, 255, 256, 257, 4096, -1, 165};

   if (!ecx_init(&ctx, "enp59s0f1")) return 1;
   if (ecx_config_init(&ctx) <= 0) return 1;
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
   printf("OP ok. baseline ANG=[%d %d %d %d %d %d]\n", in[6],in[7],in[8],in[9],in[10],in[11]);

   for (t = 0; t < 13; t++)
   {
      int16_t base[6], mx = 0, mxcur = 0;
      memset(out, 0, ctx.slavelist[1].Obytes);
      for (i = 1; i <= 6; i++) out[i] = -1;
      cyc(300);
      for (i = 0; i < 6; i++) base[i] = in[6+i];

      if (t < 12)
      {
         out[0] = envals[t];
         for (i = 7; i <= 12; i++) out[i] = 1000;   /* force max */
         for (i = 13; i <= 18; i++) out[i] = 1000;  /* speed max */
         for (i = 1; i <= 6; i++) out[i] = 0;       /* fist */
      }
      else
      {
         /* alt layout: enable LAST */
         for (i = 0; i <= 5; i++) out[i] = 0;
         for (i = 6; i <= 11; i++) out[i] = 1000;
         for (i = 12; i <= 17; i++) out[i] = 1000;
         out[18] = 1;
      }
      for (i = 0; i < 1500; i++)
      {
         int j;
         ecx_send_processdata(&ctx);
         ecx_receive_processdata(&ctx, EC_TIMEOUTRET);
         for (j = 0; j < 6; j++)
         {
            int16_t d = in[6+j] - base[j];
            if (d < 0) d = -d;
            if (d > mx) mx = d;
         }
         for (j = 0; j < 6; j++) if (in[18+j] > mxcur) mxcur = in[18+j];
         osal_usleep(1000);
      }
      printf("try=%d en=%d maxdANG=%d maxCUR=%d STA=[%d %d %d %d %d %d]\n",
             t, t < 12 ? envals[t] : 999, mx, mxcur,
             in[30],in[31],in[32],in[33],in[34],in[35]);
      fflush(stdout);
      if (mx > 30) { printf("*** MOVED at try %d ***\n", t); break; }
   }
   /* leave open pose command cleared */
   memset(out, 0, ctx.slavelist[1].Obytes);
   for (i = 1; i <= 6; i++) out[i] = -1;
   cyc(200);
   ecx_close(&ctx);
   return 0;
}
