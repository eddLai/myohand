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
      int16_t t[6] = {890, 890, 890, 890, 890, 1610};
      why[0] = 0;
      n = hs_interlock(t, ang_open, why, sizeof why);
      check("fist: thumb_bend lifted clear of index", n >= 1 && t[AX_THUMB_BEND] >= 1178);
      check("fist: fingers keep their commanded close", t[AX_PINKY] == 890 && t[AX_INDEX] == 890);
   }
   {  /* an open hand passes through untouched */
      int16_t t[6] = {1850, 1850, 1850, 1850, 1850, 1850};
      why[0] = 0;
      check("open hand: no adjustment", hs_interlock(t, ang_open, why, sizeof why) == 0);
   }
   {  /* a held (-1) thumb is judged by where it actually sits */
      int16_t t[6] = {890, 890, 890, 890, -1, -1};
      why[0] = 0;
      n = hs_interlock(t, ang_closed, why, sizeof why);
      check("held thumb resting closed is still clamped", n >= 1 && t[AX_THUMB_BEND] >= 1178);
   }
   {  /* palm-ward rotation is refused while the index is curled */
      int16_t t[6] = {-1, -1, -1, 986, 1610, 1034};
      why[0] = 0;
      hs_interlock(t, ang_open, why, sizeof why);
      check("curled index blocks palm-ward thumb rotation", t[AX_THUMB_ROT] >= 1466);
   }
   {  /* out-of-range targets are clamped, holds stay holds */
      int16_t t[6] = {5000, -80, -1, 1850, 1610, 1610};
      why[0] = 0;
      hs_interlock(t, ang_open, why, sizeof why);
      check("range clamp keeps targets within 890..1850",
            t[AX_PINKY] == 1850 && t[AX_RING] == 890 && t[AX_MIDDLE] == -1);
   }
   {  /* an axis held while stalling gets backed off toward open */
      int16_t t[6] = {-1, -1, -1, -1, -1, -1};
      int16_t cur[6] = {0, 0, 0, 0, 0, 1100};
      int16_t sta[6] = {2, 2, 2, 2, 2, 5};
      int16_t ang[6] = {1850, 1850, 1850, 1850, 1850, 1200};
      why[0] = 0;
      n = hs_stall_relief(t, cur, sta, ang, why, sizeof why);
      check("stalled axis relieved toward open",
            n == 1 && t[AX_THUMB_ROT] >= hs_ang_to_target(1200) + 120);
   }
   {  /* an axis already commanded clear is left to finish the move */
      int16_t t[6] = {-1, -1, -1, -1, -1, 1850};
      int16_t cur[6] = {0, 0, 0, 0, 0, 1100};
      int16_t sta[6] = {2, 2, 2, 2, 2, 5};
      int16_t ang[6] = {1850, 1850, 1850, 1850, 1850, 1200};
      why[0] = 0;
      check("stalled axis already escaping is not overridden",
            hs_stall_relief(t, cur, sta, ang, why, sizeof why) == 0 && t[AX_THUMB_ROT] == 1850);
   }
   {  /* a healthy hand is left alone */
      int16_t t[6] = {1370, 1370, 1370, 1370, 1370, 1610};
      why[0] = 0;
      check("healthy hand: no relief",
            hs_stall_relief(t, no_cur, ok_sta, ang_open, why, sizeof why) == 0);
   }
   {  /* the scale conversions all callers share (see hand_safety.h) */
      char js[160];
      check("closed and open ANGLEACT hit the ends of the scale",
            hs_ang_to_target(890) == HS_TGT_MIN &&
            hs_ang_to_target(1850) == HS_TGT_MAX);
      /* the measured fact, asserted rather than described: a command and
         an ANGLEACT are the same number (1100->1101, 1272->1274,
         1509->1508 on the hand, 2026-08-06) */
      check("a target and an ANGLEACT are the same number",
            hs_ang_to_target(1274) == 1274 && hs_target_to_ang(1509) == 1509);
      check("target round-trips through ANGLEACT within a step",
            hs_ang_to_target(hs_target_to_ang(1000)) >= 997 &&
            hs_ang_to_target(hs_target_to_ang(1000)) <= 1003);
      /* below the closed end the mechanism gives you its stop, not the
         number you asked for - so the clamp says so before the wire does */
      check("a target under the closed end clamps to the stop",
            hs_clamp_target(612) == HS_TGT_MIN && !hs_target_valid(612));
      check("a hold survives clamping and conversion",
            hs_clamp_target(HS_TGT_HOLD) == HS_TGT_HOLD &&
            hs_target_to_ang(HS_TGT_HOLD) == HS_TGT_HOLD &&
            hs_target_valid(HS_TGT_HOLD));
      check("overshoot clamps, out-of-range values are refused",
            hs_clamp_target(30000) == HS_TGT_MAX &&
            hs_clamp_target(-500) == HS_TGT_MIN &&
            !hs_target_valid((int16_t)(HS_TGT_MAX + 1)));
      hs_scale_json(js, sizeof js);
      check("the scale reports itself for clients to check against",
            strstr(js, "\"target_max\":1850") != NULL &&
            strstr(js, "\"target_min\":890") != NULL &&
            strstr(js, "\"ang_open\":1850") != NULL);
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
