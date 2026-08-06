/* ecat_interrogate - ask the hand what it wants instead of guessing.
 *
 * Three rounds of guessing have now failed. Round one blamed the lab
 * switch; the 2026-08-06 direct-link run reproduced AL=0x002d on a bare
 * cable. Round two blamed distributed clocks; probe_dc2/probe_dc3 held
 * OPERATIONAL for ten seconds with DCactive=1, Sync0 at 1 ms and
 * AL=0x0000, and the axis still never moved and never drew a milliamp.
 * Round three blames the master's process-data cadence during the state
 * transition. Every round has been an inference drawn from whether an
 * actuator twitched.
 *
 * The device does not have to be guessed at. Which synchronisation mode
 * an SSC application runs in is not a mystery to be deduced from motion,
 * it is a number the slave publishes: 0x1C32:1 for the output sync
 * manager, 0x1C33:1 for the input one, with 0x1C32:4 listing the modes
 * it will accept at all. Nobody in this repo has ever read them. The
 * reason nobody has is a single line, `slavelist[1].mbx_proto = 0`,
 * copied into sixteen files including tools written this week - a belief
 * that CoE is dead which was formed once, early, and never retested.
 *
 * So this tool reads. It reads what SOEM discovered, what CoE answers
 * (both as discovered and with the mailbox forced on, because the belief
 * under test is precisely that the discovery is wrong), what the ESC
 * registers say about sync managers, watchdogs and distributed clocks,
 * and what the EEPROM declares. Where the EEPROM and the live registers
 * disagree - the bring-up log claims they do for SYNCM - both are printed
 * side by side rather than one being trusted.
 *
 * It cannot move the hand, and not as a matter of care: it never calls
 * ecx_config_map_group(), so no IOmap is ever built, slavelist[1].outputs
 * stays NULL, and there is no output buffer for a target to be written
 * into. It issues no SDO write, no register write, and no state request
 * beyond the PRE_OP that ecx_config_init() performs to bring the mailbox
 * up. Mailboxes live in PRE_OP; process data does not exist there.
 *
 * Usage: ecat_interrogate <iface>
 */
#include "soem/soem.h"
#include "hand_safety.h"
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>

static ecx_contextt ctx;
static uint16 adr;                /* configured address of slave 1 */

/* ---------- ESC register reads (independent of the mailbox) ---------- */

static uint8 rd8(uint16 reg)
{
   uint8 v = 0;
   ecx_FPRD(&ctx.port, adr, reg, sizeof v, &v, EC_TIMEOUTRET);
   return v;
}

static uint16 rd16(uint16 reg)
{
   uint16 v = 0;
   ecx_FPRD(&ctx.port, adr, reg, sizeof v, &v, EC_TIMEOUTRET);
   return v;
}

static uint32 rd32(uint16 reg)
{
   uint32 v = 0;
   ecx_FPRD(&ctx.port, adr, reg, sizeof v, &v, EC_TIMEOUTRET);
   return v;
}

static uint64 rd64(uint16 reg)
{
   uint64 v = 0;
   ecx_FPRD(&ctx.port, adr, reg, sizeof v, &v, EC_TIMEOUTRET);
   return v;
}

/* ---------- SII / EEPROM ---------- */

/* one readeeprom returns the two words at [a] and [a+1] */
static uint32 sii32(uint16 a)
{
   return ecx_readeeprom(&ctx, 1, a, EC_TIMEOUTEEP);
}

static uint16 sii16(uint16 a)
{
   return (uint16)(sii32(a) & 0xFFFF);
}

/* ---------- decoding ---------- */

static const char *synctype(uint16 v)
{
   switch (v)
   {
      case 0x0000: return "Free Run - not synchronised, app runs on its own clock";
      case 0x0001: return "SM-Synchron - app runs on the SM2 event (data arrival)";
      case 0x0002: return "DC Sync0 - app runs on the Sync0 interrupt";
      case 0x0003: return "DC Sync1 - app runs on the Sync1 interrupt";
      case 0x0022: return "Synchron with SM2 event";
      case 0x0023: return "Synchron with SM3 event";
   }
   return "unlisted";
}

/* 0x1C32:4 / 0x1C33:4 - which modes the slave will accept at all */
static void print_supported(uint16 v)
{
   printf("      supported: FreeRun=%d SM-Synchron=%d",
          (v & 0x0001) ? 1 : 0, (v & 0x0002) ? 1 : 0);
   switch ((v >> 2) & 0x3)
   {
      case 0: printf(" DC=none"); break;
      case 1: printf(" DC=Sync0"); break;
      case 2: printf(" DC=Sync1"); break;
      case 3: printf(" DC=subordinated"); break;
   }
   printf("  (raw 0x%04x)\n", v);
}

/* SM control byte, ESC 0x0804 + 8n */
static void print_sm_control(uint8 c)
{
   static const char *opmode[] = {"buffered", "?", "mailbox", "?"};
   static const char *dir[] = {"ECAT read / PDI write", "ECAT write / PDI read",
                               "?", "?"};
   printf("mode=%s dir=%s ecatIRQ=%d pdiIRQ=%d watchdog=%s",
          opmode[c & 0x3], dir[(c >> 2) & 0x3],
          (c >> 4) & 1, (c >> 5) & 1,
          ((c >> 6) & 1) ? "ENABLED" : "off");
}

/* SM status byte, ESC 0x0805 + 8n. The buffer bits are the interesting
   ones: they say whether anything on the PDI side has been reading. */
static void print_sm_status(uint8 s)
{
   printf("intW=%d intR=%d mbxFull=%d bufState=%d readBufInUse=%d "
          "writeBufInUse=%d",
          s & 1, (s >> 1) & 1, (s >> 3) & 1, (s >> 4) & 3,
          (s >> 6) & 1, (s >> 7) & 1);
}

static void coe_details(uint8 d)
{
   printf("      CoE details 0x%02x: SDO=%d SDOinfo=%d PDOassign=%d "
          "PDOconfig=%d uploadAtStartup=%d completeAccess=%d\n",
          d, d & 1, (d >> 1) & 1, (d >> 2) & 1, (d >> 3) & 1,
          (d >> 4) & 1, (d >> 5) & 1);
}

static void print_mbxproto(uint16 p)
{
   printf("AoE=%d EoE=%d CoE=%d FoE=%d SoE=%d VoE=%d (raw 0x%04x)",
          (p & ECT_MBXPROT_AOE) ? 1 : 0, (p & ECT_MBXPROT_EOE) ? 1 : 0,
          (p & ECT_MBXPROT_COE) ? 1 : 0, (p & ECT_MBXPROT_FOE) ? 1 : 0,
          (p & ECT_MBXPROT_SOE) ? 1 : 0, (p & ECT_MBXPROT_VOE) ? 1 : 0, p);
}

/* ---------- CoE ---------- */

static void drain_errors(void)
{
   int i;
   for (i = 0; i < 8; i++)
   {
      char *e = ecx_elist2string(&ctx);
      if (!e || !*e) break;
      printf("        soem: %s", e);   /* elist2string already ends in \n */
   }
}

/* Reads one SDO and prints it. Returns 1 on success. Nothing is ever
   written, so a slave that dislikes the index simply aborts. */
static int sdo(uint16 index, uint8 sub, const char *label)
{
   uint8 buf[256];
   int size = sizeof buf, wkc, i;

   memset(buf, 0, sizeof buf);
   wkc = ecx_SDOread(&ctx, 1, index, sub, FALSE, &size, buf, EC_TIMEOUTRXM);
   if (wkc <= 0)
   {
      printf("  0x%04X:%02d %-22s -> FAILED (wkc=%d)\n", index, sub, label, wkc);
      drain_errors();
      return 0;
    }

   printf("  0x%04X:%02d %-22s -> ", index, sub, label);
   if (size == 1)      printf("%u (0x%02x)", buf[0], buf[0]);
   else if (size == 2) printf("%u (0x%04x)", *(uint16 *)buf, *(uint16 *)buf);
   else if (size == 4) printf("%u (0x%08x)", *(uint32 *)buf, *(uint32 *)buf);
   else
   {
      /* strings come back here; show both readings and let the eye pick */
      printf("[%d B] \"", size);
      for (i = 0; i < size && i < 64; i++)
         putchar((buf[i] >= 32 && buf[i] < 127) ? buf[i] : '.');
      printf("\" hex:");
      for (i = 0; i < size && i < 32; i++) printf(" %02x", buf[i]);
   }
   printf("\n");

   /* the two indices this whole exercise exists for */
   if ((index == 0x1C32 || index == 0x1C33) && size >= 2)
   {
      uint16 v = *(uint16 *)buf;
      if (sub == 1) printf("      => %s\n", synctype(v));
      if (sub == 4) print_supported(v);
   }
   return 1;
}

static void sdo_block(uint16 idx, const char *what)
{
   char lbl[64];
   printf("\n  -- 0x%04X (%s) --\n", idx, what);
   snprintf(lbl, sizeof lbl, "sync type");        sdo(idx, 1, lbl);
   snprintf(lbl, sizeof lbl, "cycle time ns");    sdo(idx, 2, lbl);
   snprintf(lbl, sizeof lbl, "supported modes");  sdo(idx, 4, lbl);
   snprintf(lbl, sizeof lbl, "min cycle time");   sdo(idx, 5, lbl);
   snprintf(lbl, sizeof lbl, "calc+copy time");   sdo(idx, 6, lbl);
   snprintf(lbl, sizeof lbl, "delay time");       sdo(idx, 9, lbl);
   snprintf(lbl, sizeof lbl, "sync0 cycle time"); sdo(idx, 10, lbl);
   snprintf(lbl, sizeof lbl, "cycle exceeded ct");sdo(idx, 11, lbl);
   snprintf(lbl, sizeof lbl, "shift too short");  sdo(idx, 12, lbl);
}

/* Everything CoE, run twice: once with what config_init discovered, once
   with the CoE bit forced on. The second pass is the point - the claim
   being tested is that the discovery itself is wrong. */
static void coe_pass(const char *tag)
{
   int i;

   printf("\n=== CoE (%s), mbx_proto=0x%04x ===\n", tag,
          ctx.slavelist[1].mbx_proto);

   /* Mailboxes are only serviced from PRE_OP upwards. config_init does not
      guarantee it - if the previous tool left the slave in INIT, every SDO
      here returns wkc=0 and the run looks like a dead mailbox, which is
      the exact false conclusion this tool exists to overturn. */
   ctx.slavelist[0].state = EC_STATE_PRE_OP;
   ecx_writestate(&ctx, 0);
   ecx_statecheck(&ctx, 0, EC_STATE_PRE_OP, EC_TIMEOUTSTATE * 2);
   ecx_readstate(&ctx);
   printf("  (requested PRE_OP for the mailbox; state=0x%02x AL=0x%04x)\n",
          ctx.slavelist[1].state, ctx.slavelist[1].ALstatuscode);
   if (ctx.slavelist[1].state < EC_STATE_PRE_OP)
      printf("  WARNING: not in PRE_OP - SDO failures below say nothing "
             "about the mailbox itself\n");

   if (!sdo(0x1000, 0, "DeviceType"))
   {
      printf("  0x1000 is the cheapest object a CoE device has. It did not\n"
             "  answer, so this pass found no working SDO server.\n");
      return;
   }
   sdo(0x1008, 0, "DeviceName");
   sdo(0x1009, 0, "HwVersion");
   sdo(0x100A, 0, "SwVersion");
   sdo(0x1018, 1, "Identity/VendorID");
   sdo(0x1018, 2, "Identity/ProductCode");
   sdo(0x1018, 3, "Identity/Revision");
   sdo(0x1018, 4, "Identity/Serial");

   printf("\n  -- sync manager communication types --\n");
   sdo(0x1C00, 0, "SM count");
   sdo(0x1C00, 1, "SM0 type");
   sdo(0x1C00, 2, "SM1 type");
   sdo(0x1C00, 3, "SM2 type");
   sdo(0x1C00, 4, "SM3 type");

   printf("\n  -- PDO assignment --\n");
   sdo(0x1C12, 0, "RxPDO assign count");
   sdo(0x1C12, 1, "RxPDO assign[1]");
   sdo(0x1C13, 0, "TxPDO assign count");
   sdo(0x1C13, 1, "TxPDO assign[1]");

   /* The SII declares RxPDO 0x1601 with 19 entries of which 13 are null,
      and sizes SM2 at 36 bytes, which those 19 entries overflow. What CoE
      reports here is what the firmware itself believes, so read the whole
      map entry by entry rather than trusting the count. */
   printf("\n  -- RxPDO 0x1601, entry by entry (the output image we write) --\n");
   sdo(0x1601, 0, "0x1601 entry count");
   for (i = 1; i <= 20; i++)
   {
      char lbl[32];
      snprintf(lbl, sizeof lbl, "0x1601 entry[%d]", i);
      if (!sdo(0x1601, (uint8)i, lbl)) break;
   }
   printf("\n  -- TxPDO 0x1A00 --\n");
   sdo(0x1A00, 0, "0x1A00 entry count");

   sdo_block(0x1C32, "SM2 = outputs, the one that matters");
   sdo_block(0x1C33, "SM3 = inputs");
}

int main(int argc, char **argv)
{
   int i, n, lock_fd;
   uint16 cat, len, a;

   if (argc < 2) { printf("usage: ecat_interrogate <iface>\n"); return 1; }

   lock_fd = hs_lock(20);
   if (lock_fd < 0)
   { printf("bus busy: another master holds the hand\n"); return 3; }

   if (!ecx_init(&ctx, argv[1]))
   {
      printf("ecx_init failed on %s (need CAP_NET_RAW, or wrong iface)\n",
             argv[1]);
      hs_unlock(lock_fd);
      return 2;
   }

   n = ecx_config_init(&ctx);
   if (n <= 0)
   {
      printf("no EtherCAT slave on %s (config_init=%d)\n", argv[1], n);
      ecx_close(&ctx);
      hs_unlock(lock_fd);
      return 2;
   }

   adr = ctx.slavelist[1].configadr;

   /* ---------------- what SOEM discovered ---------------- */
   printf("=== enumeration on %s ===\n", argv[1]);
   printf("slaves=%d\n", n);
   printf("  name        = \"%s\"\n", ctx.slavelist[1].name);
   printf("  vendor      = 0x%08x   product = 0x%08x\n",
          ctx.slavelist[1].eep_man, ctx.slavelist[1].eep_id);
   printf("  revision    = 0x%08x   serial  = 0x%08x\n",
          ctx.slavelist[1].eep_rev, ctx.slavelist[1].eep_ser);
   printf("  configadr   = 0x%04x   state = 0x%02x  AL = 0x%04x\n",
          adr, ctx.slavelist[1].state, ctx.slavelist[1].ALstatuscode);
   printf("  Obits/Ibits = %u / %u   (config_map deliberately NOT called,\n"
          "                            so outputs=%s - nothing can be driven)\n",
          ctx.slavelist[1].Obits, ctx.slavelist[1].Ibits,
          ctx.slavelist[1].outputs ? "non-NULL!" : "NULL");
   printf("  hasdc       = %d   ptype = 0x%02x  topology = %d  activeports = 0x%x\n",
          ctx.slavelist[1].hasdc, ctx.slavelist[1].ptype,
          ctx.slavelist[1].topology, ctx.slavelist[1].activeports);
   printf("  mailbox     = wr %u B @0x%04x, rd %u B @0x%04x\n",
          ctx.slavelist[1].mbx_l, ctx.slavelist[1].mbx_wo,
          ctx.slavelist[1].mbx_rl, ctx.slavelist[1].mbx_ro);
   printf("  mbx_proto   = "); print_mbxproto(ctx.slavelist[1].mbx_proto);
   printf("\n");

   printf("\n  SOEM's view of the sync managers:\n");
   for (i = 0; i < EC_MAXSM; i++)
   {
      if (!ctx.slavelist[1].SM[i].StartAddr) continue;
      printf("    SM%d start=0x%04x len=%u flags=0x%08x type=%u\n", i,
             ctx.slavelist[1].SM[i].StartAddr,
             ctx.slavelist[1].SM[i].SMlength,
             ctx.slavelist[1].SM[i].SMflags,
             ctx.slavelist[1].SMtype[i]);
   }

   /* ---------------- CoE, honestly ---------------- */
   coe_pass("as discovered");

   if (!(ctx.slavelist[1].mbx_proto & ECT_MBXPROT_COE))
   {
      printf("\n  config_init did not flag CoE. The belief this tool exists\n"
             "  to retest is exactly that flag, so force it and ask again.\n");
      ctx.slavelist[1].mbx_proto |= ECT_MBXPROT_COE;
      if (!ctx.slavelist[1].mbx_l)
         printf("  WARNING: mailbox length is 0 - there is no mailbox to\n"
                "  force CoE onto, and the next pass will fail for that\n"
                "  reason rather than for a firmware one.\n");
      coe_pass("CoE forced on");
   }

   /* ---------------- ESC registers ---------------- */
   printf("\n=== ESC registers (read directly, mailbox not involved) ===\n");
   printf("  0x0000 type=0x%02x  rev=0x%02x  build=0x%04x\n",
          rd8(0x0000), rd8(0x0001), rd16(0x0002));
   printf("  0x0004 FMMUs=%u  SMs=%u  RAM=%u KB  portdesc=0x%02x\n",
          rd8(0x0004), rd8(0x0005), rd8(0x0006), rd8(0x0007));
   {
      uint16 f = rd16(0x0008);
      printf("  0x0008 features=0x%04x  DC=%s  DCwidth=%s\n", f,
             (f & 0x0004) ? "yes" : "NO", (f & 0x0008) ? "64bit" : "32bit");
   }
   printf("  0x0100 DLctrl=0x%08x   0x0110 DLstatus=0x%04x\n",
          rd32(0x0100), rd16(0x0110));
   printf("  0x0120 ALctrl=0x%04x  0x0130 ALstatus=0x%04x  "
          "0x0134 ALstatuscode=0x%04x\n",
          rd16(0x0120), rd16(0x0130), rd16(0x0134));

   printf("\n  sync manager registers (0x0800 + 8n) - live, not from EEPROM:\n");
   for (i = 0; i < 4; i++)
   {
      uint16 base = (uint16)(0x0800 + 8 * i);
      uint8 ctl = rd8(base + 4), sta = rd8(base + 5), act = rd8(base + 6);
      printf("    SM%d start=0x%04x len=%-4u enable=%d\n", i,
             rd16(base), rd16(base + 2), act & 1);
      printf("        control 0x%02x: ", ctl); print_sm_control(ctl);
      printf("\n        status  0x%02x: ", sta); print_sm_status(sta);
      printf("\n");
   }

   printf("\n  watchdog:\n");
   {
      uint16 div = rd16(0x0400), wpdi = rd16(0x0410), wpd = rd16(0x0420);
      printf("    0x0400 divider          = %u  (x40ns = %.3f us)\n",
             div, div * 0.040);
      printf("    0x0410 WD time PDI      = %u  -> %.1f ms\n",
             wpdi, div * 0.000040 * wpdi);
      printf("    0x0420 WD time procdata = %u  -> %.1f ms  "
             "<= THE number the 2-3 s budget never measured\n",
             wpd, div * 0.000040 * wpd);
      printf("    0x0440 WD status procdata = 0x%04x (bit0=1 means not expired)\n",
             rd16(0x0440));
      printf("    0x0442 WD counter procdata = %u   0x0443 WD counter PDI = %u\n",
             rd8(0x0442), rd8(0x0443));
   }

   printf("\n  distributed clocks:\n");
   printf("    0x0910 system time      = %llu\n",
          (unsigned long long)rd64(0x0910));
   printf("    0x0918 sys time offset  = %llu\n",
          (unsigned long long)rd64(0x0918));
   printf("    0x0928 sys time delay   = %u\n", rd32(0x0928));
   printf("    0x092C sys time diff    = 0x%08x\n", rd32(0x092C));
   {
      uint8 cyc = rd8(0x0980), act = rd8(0x0981);
      printf("    0x0980 cyclic unit ctrl = 0x%02x\n", cyc);
      printf("    0x0981 activation       = 0x%02x  "
             "cyclicEnable=%d Sync0gen=%d Sync1gen=%d autoAct=%d\n",
             act, act & 1, (act >> 1) & 1, (act >> 2) & 1, (act >> 3) & 1);
   }
   printf("    0x0982 pulse length     = %u (x10ns)\n", rd16(0x0982));
   printf("    0x098E Sync0 status     = 0x%02x\n", rd8(0x098E));
   printf("    0x09A0 cyclic start     = %llu\n",
          (unsigned long long)rd64(0x09A0));
   printf("    0x09A0+ Sync0 cycle     = %u ns   Sync1 cycle = %u ns\n",
          rd32(0x09A0 + 8), rd32(0x09A0 + 12));

   /* ---------------- EEPROM ---------------- */
   printf("\n=== EEPROM / SII ===\n");
   printf("  fixed area: mbx proto word 0x1C = 0x%04x -> ",
          sii16(ECT_SII_MBXPROTO));
   print_mbxproto(sii16(ECT_SII_MBXPROTO));
   printf("\n  (this is the EEPROM's own claim about CoE, independent of\n"
          "   whatever config_init concluded above)\n");
   printf("  rx mbx @0x%04x size %u   tx mbx @0x%04x size %u\n",
          sii16(ECT_SII_RXMBXADR), sii16(ECT_SII_MBXSIZE),
          sii16(ECT_SII_TXMBXADR), sii16(ECT_SII_MBXSIZE + 1));

   printf("\n  category walk from word 0x%04x:\n", ECT_SII_START);
   a = ECT_SII_START;
   for (i = 0; i < 32; i++)
   {
      uint32 hdr = sii32(a);
      cat = (uint16)(hdr & 0x7FFF);
      len = (uint16)(hdr >> 16);
      if ((hdr & 0xFFFF) == 0xFFFF || cat == 0) break;

      printf("    cat %-3u len %-4u words  @0x%04x", cat, len, a);
      switch (cat)
      {
         case 10: printf("  (STRINGS)"); break;
         case 20: printf("  (DATATYPES)"); break;
         case 30: printf("  (GENERAL)"); break;
         case 40: printf("  (FMMU)"); break;
         case 41: printf("  (SM)"); break;
         case 50: printf("  (TxPDO)"); break;
         case 51: printf("  (RxPDO)"); break;
         case 60: printf("  (DC)"); break;
      }
      printf("\n");

      if (cat == 30 && len >= 6)
      {
         /* byte 5 is CoE details - whether the device claims SDO at all */
         uint16 w = sii16((uint16)(a + 2 + 2));
         coe_details((uint8)(w & 0xFF));
         printf("      FoE details 0x%02x  EoE details 0x%02x\n",
                (uint8)(w >> 8), (uint8)(sii16((uint16)(a + 2 + 3)) & 0xFF));
      }
      if (cat == 41)
      {
         /* 4 words per SM: start, length, ctrl|status, enable|type */
         uint16 k, entries = (uint16)(len / 4);
         for (k = 0; k < entries && k < 8; k++)
         {
            uint16 b = (uint16)(a + 2 + 4 * k);
            uint16 st = sii16(b), ln = sii16((uint16)(b + 1));
            uint16 cs = sii16((uint16)(b + 2)), et = sii16((uint16)(b + 3));
            printf("      SM%u EEPROM: start=0x%04x len=%-4u ctrl=0x%02x "
                   "enable=%u type=%u\n",
                   k, st, ln, (uint8)(cs & 0xFF), (uint8)(et & 0xFF),
                   (uint8)(et >> 8));
            printf("                  control decodes to: ");
            print_sm_control((uint8)(cs & 0xFF));
            printf("\n");
         }
         printf("      ^ compare against the live 0x0800 block above. The\n"
                "        bring-up log claims EEPROM SYNCM and firmware do\n"
                "        not agree; this is where that shows up.\n");
      }
      if (cat == 60)
      {
         uint16 k;
         printf("      raw words:");
         for (k = 0; k < len && k < 24; k++)
            printf(" %04x", sii16((uint16)(a + 2 + k)));
         printf("\n      ^ ETG.1000 category 60 lists the cyclic operation\n"
                "        modes the device declares it supports.\n");
      }

      a = (uint16)(a + 2 + len);
      if (a > 0x0800) break;
   }

   ecx_close(&ctx);
   hs_unlock(lock_fd);
   printf("\n(link closed; nothing was written, no IOmap was ever built)\n");
   return 0;
}
