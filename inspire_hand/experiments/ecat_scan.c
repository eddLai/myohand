/* ecat_scan - read-only "is the hand on this NIC?" enumerator.
 *
 * Every rerun of the persistent-OP work starts by answering one question:
 * which interface does the slave actually answer on. Neither the source
 * nor the link state can be trusted for that - .112 had hand_ctl.c edited
 * to enp17s0 while the hand was answering on eno1 through the lab switch,
 * and a PL-backed port can show carrier with nothing behind it. So ask
 * the bus instead of inferring.
 *
 * This does config_init and nothing else: no PDO map, no state request
 * past what enumeration needs, no process data. It cannot move the hand.
 *
 * Usage: ecat_scan [iface]        (default: $ECAT_IFACE, else eth0)
 * Exit:  0 = at least one slave found, 1 = none, 2 = could not open iface
 *
 * hasdc is printed because it decides the Sync0 question - see
 * ExoPulse_docs Project_Management/Inspire_RH56F1/01_Hand_Control/
 * EtherCAT/Persistent_OP_Probe.md.
 */
#include "soem/soem.h"
#include "hand_safety.h"
#include <stdio.h>
#include <string.h>

static ecx_contextt ctx;

int main(int argc, char **argv)
{
   const char *iface = (argc > 1) ? argv[1] : hs_iface();
   int n, i;

   if (!ecx_init(&ctx, iface))
   {
      printf("CANNOT OPEN %s (need CAP_NET_RAW: setcap cap_net_raw,"
             "cap_net_admin+eip ecat_scan)\n", iface);
      return 2;
   }

   n = ecx_config_init(&ctx);
   if (n <= 0)
   {
      printf("NO EtherCAT slave on %s (config_init=%d)\n", iface, n);
      ecx_close(&ctx);
      return 1;
   }

   printf("FOUND %d EtherCAT slave(s) on %s:\n", n, iface);
   for (i = 1; i <= n; i++)
   {
      printf("  slave %d  name=\"%s\"  vendor=0x%08x product=0x%08x "
             "rev=0x%08x  state=0x%02x  hasdc=%d\n",
             i, ctx.slavelist[i].name,
             (unsigned)ctx.slavelist[i].eep_man,
             (unsigned)ctx.slavelist[i].eep_id,
             (unsigned)ctx.slavelist[i].eep_rev,
             (unsigned)ctx.slavelist[i].state,
             ctx.slavelist[i].hasdc);
   }

   ecx_close(&ctx);
   return 0;
}
