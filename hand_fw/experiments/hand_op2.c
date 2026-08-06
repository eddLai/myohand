#include "soem/soem.h"
#include <stdio.h>
#include <stdint.h>
#include <string.h>

static ecx_contextt ctx;
static uint8 IOmap[4096];

static void cycle_ms(int ms)
{
   int i;
   for (i = 0; i < ms; i++)
   {
      ecx_send_processdata(&ctx);
      ecx_receive_processdata(&ctx, EC_TIMEOUTRET);
      osal_usleep(1000);
   }
}

static void show(const char *tag)
{
   int16_t *in = (int16_t *)ctx.slavelist[1].inputs;
   printf("%s POS=[%d %d %d %d %d %d] ANG=[%d %d %d %d %d %d]\n",
      tag, in[0],in[1],in[2],in[3],in[4],in[5], in[6],in[7],in[8],in[9],in[10],in[11]);
   printf("%s FRC=[%d %d %d %d %d %d] CUR=[%d %d %d %d %d %d]\n",
      tag, in[12],in[13],in[14],in[15],in[16],in[17], in[18],in[19],in[20],in[21],in[22],in[23]);
   printf("%s ERR=[%d %d %d %d %d %d] STA=[%d %d %d %d %d %d] TMP=[%d %d %d %d %d %d]\n",
      tag, in[24],in[25],in[26],in[27],in[28],in[29],
           in[30],in[31],in[32],in[33],in[34],in[35],
           in[36],in[37],in[38],in[39],in[40],in[41]);
   fflush(stdout);
}

int main(void)
{
   int16_t *out;
   int i, chk, v;
   int16_t enables[3] = {1, 0x3F, -1};

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
   printf("OP=%d\n", ctx.slavelist[1].state == EC_STATE_OPERATIONAL);
   if (ctx.slavelist[1].state != EC_STATE_OPERATIONAL) return 2;

   out = (int16_t *)ctx.slavelist[1].outputs;
   memset(out, 0, ctx.slavelist[1].Obytes);
   cycle_ms(300);
   show("idle ");

   for (v = 0; v < 3; v++)
   {
      /* [ENABLE, ANGLESET x6, FORCESET x6, SPEEDSET x6] = 19 shorts */
      out[0] = enables[v];
      for (i = 1; i <= 6; i++)  out[i] = -1;
      for (i = 7; i <= 12; i++) out[i] = 300;
      for (i = 13; i <= 18; i++) out[i] = 400;
      cycle_ms(800);

      for (i = 1; i <= 6; i++) out[i] = 1000;  /* open */
      cycle_ms(2500);
      printf("== enable=%d after OPEN ==\n", enables[v]);
      show("open ");

      out[1] = 0; out[2] = 0; out[3] = 1000; out[4] = 0; out[5] = 0; out[6] = -1;
      cycle_ms(2500);
      printf("== enable=%d after MIDDLE ==\n", enables[v]);
      show("mid  ");

      int16_t *in = (int16_t *)ctx.slavelist[1].inputs;
      if (in[6] < 500 && in[7] < 500) { printf("MOVED with enable=%d\n", enables[v]); break; }
   }
   ecx_close(&ctx);
   return 0;
}
