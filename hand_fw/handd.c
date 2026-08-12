/* handd - the resident EtherCAT master for the Inspire RH56F1.
 *
 * Today every pose is a fresh `hand_set` process: enumerate, map PDOs,
 * climb to OPERATIONAL, wake, write, disconnect, exit. Two to three
 * seconds of that is process startup and conservative wake loops rather
 * than anything the protocol demands - the persistent-OP probe got back
 * into OPERATIONAL in under a second and spent no time at all waking.
 * This daemon pays that cost once and then holds OPERATIONAL, so a client
 * only has to push a target at it.
 *
 * The safety layer lives in here rather than in each caller, which is the
 * point of moving it: teleop, an EMG classifier, an HTTP handler and an
 * ad-hoc script all reach the hand through this socket, and none of them
 * can route around hs_stall_relief / hs_interlock to do it.
 *
 * WHAT MAKES THE HAND EXECUTE was settled on hardware on 2026-08-06, and
 * the answer is that nothing special does. This slave applies its outputs
 * in OPERATIONAL like any other, as long as the master leaves it time to
 * finish a cycle. Driving it at 1 kHz does not: 0x1C32:02 - read-only
 * here, because in SM-Synchron that object is the slave's own measurement
 * rather than a setting - reports an 18-27 ms application cycle, and
 * 0x1C32:12, the cycle-exceeded counter, climbed about 600 a second under
 * a 1 ms feed. Every frame was preempting the work the last one started.
 * At 2 ms and slower the axis moves with the link up, OPERATIONAL held,
 * and current flowing. See experiments/results_2026-08-06.
 *
 * That is also what the older "it only moves when you disconnect" reading
 * really was: a disconnect, or 100 ms of silence, is simply the first
 * time we stopped interrupting it. The trigger was never a timeout.
 *
 * So the default is to send poses and let the hand follow them. The rest
 * remain as strategies because they are how every result before that day
 * was measured, and a firmware update could bring them back:
 *
 *   continuous  (default)  write the target and keep cycling. Requires a
 *                          period the slave can absorb - see --rate-hz,
 *                          which defaults to 500 for that reason.
 *   watchdog               write, then send nothing for longer than the
 *                          sync-manager watchdog (99.9 ms, from ESC
 *                          registers 0x0400/0x0420). The pose is applied
 *                          and the slave drops to SAFE_OP+ERROR
 *                          (AL=0x001b), so the strategy acknowledges the
 *                          error and climbs back. A fallback for a unit
 *                          that will not follow continuously.
 *   disconnect             write, hold, drop the link, reconnect. The same
 *                          silence, bought at the price of a full
 *                          re-enumeration. The reference path: every
 *                          pre-2026-08-06 measurement went through it.
 *   sync0                  arm distributed clocks and let the slave copy
 *                          its own buffer on the Sync0 interrupt.
 *                          MEASURED NOT TO WORK, though for a reason that
 *                          now looks like the cycle time rather than DC:
 *                          Sync0 at 1 ms is a 1 ms feed. Kept so the
 *                          negative result can be reproduced.
 *
 * The strategies share everything else: the loop, the socket, the guard,
 * the telemetry, the timing. Swapping them is one function, by design.
 *
 * Usage:
 *   handd [--iface=NAME] [--trigger=watchdog|disconnect|sync0] [--socket=PATH]
 *         [--rate-hz=N] [--dc-cycle-us=N] [--dc-shift-us=N] [--force=N]
 *         [--speed=N] [--hold-ms=N] [--settle-ms=N] [--simulate]
 *
 * Protocol: one text command per line on the unix socket, one JSON line
 * back. `hello` describes the daemon, `scale` reports what the target
 * numbers mean, `target P R M I TB TR` commands a pose, `state` reads
 * telemetry, `dc` reports the distributed-clock health and reads the AL
 * status code aloud, `bye` disconnects. See hand_client.py for the client
 * side.
 *
 * Exits nonzero with a readable reason if the bus is not there. It never
 * moves the hand on shutdown: an unattended park would be a surprise
 * movement, and the caller that owns the pose should be the one to change
 * it.
 */
/* sched_setaffinity and cpu_set_t are GNU extensions */
#define _GNU_SOURCE

#include "soem/soem.h"
#include "hand_safety.h"

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <sched.h>
#include <signal.h>
#include <stdarg.h>
#include <stddef.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/mman.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

/* TxPDO layout, same offsets every binary here reads */
#define IN_POS 0
#define IN_ANG 6
#define IN_FRC 12
#define IN_CUR 18
#define IN_ERR 24
#define IN_STA 30
#define IN_TMP 36
/* The T1 build streams its touch sensing after the six-axis telemetry:
   34 shorts for 8 capacitive modules (4 fingertips + thumb tip + 3 palm
   zones) x 4 quantities (normal force, tangential force, tangential
   direction, proximity) + 2 fields the datasheet does not name. Which
   field is which module/quantity is still uncalibrated against the real
   hand - these offsets only say where the block lives, not what order
   it is in. A hand without the T1 option maps a shorter input image and
   the block simply is not there, so its presence is probed per-bringup
   (have_tac), never assumed. */
#define IN_TAC   42
#define IN_TAC_N 34

#define MAX_CLIENTS 8
#define CLIENT_BUF  1024
#define WAKE_MS_MAX 12000
#define SOCKET_DEFAULT "/tmp/inspire_hand.sock"

/* ---- configuration -------------------------------------------------- */

static struct {
   const char *iface;
   const char *sock_path;
   const char *trigger_name;
   int rate_hz;
   int dc_cycle_us;      /* 0 = follow the PDO period */
   int dc_shift_us;      /* how far ahead of the Sync0 edge to aim */
   int force, speed;
   int hold_ms;          /* how long a target rides before the trigger fires */
   int settle_ms;        /* disconnect: how long to stay down */
   int starve_ms;        /* watchdog: how long to send nothing. 0 = read the
                            slave's own watchdog time and add a margin */
   int move_step;        /* target change big enough to time a step response */
   int move_eps;         /* ANGLEACT change that counts as motion having begun */
   int stuck_strikes;    /* commanded moves that may produce nothing before
                            the slave is declared to have stopped applying */
   const char *on_stuck; /* "exit" or "report" */
   const char *lat_path; /* CSV of per-step latency breakdowns */
   int cpu;              /* pin the loop to this core, -1 = wherever */
   int rt_prio;          /* SCHED_FIFO priority, 0 = leave scheduling alone */
   int lock_memory;      /* mlockall, so a page fault cannot stall a cycle */
   int simulate;
} cfg = { NULL, SOCKET_DEFAULT, "continuous", 500, 0, 50, 500, 1000, 120, 300,
          0, 96, 10, 3, "exit", NULL, -1, 0, 0, 0 };
/* rate_hz is 500 rather than 1000 because 1000 is the one rate this hand
   cannot be driven at. rate_sweep on 2026-08-06 held OPERATIONAL and
   stepped the middle finger at 1, 2, 3, 4, 5, 6 and 8 ms: every period
   from 2 ms up moved the axis about 180 counts and drew 56-71 mA, and
   1 ms moved it not at all and drew nothing. The slave says why in
   0x1C32:12, its cycle-exceeded counter, which gained 2244 in four
   seconds at 1 ms and exactly zero at every slower rate. Its application
   cannot finish a cycle if a new SM2 event arrives every millisecond, so
   outputs are never applied - which is the whole "the hand only moves
   when you disconnect" story, and it was ours, not the firmware's.
   500 Hz leaves a factor of two of margin under the measured limit. */
/* move_step is 96 rather than the 200 it was before 2026-08-06: it is a
   distance on the target scale, and that scale turned out to be ANGLEACT
   counts, where 200 old units are 96. */

/* ---- bus ------------------------------------------------------------ */

static ecx_contextt ctx;
static uint8 IOmap[4096];
static int16_t *in, *out;      /* point at the PDO buffers, or at the sim */
static int bus_up;
static int have_tac;           /* the input image reaches past IN_TAC */

static int16_t sim_in[96], sim_out[64];
static int32_t sim_ang_milli[6];   /* sub-count position, so slow moves move */
static int sim_awake_frames;

static int dc_hasdc, dc_configured, dc_active;
static int32_t dc_pdelay;

static volatile sig_atomic_t running = 1;

/* Has the slave stopped applying what we send it?
 *
 * On 2026-08-06 it did, and nothing here noticed for the rest of the
 * session. Running ecat_scan against the same NIC while this daemon held
 * the bus reset the slave's state machine underneath it; afterwards every
 * check this program makes still passed. The working counter incremented,
 * because the ESC was still receiving and acknowledging - what had stopped
 * was the application above it. Telemetry kept updating, because inputs
 * are a different sync manager. ecx_readstate still said OPERATIONAL with
 * AL 0. Every target was answered ok and unguarded and seq climbed past
 * 1300. The hand did not move and drew no current for any of it.
 *
 * The one thing that was observably different lives outside this program:
 * motion was commanded and none happened. So compare the two - which the
 * latency tracker already does per step, in lat_flush's `moved`. A pose
 * that asks for real travel, produces none, and draws no current is one
 * strike; several in a row is not a coincidence.
 *
 * Current is part of the test because a stalled axis also fails to travel,
 * and that is a different fault with its own handling (hs_stall_relief).
 * An axis being driven into something draws current; a slave that has
 * stopped applying draws none. */
static int stuck;            /* declared: the slave is not applying */
static int dead_streak;      /* consecutive commanded moves that did nothing */
static uint32_t seq;              /* every accepted target gets one */

static void on_signal(int s) { (void)s; running = 0; }

static long now_ms(void)
{
   struct timespec ts;
   clock_gettime(CLOCK_MONOTONIC, &ts);
   return ts.tv_sec * 1000L + ts.tv_nsec / 1000000L;
}

static void logf_(const char *fmt, ...)
   __attribute__((format(printf, 1, 2)));
static void logf_(const char *fmt, ...)
{
   va_list ap;
   printf("handd: ");
   va_start(ap, fmt);
   vprintf(fmt, ap);
   va_end(ap);
   printf("\n");
   fflush(stdout);
}

/* ---- simulated slave ------------------------------------------------
 * Not a pretend success: --simulate exists so the socket protocol, the
 * guard and the client path can be exercised where there is no hand, and
 * every reply it sends carries "simulate":true so no measurement taken
 * against it can be mistaken for one taken against the hardware.
 */
static void sim_reset(void)
{
   int i;
   memset(sim_in, 0, sizeof sim_in);
   memset(sim_out, 0, sizeof sim_out);
   for (i = 0; i < 6; i++)
   {
      sim_in[IN_ANG + i] = 896;        /* where the fingers actually rest */
      sim_ang_milli[i] = 896 * 1000;
      sim_in[IN_STA + i] = 7;          /* boot lands every axis in standby */
      sim_in[IN_TMP + i] = 40;
      sim_out[HS_OUT_TARGET + i] = HS_TGT_HOLD;
   }
   /* an arbitrary but non-uniform pattern, so a client that unpacks the
      block in the wrong order cannot get lucky and still pass */
   for (i = 0; i < IN_TAC_N; i++)
      sim_in[IN_TAC + i] = (int16_t)(100 * (i / 4 + 1) + i % 4);
   sim_awake_frames = 0;
}

static void sim_step(void)
{
   int i;
   int commanded = 0;

   for (i = 0; i < 6; i++)
      if (sim_out[HS_OUT_TARGET + i] != HS_TGT_HOLD) commanded = 1;
   /* standby clears once something has actually been commanded for a
      while, so the wake wiggle is exercised rather than skipped */
   if (commanded && ++sim_awake_frames > 300)
      for (i = 0; i < 6; i++) sim_in[IN_STA + i] = 2;

   for (i = 0; i < 6; i++)
   {
      int32_t want, here, step;
      if (sim_in[IN_STA + i] == 7) continue;
      if (sim_out[HS_OUT_TARGET + i] == HS_TGT_HOLD) continue;
      want = hs_target_to_ang(sim_out[HS_OUT_TARGET + i]) * 1000;
      here = sim_ang_milli[i];
      /* speed 1000 walks a finger through full travel in about 800 ms */
      step = (int32_t)(960L * 1000L / 800L) * (sim_out[HS_OUT_SPEED + i] ?
             sim_out[HS_OUT_SPEED + i] : 1000) / 1000;
      if (step < 1) step = 1;
      if (want > here) here = (here + step > want) ? want : here + step;
      else             here = (here - step < want) ? want : here - step;
      sim_ang_milli[i] = here;
      sim_in[IN_ANG + i] = (int16_t)(here / 1000);
      sim_in[IN_POS + i] = sim_in[IN_ANG + i];
   }
}

/* ---- trigger strategies --------------------------------------------- */

typedef struct {
   const char *name;
   const char *summary;
   int  (*arm)(void);            /* during bring-up, before the OP request */
   void (*on_target)(void);      /* a guarded target just reached out[] */
   void (*cycle)(void);          /* once per PDO period, after send/receive */
   int64_t (*align_ns)(void);    /* correction to the next wake-up, or NULL */
   int gates_on_exec;            /* does this trigger have an execution step
                                    of its own that a record must wait for? */
} trigger_t;

static const trigger_t *trig;

/* ---- latency instrumentation ----------------------------------------
 *
 * This is the ruler the DC question gets settled with, so it has to exist
 * before the measurement and it has to read the same way for both
 * triggers. Nothing here is trigger-specific: every stage is a pair of
 * timestamps, and the strategies differ only in which stages take time.
 *
 *   vision   frame grabbed        -> targets computed      (client)
 *   send     targets computed     -> written to the socket (client)
 *   ipc      written to socket    -> parsed here           (this host)
 *   queue    parsed               -> reaches the PDO buffer
 *   wire     reaches the buffer   -> the frame is actually sent
 *   exec     frame sent           -> the execution disconnect (disconnect only)
 *   move     frame sent           -> ANGLEACT starts changing
 *   total    frame grabbed        -> ANGLEACT starts changing
 *
 * The client stamps the first three with CLOCK_MONOTONIC; it runs on this
 * same host, so the clocks are the same clock. A client that sends no
 * stamps still gets everything from `ipc` onward.
 *
 * Motion onset is only tracked for a target that is a real step away from
 * what is already commanded, and only one at a time. In a 50 Hz stream
 * every frame supersedes the last one before an actuator could possibly
 * respond, so "onset" would be meaningless; the step response is the
 * thing worth comparing between triggers.
 */
#define LAT_RING   512
#define LAT_TIMEOUT_MS 4000

typedef struct {
   uint32_t id;
   long vision, send, ipc, queue, wire, exec, move, total;
   int moved;
} lat_rec_t;

static lat_rec_t lat_ring[LAT_RING];
static int lat_n, lat_next;
static FILE *lat_log;

static struct {
   int active;
   uint32_t id;
   int64_t t_frame, t_map, t_send, t_recv, t_write, t_wire, t_exec, t_move;
   int16_t ang0[6];
   int16_t tgt[6];
   int16_t cur_max;          /* highest current seen while this step ran */
} track;

static int64_t now_ns(void)
{
   struct timespec ts;
   clock_gettime(CLOCK_MONOTONIC, &ts);
   return (int64_t)ts.tv_sec * 1000000000L + ts.tv_nsec;
}

static long us_between(int64_t a, int64_t b)
{
   return (!a || !b) ? -1 : (long)((b - a) / 1000);
}

static void lat_flush(void)
{
   lat_rec_t *r = &lat_ring[lat_next];

   if (!track.active) return;
   r->id     = track.id;
   r->vision = us_between(track.t_frame, track.t_map);
   r->send   = us_between(track.t_map, track.t_send);
   r->ipc    = us_between(track.t_send, track.t_recv);
   r->queue  = us_between(track.t_recv, track.t_write);
   r->wire   = us_between(track.t_write, track.t_wire);
   r->exec   = us_between(track.t_wire, track.t_exec);
   r->move   = us_between(track.t_wire, track.t_move);
   r->total  = us_between(track.t_frame, track.t_move);
   r->moved  = track.t_move != 0;

   /* The one comparison nothing else here makes: was travel asked for and
      did any happen. Current keeps a stalled axis from counting - that is
      a different fault and hs_stall_relief owns it. */
   if (r->moved || track.cur_max > 20)
      dead_streak = 0;
   else if (++dead_streak >= cfg.stuck_strikes && !stuck)
   {
      stuck = 1;
      logf_("NOT APPLYING: %d commanded moves in a row produced no travel "
            "and no current, while the bus looks healthy. The slave has "
            "stopped consuming process data - most often because a second "
            "master (ecat_scan, another handd) touched this NIC and reset "
            "its state machine. Restarting is the only known recovery.",
            dead_streak);
      if (!strcmp(cfg.on_stuck, "exit"))
      {
         logf_("--on-stuck=exit: shutting down rather than answering "
               "clients ok while driving nothing");
         running = 0;
      }
      else
         logf_("--on-stuck=report: staying up, but `state` and `hello` now "
               "say applying=false");
   }

   lat_next = (lat_next + 1) % LAT_RING;
   if (lat_n < LAT_RING) lat_n++;

   if (lat_log)
   {
      fprintf(lat_log, "%u,%s,%ld,%ld,%ld,%ld,%ld,%ld,%ld,%ld,%d\n",
              r->id, trig->name, r->vision, r->send, r->ipc, r->queue,
              r->wire, r->exec, r->move, r->total, r->moved);
      fflush(lat_log);
   }
   track.active = 0;
}

/* The last pose a client actually asked for. Not out[], which the
   disconnect strategy parks back to "hold" on every reconnect - comparing
   against that would make almost nothing look like a step. */
static int16_t last_cmd[6] = { HS_TGT_HOLD, HS_TGT_HOLD, HS_TGT_HOLD,
                               HS_TGT_HOLD, HS_TGT_HOLD, HS_TGT_HOLD };

/* Is this target a step worth timing, rather than the next frame of a
   stream that is already moving? */
static int lat_is_step(const int16_t *tgt)
{
   int i, worst = 0;
   for (i = 0; i < 6; i++)
   {
      int d;
      if (tgt[i] == HS_TGT_HOLD) continue;
      if (last_cmd[i] == HS_TGT_HOLD) return 1;   /* first real command */
      d = tgt[i] - last_cmd[i];
      if (d < 0) d = -d;
      if (d > worst) worst = d;
   }
   return worst >= cfg.move_step;
}

static void lat_begin(const int16_t *tgt, int64_t t_frame, int64_t t_map,
                      int64_t t_send, int64_t t_recv)
{
   int i;
   if (track.active) return;              /* one step in flight at a time */
   if (!lat_is_step(tgt)) return;
   memset(&track, 0, sizeof track);
   track.active = 1;
   track.id = ++seq;
   track.t_frame = t_frame;
   track.t_map = t_map;
   track.t_send = t_send;
   track.t_recv = t_recv;
   memcpy(track.tgt, tgt, sizeof track.tgt);
   track.cur_max = 0;
   for (i = 0; i < 6; i++) track.ang0[i] = bus_up ? in[IN_ANG + i] : 0;
}

/* called once per cycle, after send/receive */
static void lat_sample(void)
{
   int i;
   if (!track.active) return;
   if (track.t_write && !track.t_wire) { track.t_wire = now_ns(); return; }
   if (!track.t_wire) return;
   if (bus_up)
      for (i = 0; i < 6; i++)
         if (in[IN_CUR + i] > track.cur_max) track.cur_max = in[IN_CUR + i];
   if (bus_up && !track.t_move)
      for (i = 0; i < 6; i++)
      {
         int d;
         if (track.tgt[i] == HS_TGT_HOLD) continue;
         d = in[IN_ANG + i] - track.ang0[i];
         if (d < 0) d = -d;
         if (d > cfg.move_eps) { track.t_move = now_ns(); break; }
      }
   /* Do not close the record the instant the axis twitches: with the
      disconnect trigger the execution step has its own timestamp and it
      can land after motion begins (it does in simulation, where nothing
      gates on the disconnect). Wait for the stage the trigger owes, or
      for the timeout. */
   if ((track.t_move && (!trig->gates_on_exec || track.t_exec)) ||
       now_ns() - track.t_wire > (int64_t)LAT_TIMEOUT_MS * 1000000L)
      lat_flush();
}

/* How late each wake-up was against the schedule it asked for. This is
   what --cpu / --rt-prio / isolcpus are bought with, so it is measured
   rather than assumed - and it is measured the same way under either
   trigger. */
#define JIT_RING 1024
static long jit_ring[JIT_RING];
static int jit_n, jit_next;
static long jit_worst;

static void jit_record(long late_us)
{
   if (late_us < 0) late_us = 0;
   jit_ring[jit_next] = late_us;
   jit_next = (jit_next + 1) % JIT_RING;
   if (jit_n < JIT_RING) jit_n++;
   if (late_us > jit_worst) jit_worst = late_us;
}

static int cmp_long(const void *a, const void *b)
{
   long x = *(const long *)a, y = *(const long *)b;
   return x < y ? -1 : (x > y ? 1 : 0);
}

static long jit_pct(int pct)
{
   long vals[JIT_RING];
   if (!jit_n) return -1;
   memcpy(vals, jit_ring, (size_t)jit_n * sizeof vals[0]);
   qsort(vals, (size_t)jit_n, sizeof vals[0], cmp_long);
   return vals[(jit_n - 1) * pct / 100];
}

/* percentile of one column across the ring, ignoring the rows where the
   stage did not apply (-1) */
static long lat_pct(size_t offset, int pct)
{
   long vals[LAT_RING];
   int i, n = 0;
   for (i = 0; i < lat_n; i++)
   {
      long v = *(long *)((char *)&lat_ring[i] + offset);
      if (v >= 0) vals[n++] = v;
   }
   if (!n) return -1;
   qsort(vals, (size_t)n, sizeof vals[0], cmp_long);
   return vals[(n - 1) * pct / 100];
}



static void pd(void)
{
   if (cfg.simulate) { sim_step(); return; }
   ecx_send_processdata(&ctx);
   ecx_receive_processdata(&ctx, EC_TIMEOUTRET);
}

static void cyc(int ms)
{
   int i;
   for (i = 0; i < ms; i++) { pd(); osal_usleep(1000); }
}

/* -- disconnect: today's verified behaviour, minus the process spawn -- */

enum { DS_IDLE, DS_HOLD, DS_DOWN };
static int ds_state = DS_IDLE;
static long ds_at;               /* when the current phase ends */
static int  ds_pending;          /* a target arrived while the link was down */
static int16_t ds_queued[6];

static int disc_arm(void)
{
   /* Deliberately no ecx_configdc() here. hand_ctl and hand_set drive this
      hand without ever touching distributed clocks, and that is the one
      sequence known to work; adding a DC call to the fallback path would
      put an untested variable underneath the strategy we fall back to. */
   return 0;
}

static void disc_on_target(void)
{
   /* Start the clock on the FIRST change since the last execution, not on
      the latest one. Restarting it per target reads reasonable until a
      client streams at 50-100 Hz, at which point the drop is deferred
      forever and the hand never executes anything - the exact failure the
      daemon exists to remove. Later targets inside the window just update
      the value that will ride out on the disconnect. */
   if (ds_state != DS_IDLE) return;
   ds_state = DS_HOLD;
   ds_at = now_ms() + cfg.hold_ms;
}

static int bus_bringup(void);
static void bus_close(void);
static int op_request(void);
static void apply_target(const int16_t *tgt, char *why, size_t n, int *guarded);

static void disc_cycle(void)
{
   long t = now_ms();
   if (ds_state == DS_HOLD && t >= ds_at)
   {
      /* the disconnect IS the execution command on this firmware */
      if (track.active && !track.t_exec) track.t_exec = now_ns();
      bus_close();
      ds_state = DS_DOWN;
      ds_at = t + cfg.settle_ms;
   }
   else if (ds_state == DS_DOWN && t >= ds_at)
   {
      int rc = bus_bringup();
      if (rc)
      {
         logf_("reconnect failed after the execution disconnect: %s",
               "see the bring-up error above - stopping rather than "
               "looping on a bus that is gone");
         running = 0;
         return;
      }
      ds_state = DS_IDLE;
      /* The last pose is deliberately NOT re-asserted here. bus_bringup
         parks every axis on hold, which leaves the hand where the
         disconnect put it; re-writing the old target instead would look
         like a new command and schedule the next disconnect, forever. A
         streaming client sends the next pose anyway. */
      if (ds_pending)
      {
         char why[256] = {0};
         int guarded = 0;
         ds_pending = 0;
         apply_target(ds_queued, why, sizeof why, &guarded);
      }
   }
}

static const trigger_t TRIG_DISCONNECT = {
   "disconnect",
   "write, hold, drop the link so the SM watchdog fires, reconnect",
   disc_arm, disc_on_target, disc_cycle, NULL, 1
};

/* -- watchdog: the trigger itself, with nothing torn down -------------
 *
 * watchdog_trigger.c separated the two things that a disconnect does at
 * once. Keeping the socket open, the bus lock held and the process image
 * mapped, and simply sending nothing for longer than the slave's
 * sync-manager watchdog, applies the pose: 100 ms of silence moved the
 * axis 98 counts with AL=0x0000 up to the moment it fired. The link never
 * had to go away.
 *
 * The slave pays for it by leaving OPERATIONAL - the watchdog expiry IS
 * the SAFE_OP+ERROR transition, AL=0x001b - so the strategy owns the
 * recovery: acknowledge the error latch, get process data flowing again,
 * and climb back. That is cheaper than ecx_close/ecx_init by everything
 * enumeration and PDO mapping cost, which is most of the disconnect
 * path's budget.
 *
 * The starve length is not a constant if it can be helped: the slave
 * reports its own watchdog in 0x0400 (divider, 40 ns ticks) and 0x0420
 * (process-data time, in those ticks), 99.9 ms on this unit, and the
 * default adds a margin to whatever it says. --starve-ms overrides.
 */
enum { WD_IDLE, WD_HOLD };
static int wd_state = WD_IDLE;
static long wd_at;
static int wd_ms_measured;        /* the slave's own watchdog, ms */
static int wd_starve_ms;          /* what we actually wait */
static int wd_fired, wd_recovered_hard;
static int wd_pending;
static int16_t wd_queued[6];

static int wd_arm(void)
{
   uint16 div = 0, pdt = 0;

   if (cfg.simulate)
   {
      wd_ms_measured = 100;
      wd_starve_ms = cfg.starve_ms ? cfg.starve_ms : 120;
      return 0;
   }
   ecx_FPRD(&ctx.port, ctx.slavelist[1].configadr, 0x0400, sizeof div, &div,
            EC_TIMEOUTRET);
   ecx_FPRD(&ctx.port, ctx.slavelist[1].configadr, 0x0420, sizeof pdt, &pdt,
            EC_TIMEOUTRET);
   div = etohs(div);
   pdt = etohs(pdt);
   /* divider counts 40 ns ticks; the process-data watchdog counts those */
   wd_ms_measured = (int)(((double)div * 0.000040 * (double)pdt) + 0.5);
   if (cfg.starve_ms)
      wd_starve_ms = cfg.starve_ms;
   else if (wd_ms_measured > 0 && wd_ms_measured < 5000)
      wd_starve_ms = wd_ms_measured + 20;
   else
   {
      wd_starve_ms = 120;
      logf_("WARNING: the slave's watchdog registers read %u/%u, which is "
            "not a usable time - falling back to a %d ms starve. If poses "
            "stop executing, this is the first thing to check.",
            div, pdt, wd_starve_ms);
   }
   logf_("watchdog trigger: slave watchdog %d ms (0x0400=%u 0x0420=%u), "
         "starving %d ms per pose", wd_ms_measured, div, pdt, wd_starve_ms);
   return 0;
}

static void wd_on_target(void)
{
   /* same reasoning as the disconnect strategy: time the window from the
      first change since the last execution, or a client streaming at
      50-100 Hz defers the trigger forever */
   if (wd_state != WD_IDLE) return;
   wd_state = WD_HOLD;
   wd_at = now_ms() + cfg.hold_ms;
}

/* Bring the slave back after its watchdog fired. Returns 0 on success. */
static int wd_recover(void)
{
   int i;

   ecx_readstate(&ctx);
   if (ctx.slavelist[1].state == EC_STATE_OPERATIONAL) return 0;

   /* An error state latches: the slave will refuse to leave SAFE_OP+ERROR
      until the master acknowledges it, and asking for OPERATIONAL without
      the acknowledgement is the mistake that reads as "the hand died". */
   ctx.slavelist[0].state = EC_STATE_SAFE_OP + EC_STATE_ACK;
   ecx_writestate(&ctx, 0);
   ecx_statecheck(&ctx, 0, EC_STATE_SAFE_OP, EC_TIMEOUTSTATE);
   for (i = 0; i < 20; i++) { pd(); osal_usleep(1000); }

   if (op_request()) return 0;

   /* the cheap path failed; fall back to the expensive one rather than
      leaving a daemon that answers but cannot move anything */
   logf_("watchdog recovery could not re-enter OPERATIONAL (state=0x%02x "
         "AL=0x%04x) - falling back to a full reconnect",
         ctx.slavelist[1].state, ctx.slavelist[1].ALstatuscode);
   wd_recovered_hard++;
   bus_close();
   return bus_bringup();
}

static void wd_cycle(void)
{
   int rc;

   if (wd_state != WD_HOLD || now_ms() < wd_at) return;

   /* the silence IS the execution command on this firmware */
   if (track.active && !track.t_exec) track.t_exec = now_ns();
   wd_fired++;
   wd_state = WD_IDLE;

   if (cfg.simulate) return;   /* the stand-in applies targets continuously */

   /* Send nothing at all. Not a shorter cycle, not empty frames - the
      watchdog counts frames, so anything on the wire resets it. */
   osal_usleep((unsigned)wd_starve_ms * 1000u);

   rc = wd_recover();
   if (rc)
   {
      logf_("bus did not come back after a watchdog trigger - stopping "
            "rather than looping on a bus that is gone");
      running = 0;
      return;
    }
   /* Deliberately not re-asserting the last pose: it has been applied,
      and re-writing it would look like a new command and schedule another
      starve, forever. bus_bringup (if the hard path ran) parks holds. */
   if (wd_pending)
   {
      char why[256] = {0};
      int guarded = 0;
      wd_pending = 0;
      apply_target(wd_queued, why, sizeof why, &guarded);
   }
}

static const trigger_t TRIG_WATCHDOG = {
   "watchdog",
   "write, then send nothing until the slave's own watchdog applies it",
   wd_arm, wd_on_target, wd_cycle, NULL, 1
};

/* -- continuous: what an EtherCAT slave is supposed to need -----------
 *
 * Nothing to arm, nothing to do on a target, nothing to do per cycle: the
 * loop already writes the guarded target into the output image every
 * period, and the slave applies it. The whole strategy is the absence of
 * one. It only became available when rate_sweep found that the feed rate,
 * not the firmware, was what had been stopping it - so the thing this
 * struct really carries is the rate requirement, which lives in
 * --rate-hz and is checked at startup rather than assumed here. */
static int  cont_arm(void)       { return 0; }
static void cont_on_target(void) { }
static void cont_cycle(void)     { }

static const trigger_t TRIG_CONTINUOUS = {
   "continuous",
   "write the target and keep cycling; the slave applies it in OP",
   cont_arm, cont_on_target, cont_cycle, NULL, 0
};

/* -- sync0: distributed clocks, unproven on this hand -----------------
 *
 * The theory this implements: an SSC application commonly copies its PDO
 * output buffer into the application variables inside the Sync0
 * interrupt. If this firmware does that, never starting DC means the
 * targets sit in the buffer unread - and "the hand only moves when you
 * disconnect" would be our omission rather than its design.
 *
 * 2026-08-06 settled it, and the answer is no. On a direct link with no
 * switch, Sync0 armed and DCactive=1, the slave held OPERATIONAL for ten
 * seconds with AL=0x0000 and the axis still never moved and never drew a
 * milliamp. The sync mode is not the mechanism either: CoE reports
 * 0x1C32:01=1, SM-Synchron, and writing 0x1C32:01=0 for Free Run was
 * accepted and changed nothing. The trigger is the SM watchdog. What
 * follows is correct EtherCAT and irrelevant to moving this hand.
 * See the vault's Execution_Trigger_Settled.
 */
static int64_t dc_period_ns;
static int64_t dc_delta_ns;      /* last measured distance from the edge */
static int64_t dc_offset_ns;     /* correction currently being applied */
static int64_t dc_delta_worst;

static int sync0_arm(void)
{
   if (cfg.simulate) { dc_period_ns = 1000000000L / cfg.rate_hz; return 0; }

   dc_hasdc = ctx.slavelist[1].hasdc;
   if (!dc_hasdc) return 5;
   dc_configured = ecx_configdc(&ctx) ? 1 : 0;
   dc_period_ns = (int64_t)(cfg.dc_cycle_us ? cfg.dc_cycle_us
                                            : 1000000 / cfg.rate_hz) * 1000;
   /* armed before the OP request, so the slave is already producing the
      interrupt by the time it starts consuming process data */
   ecx_dcsync0(&ctx, 1, TRUE, (uint32_t)dc_period_ns, 0);
   dc_active = ctx.slavelist[1].DCactive;
   dc_pdelay = ctx.slavelist[1].pdelay;

   /* pdelay=0 was once read here as the signature of a switch in the path.
      It is not: on a bus with one slave, that slave IS the reference
      clock, so zero propagation delay is the correct measurement rather
      than a missing one. Logged as a fact, not a warning. */
   if (dc_pdelay == 0)
      logf_("pdelay=0 after configdc - expected on a single-slave bus, "
            "where the only slave is the reference clock.");
   return 0;
}

static void sync0_on_target(void) { /* the loop already writes it out */ }
static void sync0_cycle(void)     { /* Sync0 does the rest */ }

/* Keep the local cycle a fixed distance ahead of the slave's Sync0 edge.
   This is the controller from SOEM's own sample, gains included: the
   proportional term pulls the phase in and the integral term absorbs the
   drift between the host clock and the slave's. Untested against this
   hand - it has never held OPERATIONAL with DC armed. */
static int64_t sync0_align_ns(void)
{
   static int64_t integral;
   int64_t delta;

   if (cfg.simulate || dc_period_ns <= 0) return 0;
   delta = (ctx.DCtime - (int64_t)cfg.dc_shift_us * 1000) % dc_period_ns;
   if (delta > dc_period_ns / 2) delta -= dc_period_ns;
   if (delta > 0) integral++;
   if (delta < 0) integral--;
   dc_delta_ns = delta;
   if (delta < 0 ? -delta > dc_delta_worst : delta > dc_delta_worst)
      dc_delta_worst = delta < 0 ? -delta : delta;
   dc_offset_ns = -(delta / 100) - (integral / 20);
   return dc_offset_ns;
}

static const trigger_t TRIG_SYNC0 = {
   "sync0",
   "arm distributed clocks; the slave applies its own buffer on Sync0",
   sync0_arm, sync0_on_target, sync0_cycle, sync0_align_ns, 0
};

static const trigger_t *TRIGGERS[] = { &TRIG_CONTINUOUS, &TRIG_WATCHDOG,
                                       &TRIG_DISCONNECT, &TRIG_SYNC0, NULL };

/* ---- bring-up ------------------------------------------------------- */

static const char *bringup_err(int rc)
{
   switch (rc)
   {
      case 1: return "ecx_init failed - wrong interface, or missing "
                     "CAP_NET_RAW (run: make cap)";
      case 2: return "no EtherCAT slave answered - check the cable, the "
                     "hand's power, and which NIC it is on (ecat_scan)";
      case 3: return "the slave refused OPERATIONAL";
      case 4: return "the PDO map is smaller than the RH56F1 layout needs";
      case 5: return "the trigger needs distributed clocks and the slave "
                     "reports hasdc=0";
   }
   return "unknown failure";
}

/* The AL status code is the only place the slave says WHY it refused a
   state, and for the DC work it is the entire answer - so translate the
   handful that matter here instead of leaving a hex number in a log. */
static const char *al_reading(uint16_t al)
{
   switch (al)
   {
      case 0x0000: return "";
      case 0x001a: return "Synchronization Error - the slave is not seeing "
                          "process data arrive when it expects it";
      case 0x001b: return "Sync Manager Watchdog - process data stopped "
                          "arriving";
      case 0x002d: return "No Sync Error - the slave is in a DC sync mode "
                          "and the Sync0 signal it expects is not arriving. "
                          "Seen on this hand even on a direct link, and a "
                          "dead end regardless: DC is not what makes it "
                          "apply outputs";
      case 0x0030: return "DC Invalid Sync Configuration";
      case 0x0032: return "DC Sync started while the PLL was not locked";
      case 0x0033: return "DC Invalid Sync Cycle Time";
      case 0x0034: return "DC Sync0 Cycle Time does not fit the application";
      case 0x001e: return "Invalid Input Configuration";
      case 0x001f: return "Invalid Output Configuration";
   }
   return "see the ETG AL status code table";
}

/* Ask for OPERATIONAL without ever letting the process data drop below
   the loop rate. Cadence during the transition is not cosmetic: this
   daemon used to send one frame per 50 ms statecheck - about 20 Hz - and
   the 2026-08-06 runs showed that a slave with Sync0 armed refuses the
   transition outright at that rate (AL=0x002d, "no sync"), while 1 kHz
   through the whole transition reaches OP in 80-160 ms. Returns 1 on
   OPERATIONAL. */
static int op_request(void)
{
   int i;

   if (cfg.simulate) return 1;
   ctx.slavelist[0].state = EC_STATE_OPERATIONAL;
   pd();
   ecx_writestate(&ctx, 0);
   for (i = 0; i < 2000; i++)
   {
      pd();
      if (i % 20 == 0)
      {
         ecx_statecheck(&ctx, 0, EC_STATE_OPERATIONAL, 0);
         if (ctx.slavelist[0].state == EC_STATE_OPERATIONAL) break;
      }
      osal_usleep(1000);
   }
   ecx_readstate(&ctx);
   return ctx.slavelist[1].state == EC_STATE_OPERATIONAL;
}

static int bus_bringup(void)
{
   int i, rc;

   if (cfg.simulate)
   {
      in = sim_in;
      out = sim_out;
      have_tac = 1;
      bus_up = 1;
      out[0] = 1;
      for (i = 0; i < 6; i++) out[HS_OUT_TARGET + i] = HS_TGT_HOLD;
      hs_profile(out, cfg.force, cfg.speed);
      return 0;
   }

   if (!ecx_init(&ctx, cfg.iface)) return 1;
   if (ecx_config_init(&ctx) <= 0) return 2;
   ctx.slavelist[1].mbx_proto = 0;   /* NOT because CoE is dead - it answers
      every SDO. Zeroing it makes SOEM size the output image from the SII
      at 38 bytes, the only layout this firmware accepts; mapping over CoE
      yields 18 and is refused with AL=0x001e. */
   ecx_config_map_group(&ctx, IOmap, 0);
   if (ctx.slavelist[1].Ibytes < 36 * 2 || ctx.slavelist[1].Obytes < 19 * 2)
      return 4;
   have_tac = ctx.slavelist[1].Ibytes >= (IN_TAC + IN_TAC_N) * 2;

   out = (int16_t *)ctx.slavelist[1].outputs;
   in  = (int16_t *)ctx.slavelist[1].inputs;
   /* a zeroed output buffer reads as "close every axis"; park holds before
      the first frame can carry that pattern onto the wire */
   memset(out, 0, ctx.slavelist[1].Obytes);
   for (i = 0; i < 6; i++) out[HS_OUT_TARGET + i] = HS_TGT_HOLD;

   ecx_statecheck(&ctx, 0, EC_STATE_SAFE_OP, EC_TIMEOUTSTATE * 4);

   rc = trig->arm();
   if (rc) return rc;

   if (!op_request()) return 3;

   bus_up = 1;
   out[0] = 1;                                  /* enable word */
   for (i = 0; i < 6; i++) out[HS_OUT_TARGET + i] = HS_TGT_HOLD;
   hs_profile(out, cfg.force, cfg.speed);
   cyc(200);
   return 0;
}

static void bus_close(void)
{
   if (!bus_up) return;
   bus_up = 0;
   if (cfg.simulate) return;
   ecx_close(&ctx);
   in = out = NULL;
}

/* Boot lands every axis in STATUS=7 and a pose is ignored until they
   leave it. Only pay for the wiggle when an axis is actually in standby -
   the probe's reconnects found none and spent no time here. */
static int wake_if_asleep(void)
{
   int asleep = 0, t, i;
   for (i = 0; i < 6; i++) if (in[IN_STA + i] == 7) asleep = 1;
   if (!asleep) return 0;
   logf_("axes in standby (STA=7) - waking");
   for (t = 0; t < WAKE_MS_MAX && asleep; t++)
   {
      for (i = 0; i < 6; i++)
      {
         int16_t base = in[IN_ANG + i];
         if (base < 200)  base = 200;
         if (base > 1800) base = 1800;
         out[HS_OUT_TARGET + i] = base + (((t / 400) % 2) ? 60 : -60);
      }
      pd();
      if (t % 200 == 0)
      {
         asleep = 0;
         for (i = 0; i < 6; i++) if (in[IN_STA + i] == 7) asleep = 1;
      }
      osal_usleep(1000);
   }
   for (i = 0; i < 6; i++) out[HS_OUT_TARGET + i] = HS_TGT_HOLD;
   if (asleep) logf_("wake gave up after %d ms - axes still in standby", t);
   else        logf_("awake after %d ms", t);
   return asleep;
}

/* ---- the guard every client goes through ---------------------------- */

static void apply_target(const int16_t *want, char *why, size_t n, int *guarded)
{
   int16_t tgt[6];
   int i;

   memcpy(tgt, want, sizeof tgt);
   if (!bus_up)
   {
      /* the disconnect strategy spends part of every cycle with the link
         down; hold the target until there is telemetry to judge it by,
         because the guard reads live ANGLEACT to decide */
      memcpy(ds_queued, tgt, sizeof ds_queued);
      ds_pending = 1;
      *guarded = 0;
      return;
   }
   *guarded  = hs_stall_relief(tgt, &in[IN_CUR], &in[IN_STA], &in[IN_ANG], why, n);
   *guarded += hs_interlock(tgt, &in[IN_ANG], why, n);
   if (track.active && !track.t_write)
   {
      /* the guard may have altered the pose; time what actually goes out */
      memcpy(track.tgt, tgt, sizeof track.tgt);
      for (i = 0; i < 6; i++) track.ang0[i] = in[IN_ANG + i];
      track.t_write = now_ns();
   }
   for (i = 0; i < 6; i++) out[HS_OUT_TARGET + i] = tgt[i];
   memcpy(last_cmd, tgt, sizeof last_cmd);
   trig->on_target();
}

/* ---- unix socket ---------------------------------------------------- */

typedef struct {
   int fd;
   char buf[CLIENT_BUF];
   size_t len;
} client_t;

static client_t clients[MAX_CLIENTS];
static int listen_fd = -1;

static void creply(client_t *c, const char *fmt, ...)
   __attribute__((format(printf, 2, 3)));
static void creply(client_t *c, const char *fmt, ...)
{
   char line[1024];
   int n;
   va_list ap;
   va_start(ap, fmt);
   n = vsnprintf(line, sizeof line - 2, fmt, ap);
   va_end(ap);
   if (n < 0) return;
   line[n++] = '\n';
   /* a client that stopped reading must not stall the PDO loop */
   if (write(c->fd, line, (size_t)n) < 0 && errno != EAGAIN)
      c->fd = -1;
}

static void jarr(char *dst, size_t n, const int16_t *v)
{
   snprintf(dst, n, "[%d,%d,%d,%d,%d,%d]", v[0], v[1], v[2], v[3], v[4], v[5]);
}

static void jarrn(char *dst, size_t n, const int16_t *v, int count)
{
   size_t off = 0;
   int i;
   off += (size_t)snprintf(dst + off, n - off, "[");
   for (i = 0; i < count && off < n; i++)
      off += (size_t)snprintf(dst + off, n - off, "%s%d", i ? "," : "", v[i]);
   if (off < n) snprintf(dst + off, n - off, "]");
}

static void reply_state(client_t *c)
{
   char pos[64], ang[64], frc[64], cur[64], err[64], sta[64], tmp[64];
   char tac[256];
   if (!bus_up)
   {
      creply(c, "{\"ok\":true,\"bus\":\"down\",\"note\":\"between execution "
                "disconnects - telemetry resumes on reconnect\"}");
      return;
   }
   jarr(pos, sizeof pos, &in[IN_POS]);
   jarr(ang, sizeof ang, &in[IN_ANG]);
   jarr(frc, sizeof frc, &in[IN_FRC]);
   jarr(cur, sizeof cur, &in[IN_CUR]);
   jarr(err, sizeof err, &in[IN_ERR]);
   jarr(sta, sizeof sta, &in[IN_STA]);
   jarr(tmp, sizeof tmp, &in[IN_TMP]);
   /* null, not [], on a hand without the T1 option: a client telling the
      two apart must not have to guess from an empty list */
   if (have_tac) jarrn(tac, sizeof tac, &in[IN_TAC], IN_TAC_N);
   else          strcpy(tac, "null");
   creply(c, "{\"ok\":true,\"bus\":\"up\",\"applying\":%s,\"simulate\":%s,"
             "\"pos\":%s,\"ang\":%s,"
             "\"frc\":%s,\"cur\":%s,\"err\":%s,\"sta\":%s,\"tmp\":%s,"
             "\"tac\":%s}",
          stuck ? "false" : "true",
          cfg.simulate ? "true" : "false", pos, ang, frc, cur, err, sta, tmp,
          tac);
}

static void handle_line(client_t *c, char *line)
{
   char *cmd = strtok(line, " \t");
   if (!cmd) return;

   if (!strcmp(cmd, "hello"))
   {
      char js[160], tl[160];
      const trigger_t **t;
      int n = 0;
      hs_scale_json(js, sizeof js);
      /* the strategies this build carries, so a client can tell whether it
         is talking to a daemon that knows about the continuous path */
      n += snprintf(tl + n, sizeof tl - n, "[");
      for (t = TRIGGERS; *t && n < (int)sizeof tl - 2; t++)
         n += snprintf(tl + n, sizeof tl - n, "%s\"%s\"",
                       t == TRIGGERS ? "" : ",", (*t)->name);
      snprintf(tl + n, sizeof tl - n, "]");
      creply(c, "{\"ok\":true,\"daemon\":\"handd\",\"trigger\":\"%s\","
                "\"triggers\":%s,"
                "\"rate_hz\":%d,\"force\":%d,\"speed\":%d,\"applying\":%s,"
                "\"simulate\":%s,"
                "\"scale\":%s}",
             trig->name, tl, cfg.rate_hz, cfg.force, cfg.speed,
             stuck ? "false" : "true",
             cfg.simulate ? "true" : "false", js);
   }
   else if (!strcmp(cmd, "scale"))
   {
      char js[160];
      hs_scale_json(js, sizeof js);
      creply(c, "{\"ok\":true,\"scale\":%s}", js);
   }
   else if (!strcmp(cmd, "state"))
      reply_state(c);
   else if (!strcmp(cmd, "dc"))
   {
      /* everything the decisive DC run needs to be read without stopping
         the loop, including the number that gives the topology away */
      uint16_t al = cfg.simulate ? 0 : ctx.slavelist[1].ALstatuscode;
      creply(c, "{\"ok\":true,\"trigger\":\"%s\",\"hasdc\":%d,\"configdc\":%d,"
                "\"dcactive\":%d,\"pdelay\":%d,\"al\":%u,\"al_reading\":\"%s\","
                "\"cycle_ns\":%lld,\"delta_ns\":%lld,\"worst_delta_ns\":%lld,"
                "\"offset_ns\":%lld}",
             trig->name, dc_hasdc, dc_configured, dc_active, dc_pdelay,
             (unsigned)al, al_reading(al), (long long)dc_period_ns,
             (long long)dc_delta_ns, (long long)dc_delta_worst,
             (long long)dc_offset_ns);
   }
   else if (!strcmp(cmd, "target"))
   {
      int16_t tgt[6];
      char why[256] = {0};
      int i, guarded = 0;
      int64_t t_frame = 0, t_map = 0, t_send = 0, t_recv = now_ns();
      char *tok;
      for (i = 0; i < 6; i++)
      {
         long v;
         tok = strtok(NULL, " \t");
         if (!tok)
         {
            creply(c, "{\"ok\":false,\"error\":\"target needs 6 values "
                      "(pinky ring middle index thumb_bend thumb_rot)\"}");
            return;
         }
         v = strtol(tok, NULL, 10);
         /* clamp rather than reject: a streaming source that overshoots
            should degrade to the nearest legal pose, not drop a frame */
         tgt[i] = hs_clamp_target((int16_t)(v < -32768 ? -32768 :
                                            v > 32767 ? 32767 : v));
      }
      /* Optional stamps let the client hand over the stages only it can
         see. Same host, same CLOCK_MONOTONIC, so they are comparable
         without any clock synchronisation. */
      while ((tok = strtok(NULL, " \t")) != NULL)
      {
         if      (!strncmp(tok, "t_frame=", 8)) t_frame = strtoll(tok + 8, NULL, 10);
         else if (!strncmp(tok, "t_map=", 6))   t_map   = strtoll(tok + 6, NULL, 10);
         else if (!strncmp(tok, "t_send=", 7))  t_send  = strtoll(tok + 7, NULL, 10);
         else
         {
            creply(c, "{\"ok\":false,\"error\":\"target takes 6 values plus "
                      "optional t_frame=/t_map=/t_send= stamps, not '%s'\"}", tok);
            return;
         }
      }
      lat_begin(tgt, t_frame, t_map, t_send, t_recv);
      apply_target(tgt, why, sizeof why, &guarded);
      creply(c, "{\"ok\":true,\"seq\":%u,\"guarded\":%d,\"guard_note\":\"%s\","
                "\"queued\":%s}",
             ++seq, guarded, why, bus_up ? "false" : "true");
   }
   else if (!strcmp(cmd, "profile"))
   {
      /* hand_api.InspireHand.pose() has always taken force and speed per
         call. The daemon only had them as start-up flags, so routing that
         API through here would have silently dropped two arguments - an
         API that still type-checks but no longer means what it says. This
         command exists so it keeps meaning it. */
      char *tf = strtok(NULL, " \t"), *ts = strtok(NULL, " \t");
      long f, sp;
      if (!tf || !ts)
      {
         creply(c, "{\"ok\":false,\"error\":\"profile needs force and "
                   "speed\"}");
         return;
      }
      f = strtol(tf, NULL, 10);
      sp = strtol(ts, NULL, 10);
      if (f < 0 || f > 1000 || sp < 50 || sp > 1000)
      {
         creply(c, "{\"ok\":false,\"error\":\"force 0..1000, speed "
                   "50..1000\"}");
         return;
      }
      cfg.force = (int)f;
      cfg.speed = (int)sp;
      /* write it into the live output image now rather than waiting for
         the next target, so a client that sets a profile and then reads
         state sees a hand that already agrees with it */
      if (bus_up && out) hs_profile(out, cfg.force, cfg.speed);
      creply(c, "{\"ok\":true,\"force\":%d,\"speed\":%d}",
             cfg.force, cfg.speed);
   }
   else if (!strcmp(cmd, "stats"))
   {
      /* the same breakdown for either trigger - that is the point of it */
      creply(c, "{\"ok\":true,\"trigger\":\"%s\",\"samples\":%d,"
                "\"unit\":\"us\",\"p50\":{\"vision\":%ld,\"send\":%ld,"
                "\"ipc\":%ld,\"queue\":%ld,\"wire\":%ld,\"exec\":%ld,"
                "\"move\":%ld,\"total\":%ld},\"p95_total\":%ld,"
                "\"max_total\":%ld,\"cycle_late_us\":{\"samples\":%d,"
                "\"p50\":%ld,\"p95\":%ld,\"p99\":%ld,\"max\":%ld}}",
             trig->name, lat_n,
             lat_pct(offsetof(lat_rec_t, vision), 50),
             lat_pct(offsetof(lat_rec_t, send), 50),
             lat_pct(offsetof(lat_rec_t, ipc), 50),
             lat_pct(offsetof(lat_rec_t, queue), 50),
             lat_pct(offsetof(lat_rec_t, wire), 50),
             lat_pct(offsetof(lat_rec_t, exec), 50),
             lat_pct(offsetof(lat_rec_t, move), 50),
             lat_pct(offsetof(lat_rec_t, total), 50),
             lat_pct(offsetof(lat_rec_t, total), 95),
             lat_pct(offsetof(lat_rec_t, total), 100),
             jit_n, jit_pct(50), jit_pct(95), jit_pct(99), jit_worst);
   }
   else if (!strcmp(cmd, "bye"))
   {
      creply(c, "{\"ok\":true,\"bye\":true}");
      close(c->fd);
      c->fd = -1;
   }
   else
      creply(c, "{\"ok\":false,\"error\":\"unknown command '%s' - try hello, "
                "scale, dc, state, stats, target, profile, bye\"}", cmd);
}

static int socket_open(const char *path)
{
   struct sockaddr_un addr;
   int fd;

   if (strlen(path) >= sizeof addr.sun_path)
   {
      logf_("socket path is too long: %s", path);
      return -1;
   }
   fd = socket(AF_UNIX, SOCK_STREAM, 0);
   if (fd < 0) { logf_("socket(): %s", strerror(errno)); return -1; }
   memset(&addr, 0, sizeof addr);
   addr.sun_family = AF_UNIX;
   strcpy(addr.sun_path, path);

   if (bind(fd, (struct sockaddr *)&addr, sizeof addr) < 0)
   {
      /* a leftover socket file from a killed daemon must not block the
         next start, but a live one must - so ask it before unlinking */
      int probe = socket(AF_UNIX, SOCK_STREAM, 0);
      if (errno == EADDRINUSE && probe >= 0 &&
          connect(probe, (struct sockaddr *)&addr, sizeof addr) == 0)
      {
         close(probe);
         close(fd);
         logf_("another handd is already listening on %s", path);
         return -1;
      }
      if (probe >= 0) close(probe);
      unlink(path);
      if (bind(fd, (struct sockaddr *)&addr, sizeof addr) < 0)
      {
         logf_("bind(%s): %s", path, strerror(errno));
         close(fd);
         return -1;
      }
   }
   if (listen(fd, MAX_CLIENTS) < 0)
   {
      logf_("listen(): %s", strerror(errno));
      close(fd);
      return -1;
   }
   /* clients are unprivileged; the daemon holds the raw-socket capability */
   chmod(path, 0666);
   fcntl(fd, F_SETFL, O_NONBLOCK);
   return fd;
}

static void serve_clients(void)
{
   struct pollfd pfd[MAX_CLIENTS + 1];
   int nfd = 0, i, map[MAX_CLIENTS + 1];

   pfd[nfd].fd = listen_fd;
   pfd[nfd].events = POLLIN;
   map[nfd++] = -1;
   for (i = 0; i < MAX_CLIENTS; i++)
      if (clients[i].fd >= 0)
      {
         pfd[nfd].fd = clients[i].fd;
         pfd[nfd].events = POLLIN;
         map[nfd++] = i;
      }
   if (poll(pfd, (nfds_t)nfd, 0) <= 0) return;

   for (i = 0; i < nfd; i++)
   {
      if (!(pfd[i].revents & (POLLIN | POLLHUP | POLLERR))) continue;
      if (map[i] < 0)
      {
         int cfd = accept(listen_fd, NULL, NULL), s;
         if (cfd < 0) continue;
         fcntl(cfd, F_SETFL, O_NONBLOCK);
         for (s = 0; s < MAX_CLIENTS; s++) if (clients[s].fd < 0) break;
         if (s == MAX_CLIENTS) { close(cfd); continue; }
         clients[s].fd = cfd;
         clients[s].len = 0;
      }
      else
      {
         client_t *c = &clients[map[i]];
         ssize_t n = read(c->fd, c->buf + c->len, sizeof c->buf - c->len - 1);
         char *nl;
         if (n <= 0)
         {
            if (n == 0 || (errno != EAGAIN && errno != EINTR))
            { close(c->fd); c->fd = -1; }
            continue;
         }
         c->len += (size_t)n;
         c->buf[c->len] = 0;
         while (c->fd >= 0 && (nl = strchr(c->buf, '\n')) != NULL)
         {
            size_t used = (size_t)(nl - c->buf) + 1;
            *nl = 0;
            handle_line(c, c->buf);
            memmove(c->buf, c->buf + used, c->len - used);
            c->len -= used;
            c->buf[c->len] = 0;
         }
         if (c->len >= sizeof c->buf - 1)
         {
            /* a line longer than the buffer is a broken client, not a pose */
            c->len = 0;
            if (c->fd >= 0) creply(c, "{\"ok\":false,\"error\":\"line too long\"}");
         }
      }
   }
}

/* ---- determinism ----------------------------------------------------
 *
 * The board runs 5.15.0-xilinx-zynqmp, which is not PREEMPT_RT, so these
 * are the cheap measures: keep the loop on one core, run it ahead of
 * everything else on that core, and stop the kernel reclaiming its pages.
 * Isolating the core itself (isolcpus) is a boot-time change and lives in
 * experiments/rt_check.sh, which reports rather than applies it.
 *
 * Each one says whether it actually took effect. A daemon that silently
 * failed to get SCHED_FIFO and then produced jitter numbers would be
 * worse than one that never asked.
 */
static void apply_determinism(void)
{
   if (cfg.cpu >= 0)
   {
      cpu_set_t set;
      CPU_ZERO(&set);
      CPU_SET(cfg.cpu, &set);
      if (sched_setaffinity(0, sizeof set, &set) == 0)
         logf_("pinned to CPU %d", cfg.cpu);
      else
         logf_("WARNING: could not pin to CPU %d (%s) - the loop is still "
               "free to migrate", cfg.cpu, strerror(errno));
   }
   if (cfg.rt_prio > 0)
   {
      struct sched_param sp;
      memset(&sp, 0, sizeof sp);
      sp.sched_priority = cfg.rt_prio;
      if (sched_setscheduler(0, SCHED_FIFO, &sp) == 0)
         logf_("SCHED_FIFO priority %d", cfg.rt_prio);
      else
         logf_("WARNING: SCHED_FIFO %d refused (%s) - still SCHED_OTHER. "
               "The binary needs cap_sys_nice (make cap) or root",
               cfg.rt_prio, strerror(errno));
   }
   if (cfg.lock_memory)
   {
      if (mlockall(MCL_CURRENT | MCL_FUTURE) == 0)
         logf_("memory locked");
      else
         logf_("WARNING: mlockall refused (%s) - a page fault can still "
               "stall a cycle. The binary needs cap_ipc_lock (make cap)",
               strerror(errno));
   }
}

/* ---- main loop ------------------------------------------------------ */

static void run_loop(void)
{
   struct timespec next;
   long period_ns = 1000000000L / cfg.rate_hz;

   clock_gettime(CLOCK_MONOTONIC, &next);
   while (running)
   {
      /* The correction is what makes this a DC-aligned loop rather than a
         free-running one: it slides the local period so the frame keeps
         arriving a fixed distance before the slave's Sync0 edge. The
         disconnect strategy has no such edge and returns nothing. */
      long adjust = trig->align_ns ? (long)trig->align_ns() : 0;
      next.tv_nsec += period_ns + adjust;
      while (next.tv_nsec >= 1000000000L) { next.tv_nsec -= 1000000000L; next.tv_sec++; }
      while (next.tv_nsec < 0) { next.tv_nsec += 1000000000L; next.tv_sec--; }
      clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next, NULL);
      {
         struct timespec woke;
         clock_gettime(CLOCK_MONOTONIC, &woke);
         jit_record((long)(((int64_t)(woke.tv_sec - next.tv_sec) * 1000000000L
                            + (woke.tv_nsec - next.tv_nsec)) / 1000));
      }

      serve_clients();
      if (bus_up) pd();
      else if (cfg.simulate) sim_step();   /* the firmware under test moves
                                              during the disconnect, so the
                                              stand-in has to as well */
      lat_sample();
      trig->cycle();
   }
}

/* ---- startup -------------------------------------------------------- */

static int int_arg(const char *val, const char *name, int lo, int hi, int *dst)
{
   char *end;
   long v = strtol(val, &end, 10);
   if (*end || v < lo || v > hi)
   {
      fprintf(stderr, "handd: --%s must be an integer %d..%d, got '%s'\n",
              name, lo, hi, val);
      return 1;
   }
   *dst = (int)v;
   return 0;
}

static void usage(void)
{
   const trigger_t **t;
   fprintf(stderr,
      "usage: handd [options]\n"
      "  --iface=NAME       NIC the master opens (default $ECAT_IFACE, else eth0)\n"
      "  --trigger=NAME     what makes the hand execute (default continuous)\n"
      "  --socket=PATH      unix socket clients connect to (default %s)\n"
      "  --rate-hz=N        PDO cycle rate, 50..2000 (default 500).\n"
      "                     Do NOT use 1000: measured 2026-08-06, this\n"
      "                     hand applies no output at all at 1 kHz and\n"
      "                     works at every rate 500 Hz and below.\n"
      "  --dc-cycle-us=N    sync0 period; 0 follows the PDO rate\n"
      "  --dc-shift-us=N    how far ahead of the Sync0 edge to aim (default 50)\n"
      "  --force=N          0..1000 (default 500)\n"
      "  --speed=N          50..1000 (default 1000)\n"
      "  --hold-ms=N        watchdog/disconnect: target rides this long first\n"
      "  --settle-ms=N      disconnect: how long the link stays down\n"
      "  --starve-ms=N      watchdog: how long to send nothing. 0 (default)\n"
      "                     reads the slave's own watchdog and adds 20 ms\n"
      "  --simulate         no bus; a stand-in slave for testing clients\n"
      "  --explain-al=CODE  read an AL status code aloud and exit\n"
      "  --latency-log=PATH CSV of the per-step latency breakdown\n"
      "  --move-step=N      target change big enough to time (default 200)\n"
      "  --stuck-strikes=N  commanded moves that may produce nothing before\n"
      "                     the slave is called stuck (default 3)\n"
      "  --on-stuck=WHAT    exit (default) or report. exit is the safer\n"
      "                     default: a daemon that answers ok while driving\n"
      "                     nothing is worse than one that is gone, and a\n"
      "                     restart is the only known recovery anyway.\n"
      "  --move-eps=N       ANGLEACT change that counts as motion (default 10)\n"
      "  --cpu=N            pin the PDO loop to one core\n"
      "  --rt-prio=N        run it SCHED_FIFO at this priority (needs cap_sys_nice)\n"
      "  --lock-memory      mlockall, so a page fault cannot stall a cycle\n"
      "\ntriggers:\n", SOCKET_DEFAULT);
   for (t = TRIGGERS; *t; t++)
      fprintf(stderr, "  %-12s %s\n", (*t)->name, (*t)->summary);
}

static int parse_args(int argc, char **argv)
{
   int i;
   const trigger_t **t;

   for (i = 1; i < argc; i++)
   {
      char *a = argv[i], *eq = strchr(a, '=');
      const char *val = eq ? eq + 1 : "";
      if (!strncmp(a, "--iface=", 8))            cfg.iface = val;
      else if (!strncmp(a, "--socket=", 9))      cfg.sock_path = val;
      else if (!strncmp(a, "--trigger=", 10))    cfg.trigger_name = val;
      else if (!strncmp(a, "--rate-hz=", 10))
      { if (int_arg(val, "rate-hz", 50, 2000, &cfg.rate_hz)) return 1; }
      else if (!strncmp(a, "--dc-cycle-us=", 14))
      { if (int_arg(val, "dc-cycle-us", 0, 100000, &cfg.dc_cycle_us)) return 1; }
      else if (!strncmp(a, "--dc-shift-us=", 14))
      { if (int_arg(val, "dc-shift-us", 0, 10000, &cfg.dc_shift_us)) return 1; }
      else if (!strncmp(a, "--force=", 8))
      { if (int_arg(val, "force", 0, 1000, &cfg.force)) return 1; }
      else if (!strncmp(a, "--speed=", 8))
      { if (int_arg(val, "speed", 50, 1000, &cfg.speed)) return 1; }
      else if (!strncmp(a, "--hold-ms=", 10))
      { if (int_arg(val, "hold-ms", 0, 5000, &cfg.hold_ms)) return 1; }
      else if (!strncmp(a, "--settle-ms=", 12))
      { if (int_arg(val, "settle-ms", 0, 5000, &cfg.settle_ms)) return 1; }
      else if (!strncmp(a, "--starve-ms=", 12))
      { if (int_arg(val, "starve-ms", 0, 5000, &cfg.starve_ms)) return 1; }
      else if (!strncmp(a, "--explain-al=", 13))
      {
         /* the DC run's whole answer is a hex code in a log line; being
            able to look one up afterwards costs nothing */
         unsigned al = (unsigned)strtoul(val, NULL, 0);
         printf("AL 0x%04x: %s\n", al, al_reading((uint16_t)al));
         exit(0);
      }
      else if (!strncmp(a, "--latency-log=", 14))  cfg.lat_path = val;
      else if (!strncmp(a, "--move-step=", 12))
      { if (int_arg(val, "move-step", 1, 2000, &cfg.move_step)) return 1; }
      else if (!strncmp(a, "--move-eps=", 11))
      { if (int_arg(val, "move-eps", 1, 500, &cfg.move_eps)) return 1; }
      else if (!strncmp(a, "--stuck-strikes=", 16))
      { if (int_arg(val, "stuck-strikes", 1, 100, &cfg.stuck_strikes)) return 1; }
      else if (!strncmp(a, "--on-stuck=", 11))
      {
         if (strcmp(val, "exit") && strcmp(val, "report"))
         {
            fprintf(stderr, "handd: --on-stuck must be exit or report, "
                            "not '%s'\n", val);
            return 1;
         }
         cfg.on_stuck = val;
      }
      else if (!strncmp(a, "--cpu=", 6))
      { if (int_arg(val, "cpu", 0, 63, &cfg.cpu)) return 1; }
      else if (!strncmp(a, "--rt-prio=", 10))
      { if (int_arg(val, "rt-prio", 1, 99, &cfg.rt_prio)) return 1; }
      else if (!strcmp(a, "--lock-memory"))      cfg.lock_memory = 1;
      else if (!strcmp(a, "--simulate"))         cfg.simulate = 1;
      else if (!strcmp(a, "--help") || !strcmp(a, "-h")) { usage(); exit(0); }
      else
      {
         fprintf(stderr, "handd: unknown option '%s'\n", a);
         usage();
         return 1;
      }
   }

   /* An unknown trigger is an error, never a silent fall back to the
      default: someone asking for sync0 and quietly getting disconnect
      would measure the wrong strategy and believe the number. */
   for (t = TRIGGERS; *t; t++)
      if (!strcmp((*t)->name, cfg.trigger_name)) { trig = *t; break; }
   if (!trig)
   {
      fprintf(stderr, "handd: unknown trigger '%s'\n", cfg.trigger_name);
      usage();
      return 1;
   }
   if (!cfg.iface) cfg.iface = hs_iface();
   return 0;
}

int main(int argc, char **argv)
{
   int rc, i, lock_fd = -1;
   struct sigaction sa;
   const char *env_sock = getenv("HAND_SOCKET");

   /* hand_client reads HAND_SOCKET and this did not, so exporting it moved
      the client and left the daemon on the compiled-in path - two
      processes, two sockets, and a connection refused with nothing
      obviously wrong. Same variable, same default, either can still be
      overridden by --socket. */
   if (env_sock && *env_sock) cfg.sock_path = env_sock;

   for (i = 0; i < MAX_CLIENTS; i++) clients[i].fd = -1;
   if (parse_args(argc, argv)) return 1;

   memset(&sa, 0, sizeof sa);
   sa.sa_handler = on_signal;
   sigaction(SIGINT, &sa, NULL);
   sigaction(SIGTERM, &sa, NULL);
   /* Closing the terminal should end the same way Ctrl+C does. Without
      this, SIGHUP's default action kills the process outright: the socket
      file is left behind for the next run to trip over and nothing gets
      logged about why it went. */
   sigaction(SIGHUP, &sa, NULL);
   signal(SIGPIPE, SIG_IGN);       /* a client that quits mid-reply is normal */

   if (cfg.simulate)
   {
      logf_("SIMULATE - no EtherCAT bus is opened and no hand is driven; "
            "every reply is tagged simulate:true");
      sim_reset();
   }
   else
   {
      lock_fd = hs_lock(20);
      if (lock_fd < 0)
      {
         fprintf(stderr, "handd: bus busy - another master on this host "
                         "holds the hand (20 s timeout)\n");
         return 3;
      }
   }

   logf_("trigger=%s iface=%s rate=%d Hz socket=%s",
         trig->name, cfg.simulate ? "(simulated)" : cfg.iface,
         cfg.rate_hz, cfg.sock_path);

   rc = bus_bringup();
   if (rc)
   {
      fprintf(stderr, "handd: %s\n", bringup_err(rc));
      if (!cfg.simulate)
      {
         uint16_t al = ctx.slavelist[1].ALstatuscode;
         fprintf(stderr, "  iface=%s state=0x%02x AL=0x%04x  "
                         "dc: hasdc=%d configdc=%d DCactive=%d pdelay=%d\n",
                 cfg.iface, ctx.slavelist[1].state, al,
                 dc_hasdc, dc_configured, dc_active, dc_pdelay);
         if (al) fprintf(stderr, "  AL 0x%04x: %s\n", al, al_reading(al));
      }
      if (!cfg.simulate) ecx_close(&ctx);
      hs_unlock(lock_fd);
      return 2;
   }
   if (!cfg.simulate)
      logf_("OPERATIONAL  dc: hasdc=%d configdc=%d DCactive=%d pdelay=%d",
            dc_hasdc, dc_configured, dc_active, dc_pdelay);
   wake_if_asleep();

   if (cfg.lat_path)
   {
      lat_log = fopen(cfg.lat_path, "a");
      if (!lat_log)
         logf_("WARNING: cannot write %s (%s) - running without a latency log",
               cfg.lat_path, strerror(errno));
      else
      {
         fprintf(lat_log, "id,trigger,vision_us,send_us,ipc_us,queue_us,"
                          "wire_us,exec_us,move_us,total_us,moved\n");
         fflush(lat_log);
         logf_("latency log: %s (-1 means the stage did not apply)",
               cfg.lat_path);
      }
   }

   listen_fd = socket_open(cfg.sock_path);
   if (listen_fd < 0)
   {
      bus_close();
      hs_unlock(lock_fd);
      return 4;
   }
   apply_determinism();
   logf_("ready - listening on %s", cfg.sock_path);

   run_loop();

   logf_("shutting down (the hand keeps whatever pose it was last given)");
   /* A supervisor has to be able to tell "asked to stop" from "gave up
      because the slave stopped listening", or it will restart the first
      and not the second. */
   if (stuck) logf_("exit 5: the slave was not applying");
   for (i = 0; i < MAX_CLIENTS; i++) if (clients[i].fd >= 0) close(clients[i].fd);
   close(listen_fd);
   unlink(cfg.sock_path);
   if (lat_log) fclose(lat_log);
   bus_close();
   hs_unlock(lock_fd);
   return stuck ? 5 : 0;
}
