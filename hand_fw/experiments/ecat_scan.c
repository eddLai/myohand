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
 * It cannot move the hand, but it is not passive either, and that
 * distinction cost an afternoon on 2026-08-06. config_init drives the
 * slave's state machine; running it while handd held the bus left the
 * daemon holding OPERATIONAL and accepting targets - seq climbed past
 * 1300 - while the slave's application applied none of them. Nothing
 * reported it: bus up, AL 0, every reply ok and unguarded, no motion and
 * no current. So this now takes the same bus lock every other tool here
 * takes, and says who has it rather than quietly becoming a second
 * master.
 *
 * Usage: ecat_scan [iface]        (default: $ECAT_IFACE, else eth0)
 * Exit:  0 = at least one slave found, 1 = none, 2 = could not open
 *        iface, 3 = another master holds the bus
 *
 * hasdc is printed because it decides the Sync0 question - see
 * ExoPulse_docs L5_HMInteraction/Inspire_RH56F1/01_Hand_Control/
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
   int n, i, lock_fd;

   /* Short wait on purpose: the usual reason this is busy is a resident
      handd, and the answer then is to ask the daemon rather than to queue
      behind it. Twenty seconds of silence would just look like a hang. */
   lock_fd = hs_lock(2);
   if (lock_fd < 0)
   {
      printf("BUS BUSY - another master holds the hand (handd?). Scanning "
             "now would reset the slave's state machine underneath it and "
             "silently stop it applying targets.\n"
             "  running masters: try `pgrep -a handd`\n"
             "  to read state without a second master: use the daemon's "
             "socket (hand_client.HandClient().state())\n");
      return 3;
   }

   if (!ecx_init(&ctx, iface))
   {
      printf("CANNOT OPEN %s (need CAP_NET_RAW: setcap cap_net_raw,"
             "cap_net_admin+eip ecat_scan)\n", iface);
      hs_unlock(lock_fd);
      return 2;
   }

   n = ecx_config_init(&ctx);
   if (n <= 0)
   {
      printf("NO EtherCAT slave on %s (config_init=%d)\n", iface, n);
      ecx_close(&ctx);
      hs_unlock(lock_fd);
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
   hs_unlock(lock_fd);
   return 0;
}
