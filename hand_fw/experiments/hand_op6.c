#include "soem/soem.h"
#include <stdio.h>
#include <stdint.h>
#include <string.h>

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

static void show(const char *tag)
{
   int16_t *in = (int16_t *)ctx.slavelist[1].inputs;
   printf("%s ANG=[%d %d %d %d %d %d] CUR=[%d %d %d %d %d %d] STA=[%d %d %d %d %d %d]\n",
      tag, in[6], in[7], in[8], in[9], in[10], in[11],
           in[18], in[19], in[20], in[21], in[22], in[23],
           in[30], in[31], in[32], in[33], in[34], in[35]);
   fflush(stdout);
}

int main(void)
{
   int16_t *out;
   int i, chk;
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
   printf("OP=%d state=0x%02x alstatus=0x%04x\n",
          ctx.slavelist[1].state == EC_STATE_OPERATIONAL,
          ctx.slavelist[1].state, ctx.slavelist[1].ALstatuscode);
   if (ctx.slavelist[1].state != EC_STATE_OPERATIONAL) { printf("no OP\n"); return 2; }
   out = (int16_t *)ctx.slavelist[1].outputs;

   /* F1 convention: 0 = extended, 1000 = flexed */
   memset(out, 0, ctx.slavelist[1].Obytes);
   out[0] = 1;
   for (i = 1; i <= 6; i++)  out[i] = 0;    /* all open */
   for (i = 7; i <= 12; i++) out[i] = 1000;
   for (i = 13; i <= 18; i++) out[i] = 1000;
   show("before-open");
   cyc(3000);
   show("after-open ");

   ecx_close(&ctx);
   return 0;
}
