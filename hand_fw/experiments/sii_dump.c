/* sii_dump - what does the slave actually say its PDOs and mailbox are?
 *
 * Written to test two assumptions that every binary in this tree had
 * carried without ever re-checking them. Both are now answered, and the
 * answers are in results_2026-08-06/sii_dump.txt next door:
 *
 *   1. The RxPDO was assumed to be [ENABLE_SET][ANGLE_SET x6][FORCE_SET x6]
 *      [SPEED_SET x6]. The EEPROM names only SIX of the nineteen words it
 *      reserves - ENABLE_SET at 0x7000:01 and ANGLESET1..5 at :02..:06 -
 *      and leaves the other thirteen as zeros. So ENABLE_SET really is
 *      word 0 and 16 bits wide (which is what op_execute_hunt needed to
 *      know), the sixth angle is missing from the description of a
 *      six-axis hand, and the force/speed half of our layout has no
 *      backing in the device's own description at all. It is inferred,
 *      and this file is where that becomes visible.
 *
 *   2. "dead CoE mailbox on this SSC build" - a line sixteen files
 *      repeated as `mbx_proto = 0`. It is wrong: CoE answers every SDO,
 *      and the device calls itself LAN9252_16HBI. The belief came from
 *      asking in INIT, where mailboxes are not serviced yet; this tool
 *      made the same mistake on its first run and reported state=0x01
 *      while doing so. The zeroing has to stay regardless, because the
 *      compliant 18-byte CoE map is refused with AL=0x001e - see
 *      compliant_op.
 *
 * Read-only: SII reads and, if the mailbox answers, SDO reads. No PDO
 * map, no OPERATIONAL, no output data. It cannot move the hand.
 *
 * Usage: sii_dump <iface>
 */
#include "soem/soem.h"
#include "hand_safety.h"
#include <stdio.h>
#include <stdint.h>
#include <string.h>

static ecx_contextt ctx;
static uint16 sii[2048];       /* SII words, 0..0x7ff */
static int sii_words;

static uint16 w(int i) { return (i < sii_words) ? sii[i] : 0xffff; }

/* SII strings are a length-prefixed list in category 10; index 1 is the
   first string. Returns "" for a missing index rather than failing, so a
   PDO entry with no name still prints its numbers. */
static const char *sii_string(int cat_off, int cat_len, int idx)
{
   static char buf[64];
   const uint8 *p = (const uint8 *)&sii[cat_off];
   int n, i, off = 1;   /* byte 0 is the count of strings */
   int count = p[0];
   buf[0] = 0;
   if (idx < 1 || idx > count) return buf;
   for (i = 1; i <= count; i++)
   {
      n = p[off++];
      if (off + n > cat_len * 2) break;
      if (i == idx)
      {
         int k = n < (int)sizeof buf - 1 ? n : (int)sizeof buf - 1;
         memcpy(buf, p + off, k);
         buf[k] = 0;
         return buf;
      }
      off += n;
   }
   return buf;
}

int main(int argc, char **argv)
{
   int i, off, str_off = 0, str_len = 0, lock_fd;
   const char *iface;

   if (argc < 2) { printf("usage: sii_dump <iface>\n"); return 1; }
   iface = argv[1];

   lock_fd = hs_lock(20);
   if (lock_fd < 0) { printf("bus busy: another master holds the hand\n"); return 3; }

   if (!ecx_init(&ctx, iface)) { printf("ecx_init failed on %s\n", iface); return 2; }
   if (ecx_config_init(&ctx) <= 0)
   { printf("no slave on %s\n", iface); ecx_close(&ctx); return 2; }

   printf("slave: name=\"%s\" vendor=0x%08x product=0x%08x rev=0x%08x\n",
          ctx.slavelist[1].name, ctx.slavelist[1].eep_man,
          ctx.slavelist[1].eep_id, ctx.slavelist[1].eep_rev);
   printf("mailbox: mbx_proto=0x%04x mbx_l=%d mbx_wo=0x%04x "
          "mbx_rl=%d mbx_ro=0x%04x   CoE=%s\n",
          ctx.slavelist[1].mbx_proto, ctx.slavelist[1].mbx_l,
          ctx.slavelist[1].mbx_wo, ctx.slavelist[1].mbx_rl,
          ctx.slavelist[1].mbx_ro,
          (ctx.slavelist[1].mbx_proto & ECT_MBXPROT_COE) ? "ADVERTISED"
                                                         : "not advertised");

   /* What SOEM ends up sizing the process image as. Every driver in this
      tree zeroes mbx_proto first, which makes SOEM map from the SII
      instead of asking the (dead) CoE mailbox for a PDO list - and the
      two answers are not the same size, so the line that looks like a
      workaround is actually load-bearing. Measure both. */
   {
      static uint8 map[4096];
      uint16 saved = ctx.slavelist[1].mbx_proto;
      ecx_config_map_group(&ctx, map, 0);
      printf("mapped with CoE advertised: Obytes=%d Obits=%d  "
             "Ibytes=%d Ibits=%d\n",
             ctx.slavelist[1].Obytes, ctx.slavelist[1].Obits,
             ctx.slavelist[1].Ibytes, ctx.slavelist[1].Ibits);
      ecx_close(&ctx);
      if (!ecx_init(&ctx, iface) || ecx_config_init(&ctx) <= 0)
      { printf("re-init failed\n"); return 2; }
      ctx.slavelist[1].mbx_proto = 0;
      ecx_config_map_group(&ctx, map, 0);
      printf("mapped with mbx_proto=0:     Obytes=%d Obits=%d  "
             "Ibytes=%d Ibits=%d   <- what the drivers use\n",
             ctx.slavelist[1].Obytes, ctx.slavelist[1].Obits,
             ctx.slavelist[1].Ibytes, ctx.slavelist[1].Ibits);
      ctx.slavelist[1].mbx_proto = saved;
   }

   /* SII, word by word. ecx_readeeprom returns 32 bits per call. */
   for (i = 0; i < 1024; i += 2)
   {
      uint32 v = ecx_readeeprom(&ctx, 1, (uint16)i, EC_TIMEOUTEEP);
      sii[i] = (uint16)(v & 0xffff);
      sii[i + 1] = (uint16)(v >> 16);
      sii_words = i + 2;
      if (sii[i] == 0xffff && i >= 0x40) break;
   }
   printf("SII: read %d words\n\n", sii_words);

   /* categories start at word 0x40 */
   off = 0x40;
   while (off + 2 < sii_words)
   {
      uint16 cat = w(off), len = w(off + 1);
      const char *name;
      if (cat == 0xffff || cat == 0) break;
      switch (cat)
      {
         case 10: name = "STRINGS"; str_off = off + 2; str_len = len; break;
         case 20: name = "DATATYPES"; break;
         case 30: name = "GENERAL"; break;
         case 40: name = "FMMU"; break;
         case 41: name = "SYNCMANAGER"; break;
         case 50: name = "TXPDO"; break;
         case 51: name = "RXPDO"; break;
         default: name = "?";
      }
      printf("== category %d (%s), %d words at 0x%03x ==\n", cat, name, len, off + 2);

      if (cat == 41)
      {
         /* 8 bytes per SM: start, length, control, status, enable, type */
         const uint8 *p = (const uint8 *)&sii[off + 2];
         for (i = 0; i + 8 <= len * 2; i += 8)
         {
            static const char *smtype[] = {"unused", "mbx out", "mbx in",
                                           "outputs (RxPDO)", "inputs (TxPDO)"};
            int t = p[i + 7];
            printf("  SM%d start=0x%04x len=%-4d ctrl=0x%02x enable=0x%02x "
                   "type=%d %s%s\n", i / 8,
                   (uint16)(p[i] | (p[i + 1] << 8)),
                   (uint16)(p[i + 2] | (p[i + 3] << 8)),
                   p[i + 4], p[i + 6], t, t < 5 ? smtype[t] : "?",
                   (p[i + 4] & 0x40) ? "  [watchdog enabled]" : "");
         }
      }
      else if (cat == 51)
      {
         /* raw bytes too: the RxPDO entry table is where the layout this
            whole driver is built on comes from, and a parser can lie */
         const uint8 *p = (const uint8 *)&sii[off + 2];
         int b;
         printf("  raw:");
         for (b = 0; b < len * 2 && b < 176; b++)
            printf("%s%02x", (b % 16) == 0 ? "\n    " : " ", p[b]);
         printf("\n");
      }
      if (cat == 50 || cat == 51)
      {
         /* PDO header: index, nEntries, syncM, dcsync, name idx, flags
            then nEntries x 8 bytes: index, subindex, name idx, datatype,
            bitlen, flags */
         const uint8 *p = (const uint8 *)&sii[off + 2];
         int n = p[2], e, bitpos = 0;
         printf("  PDO 0x%04x  entries=%d  assigned to SM%d  name=\"%s\"\n",
                (uint16)(p[0] | (p[1] << 8)), n, p[3],
                sii_string(str_off, str_len, p[5]));
         for (e = 0; e < n && 8 + e * 8 + 8 <= len * 2; e++)
         {
            const uint8 *q = p + 8 + e * 8;
            int bits = q[5];
            printf("    [%2d] word %-3d 0x%04x:%02x %-3d bit  \"%s\"\n",
                   e, bitpos / 16, (uint16)(q[0] | (q[1] << 8)), q[2], bits,
                   sii_string(str_off, str_len, q[3]));
            bitpos += bits;
         }
      }
      off += 2 + len;
   }

   /* CoE liveness. mbx_proto is left exactly as the slave advertised it -
      the point of this tool is to test the claim that it is dead. */
   printf("\n== CoE mailbox test ==\n");
   if (!(ctx.slavelist[1].mbx_proto & ECT_MBXPROT_COE))
      printf("  slave does not advertise CoE in its SII - nothing to try\n");
   else
   {
      struct { uint16 idx; uint8 sub; const char *what; } probe[] = {
         {0x1000, 0, "DeviceType"},
         {0x1008, 0, "DeviceName"},
         {0x1018, 1, "Identity.VendorID"},
         {0x1c32, 1, "SM2 sync type (0=FreeRun 1=SM-Synchron 2=DC-Sync0)"},
         {0x1c32, 2, "SM2 cycle time ns"},
         {0x1c33, 1, "SM3 sync type"},
         {0x1c12, 0, "RxPDO assign count"},
         {0x1c13, 0, "TxPDO assign count"},
      };
      ctx.slavelist[0].state = EC_STATE_PRE_OP;
      ecx_writestate(&ctx, 0);
      ecx_statecheck(&ctx, 0, EC_STATE_PRE_OP, EC_TIMEOUTSTATE * 2);
      printf("  (moved to PRE_OP so the mailbox is serviceable; state=0x%02x)\n",
             ctx.slavelist[1].state);
      for (i = 0; i < (int)(sizeof probe / sizeof probe[0]); i++)
      {
         uint8 buf[64];
         int sz = sizeof buf, wkc;
         memset(buf, 0, sizeof buf);
         ctx.ecaterror = FALSE;
         wkc = ecx_SDOread(&ctx, 1, probe[i].idx, probe[i].sub, FALSE, &sz,
                           buf, EC_TIMEOUTRXM);
         if (wkc > 0)
         {
            int b;
            printf("  0x%04x:%02x %-45s = ", probe[i].idx, probe[i].sub,
                   probe[i].what);
            for (b = 0; b < sz && b < 16; b++) printf("%02x ", buf[b]);
            if (sz <= 4)
            {
               uint32 v = 0;
               memcpy(&v, buf, sz < 4 ? sz : 4);
               printf(" (%u)", v);
            }
            printf("\n");
         }
         else
            printf("  0x%04x:%02x %-45s : no answer (wkc=%d)\n",
                   probe[i].idx, probe[i].sub, probe[i].what, wkc);
      }
   }

   ctx.slavelist[0].state = EC_STATE_INIT;
   ecx_writestate(&ctx, 0);
   ecx_close(&ctx);
   hs_unlock(lock_fd);
   return 0;
}
