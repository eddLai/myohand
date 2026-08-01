/* Offline checks for the driver-level interlock: no hardware needed.
   Build: gcc -I . test_safety.c hand_safety.c -o test_safety */
#include "hand_safety.h"
#include <stdio.h>
#include <string.h>

static int fails;

static void check(const char *name, int cond)
{
   printf("%-46s %s\n", name, cond ? "ok" : "FAIL");
   if (!cond) fails++;
}

int main(void)
{
   /* ANGLEACT roughly: 890 closed, 1850 open */
   int16_t ang_open[6]   = {1850, 1850, 1850, 1850, 1850, 1850};
   int16_t ang_closed[6] = {890, 890, 890, 890, 890, 890};
   int16_t no_cur[6] = {0, 0, 0, 0, 0, 0};
   int16_t ok_sta[6] = {2, 2, 2, 2, 2, 2};
   char why[256];
   int n;

   {  /* a fist commanding index and thumb into each other gets clamped */
      int16_t t[6] = {0, 0, 0, 0, 0, 1500};
      why[0] = 0;
      n = hs_interlock(t, ang_open, why, sizeof why);
      check("fist: thumb_bend lifted clear of index", n >= 1 && t[AX_THUMB_BEND] >= 600);
      check("fist: fingers keep their commanded close", t[AX_PINKY] == 0 && t[AX_INDEX] == 0);
   }
   {  /* an open hand passes through untouched */
      int16_t t[6] = {2000, 2000, 2000, 2000, 2000, 2000};
      why[0] = 0;
      check("open hand: no adjustment", hs_interlock(t, ang_open, why, sizeof why) == 0);
   }
   {  /* a held (-1) thumb is judged by where it actually sits */
      int16_t t[6] = {0, 0, 0, 0, -1, -1};
      why[0] = 0;
      n = hs_interlock(t, ang_closed, why, sizeof why);
      check("held thumb resting closed is still clamped", n >= 1 && t[AX_THUMB_BEND] >= 600);
   }
   {  /* palm-ward rotation is refused while the index is curled */
      int16_t t[6] = {-1, -1, -1, 200, 1500, 300};
      why[0] = 0;
      hs_interlock(t, ang_open, why, sizeof why);
      check("curled index blocks palm-ward thumb rotation", t[AX_THUMB_ROT] >= 1200);
   }
   {  /* out-of-range targets are clamped, holds stay holds */
      int16_t t[6] = {5000, -80, -1, 2000, 1500, 1500};
      why[0] = 0;
      hs_interlock(t, ang_open, why, sizeof why);
      check("range clamp keeps targets within 0..2000",
            t[AX_PINKY] == 2000 && t[AX_RING] == 0 && t[AX_MIDDLE] == -1);
   }
   {  /* an axis held while stalling gets backed off toward open */
      int16_t t[6] = {-1, -1, -1, -1, -1, -1};
      int16_t cur[6] = {0, 0, 0, 0, 0, 1100};
      int16_t sta[6] = {2, 2, 2, 2, 2, 5};
      int16_t ang[6] = {1850, 1850, 1850, 1850, 1850, 1200};
      why[0] = 0;
      n = hs_stall_relief(t, cur, sta, ang, why, sizeof why);
      check("stalled axis relieved toward open",
            n == 1 && t[AX_THUMB_ROT] >= hs_ang_to_target(1200) + 250);
   }
   {  /* an axis already commanded clear is left to finish the move */
      int16_t t[6] = {-1, -1, -1, -1, -1, 2000};
      int16_t cur[6] = {0, 0, 0, 0, 0, 1100};
      int16_t sta[6] = {2, 2, 2, 2, 2, 5};
      int16_t ang[6] = {1850, 1850, 1850, 1850, 1850, 1200};
      why[0] = 0;
      check("stalled axis already escaping is not overridden",
            hs_stall_relief(t, cur, sta, ang, why, sizeof why) == 0 && t[AX_THUMB_ROT] == 2000);
   }
   {  /* a healthy hand is left alone */
      int16_t t[6] = {1000, 1000, 1000, 1000, 1000, 1500};
      why[0] = 0;
      check("healthy hand: no relief",
            hs_stall_relief(t, no_cur, ok_sta, ang_open, why, sizeof why) == 0);
   }
   {  /* thumb-bend gets the force headroom its phantom reading demands */
      int16_t out[19];
      memset(out, 0, sizeof out);
      hs_profile(out, 500, 1000);
      check("thumb_bend force clears its phantom reading",
            out[HS_OUT_FORCE + AX_THUMB_BEND] > 1857 &&
            out[HS_OUT_FORCE + AX_PINKY] == 500);
      check("thumb_bend speed reduced for that headroom",
            out[HS_OUT_SPEED + AX_THUMB_BEND] < out[HS_OUT_SPEED + AX_PINKY]);
   }

   printf("\n%s\n", fails ? "FAILURES PRESENT" : "all checks passed");
   return fails ? 1 : 0;
}
