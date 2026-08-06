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

static void show_in(const char *tag)
{
   int16_t *in = (int16_t *)ctx.slavelist[1].inputs;
   int n = ctx.slavelist[1].Ibytes / 2, i;
   printf("%s IN[0..23]:", tag);
   for (i = 0; i < n && i < 24; i++) printf(" %d", in[i]);
   printf("\n");
   fflush(stdout);
}

int main(int argc, char **argv)
{
   char *iface = argc > 1 ? argv[1] : "enp59s0f1";
   int16_t *out;
   int i, chk;

   if (!ecx_init(&ctx, iface)) { printf("init fail\n"); return 1; }
   if (ecx_config_init(&ctx) <= 0) { printf("no slaves\n"); return 1; }
   printf("slaves: %d name=%s\n", ctx.slavecount, ctx.slavelist[1].name);

   /* vendor EEPROM PDO category disagrees with firmware SM sizes (36/96B):
      pre-set IO bits so ecx_map_sii skips the broken SII PDO recompute */
   ctx.slavelist[1].mbx_proto = 0; /* CoE mailbox is dead on this SSC build */
   ctx.slavelist[1].Obits = 36 * 8;
   ctx.slavelist[1].Ibits = 96 * 8;

   ecx_config_map_group(&ctx, IOmap, 0);
   printf("mapped: Obytes=%u Ibytes=%u\n",
          (unsigned)ctx.slavelist[1].Obytes, (unsigned)ctx.slavelist[1].Ibytes);

   ecx_statecheck(&ctx, 0, EC_STATE_SAFE_OP, EC_TIMEOUTSTATE * 4);
   ecx_readstate(&ctx);
   printf("after map: state=0x%02x alstatus=0x%04x\n",
          ctx.slavelist[1].state, ctx.slavelist[1].ALstatuscode);

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
   if (ctx.slavelist[1].state != EC_STATE_OPERATIONAL) { ecx_close(&ctx); return 2; }

   out = (int16_t *)ctx.slavelist[1].outputs;
   memset(out, 0, ctx.slavelist[1].Obytes);
   cycle_ms(300);
   show_in("idle       ");

   /* layout A: [ENABLE, ANGLESET x6, FORCESET x6, SPEEDSET x5] */
   out[0] = 1;
   for (i = 1; i <= 6; i++)  out[i] = 1000;
   for (i = 7; i <= 12; i++) out[i] = 300;
   for (i = 13; i <= 17; i++) out[i] = 400;
   cycle_ms(3000);
   show_in("A open     ");

   out[1] = 0; out[2] = 0; out[3] = 1000; out[4] = 0; out[5] = 0; out[6] = -1;
   cycle_ms(3000);
   show_in("A middle   ");

   /* layout B: [ANGLESET x6, FORCESET x6, SPEEDSET x6] */
   for (i = 0; i < 6; i++)  out[i] = 1000;
   for (i = 6; i < 12; i++) out[i] = 300;
   for (i = 12; i < 18; i++) out[i] = 400;
   cycle_ms(3000);
   show_in("B open     ");

   out[0] = 0; out[1] = 0; out[2] = 1000; out[3] = 0; out[4] = 0; out[5] = -1;
   cycle_ms(3000);
   show_in("B middle   ");

   ecx_close(&ctx);
   return 0;
}
