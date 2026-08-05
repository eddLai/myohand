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
 * WHAT MAKES THE HAND EXECUTE is still an open question, so it is a
 * swappable strategy rather than a baked-in assumption:
 *
 *   disconnect  (default)  write the target, hold briefly, drop the link
 *                          so the SM watchdog fires, reconnect. This is
 *                          the only path ever observed to drive this hand,
 *                          so it stays the default until something else is
 *                          measured to work. Do not delete it.
 *   sync0                  arm distributed clocks and let the slave copy
 *                          its own PDO buffer on the Sync0 interrupt. Only
 *                          possible on a direct link - DC cannot traverse
 *                          an ethernet switch - and unproven on this hand.
 *
 * Both strategies share everything else: the loop, the socket, the guard,
 * the telemetry, the timing. Swapping them is one function, by design.
 *
 * Usage:
 *   handd [--iface=NAME] [--trigger=disconnect|sync0] [--socket=PATH]
 *         [--rate-hz=N] [--dc-cycle-us=N] [--force=N] [--speed=N]
 *         [--hold-ms=N] [--settle-ms=N] [--simulate]
 *
 * Protocol: one text command per line on the unix socket, one JSON line
 * back. `hello` describes the daemon, `scale` reports what the target
 * numbers mean, `target P R M I TB TR` commands a pose, `state` reads
 * telemetry, `bye` disconnects. See hand_client.py for the client side.
 *
 * Exits nonzero with a readable reason if the bus is not there. It never
 * moves the hand on shutdown: an unattended park would be a surprise
 * movement, and the caller that owns the pose should be the one to change
 * it.
 */
#include "soem/soem.h"
#include "hand_safety.h"

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
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
   int force, speed;
   int hold_ms;          /* disconnect: how long the target rides before close */
   int settle_ms;        /* disconnect: how long to stay down */
   int simulate;
} cfg = { NULL, SOCKET_DEFAULT, "disconnect", 1000, 0, 500, 1000, 120, 300, 0 };

/* ---- bus ------------------------------------------------------------ */

static ecx_contextt ctx;
static uint8 IOmap[4096];
static int16_t *in, *out;      /* point at the PDO buffers, or at the sim */
static int bus_up;

static int16_t sim_in[64], sim_out[64];
static int32_t sim_ang_milli[6];   /* sub-count position, so slow moves move */
static int sim_awake_frames;

static int dc_hasdc, dc_configured, dc_active;
static int32_t dc_pdelay;

static volatile sig_atomic_t running = 1;

static void on_signal(int s) { (void)s; running = 0; }

static long now_ms(void)
{
   struct timespec ts;
   clock_gettime(CLOCK_MONOTONIC, &ts);
   return ts.tv_sec * 1000L + ts.tv_nsec / 1000000L;
}

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
} trigger_t;

static const trigger_t *trig;

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
static void apply_target(const int16_t *tgt, char *why, size_t n, int *guarded);

static void disc_cycle(void)
{
   long t = now_ms();
   if (ds_state == DS_HOLD && t >= ds_at)
   {
      /* the disconnect IS the execution command on this firmware */
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
   disc_arm, disc_on_target, disc_cycle
};

/* -- sync0: distributed clocks, unproven on this hand ----------------- */

static int sync0_arm(void)
{
   uint32_t period_ns;

   if (cfg.simulate) return 0;
   dc_hasdc = ctx.slavelist[1].hasdc;
   if (!dc_hasdc) return 5;
   dc_configured = ecx_configdc(&ctx) ? 1 : 0;
   period_ns = (uint32_t)(cfg.dc_cycle_us ? cfg.dc_cycle_us
                                          : 1000000 / cfg.rate_hz) * 1000u;
   /* armed before the OP request so the slave is already producing the
      interrupt by the time it starts consuming process data */
   ecx_dcsync0(&ctx, 1, TRUE, period_ns, 0);
   dc_active = ctx.slavelist[1].DCactive;
   dc_pdelay = ctx.slavelist[1].pdelay;
   return 0;
}

static void sync0_on_target(void) { /* the loop already writes it out */ }
static void sync0_cycle(void)     { /* Sync0 does the rest */ }

static const trigger_t TRIG_SYNC0 = {
   "sync0",
   "arm distributed clocks; the slave applies its own buffer on Sync0",
   sync0_arm, sync0_on_target, sync0_cycle
};

static const trigger_t *TRIGGERS[] = { &TRIG_DISCONNECT, &TRIG_SYNC0, NULL };

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

static int bus_bringup(void)
{
   int chk, i, rc;

   if (cfg.simulate)
   {
      in = sim_in;
      out = sim_out;
      bus_up = 1;
      out[0] = 1;
      for (i = 0; i < 6; i++) out[HS_OUT_TARGET + i] = HS_TGT_HOLD;
      hs_profile(out, cfg.force, cfg.speed);
      return 0;
   }

   if (!ecx_init(&ctx, cfg.iface)) return 1;
   if (ecx_config_init(&ctx) <= 0) return 2;
   ctx.slavelist[1].mbx_proto = 0;   /* dead CoE mailbox on this SSC build */
   ecx_config_map_group(&ctx, IOmap, 0);
   if (ctx.slavelist[1].Ibytes < 36 * 2 || ctx.slavelist[1].Obytes < 19 * 2)
      return 4;

   out = (int16_t *)ctx.slavelist[1].outputs;
   in  = (int16_t *)ctx.slavelist[1].inputs;
   /* a zeroed output buffer reads as "close every axis"; park holds before
      the first frame can carry that pattern onto the wire */
   memset(out, 0, ctx.slavelist[1].Obytes);
   for (i = 0; i < 6; i++) out[HS_OUT_TARGET + i] = HS_TGT_HOLD;

   rc = trig->arm();
   if (rc) return rc;

   ecx_statecheck(&ctx, 0, EC_STATE_SAFE_OP, EC_TIMEOUTSTATE * 4);
   ctx.slavelist[0].state = EC_STATE_OPERATIONAL;
   pd();
   ecx_writestate(&ctx, 0);
   chk = 200;
   do { pd(); ecx_statecheck(&ctx, 0, EC_STATE_OPERATIONAL, 50000); }
   while (chk-- && (ctx.slavelist[0].state != EC_STATE_OPERATIONAL));
   ecx_readstate(&ctx);
   if (ctx.slavelist[1].state != EC_STATE_OPERATIONAL) return 3;

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
   for (i = 0; i < 6; i++) out[HS_OUT_TARGET + i] = tgt[i];
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
static uint32_t seq;

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

static void reply_state(client_t *c)
{
   char pos[64], ang[64], frc[64], cur[64], err[64], sta[64], tmp[64];
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
   creply(c, "{\"ok\":true,\"bus\":\"up\",\"simulate\":%s,\"pos\":%s,\"ang\":%s,"
             "\"frc\":%s,\"cur\":%s,\"err\":%s,\"sta\":%s,\"tmp\":%s}",
          cfg.simulate ? "true" : "false", pos, ang, frc, cur, err, sta, tmp);
}

static void handle_line(client_t *c, char *line)
{
   char *cmd = strtok(line, " \t");
   if (!cmd) return;

   if (!strcmp(cmd, "hello"))
   {
      char js[160];
      hs_scale_json(js, sizeof js);
      creply(c, "{\"ok\":true,\"daemon\":\"handd\",\"trigger\":\"%s\","
                "\"rate_hz\":%d,\"force\":%d,\"speed\":%d,\"simulate\":%s,"
                "\"scale\":%s}",
             trig->name, cfg.rate_hz, cfg.force, cfg.speed,
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
   else if (!strcmp(cmd, "target"))
   {
      int16_t tgt[6];
      char why[256] = {0};
      int i, guarded = 0;
      for (i = 0; i < 6; i++)
      {
         char *tok = strtok(NULL, " \t");
         long v;
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
      if (strtok(NULL, " \t"))
      {
         creply(c, "{\"ok\":false,\"error\":\"target takes exactly 6 values\"}");
         return;
      }
      apply_target(tgt, why, sizeof why, &guarded);
      creply(c, "{\"ok\":true,\"seq\":%u,\"guarded\":%d,\"guard_note\":\"%s\","
                "\"queued\":%s}",
             ++seq, guarded, why, bus_up ? "false" : "true");
   }
   else if (!strcmp(cmd, "bye"))
   {
      creply(c, "{\"ok\":true,\"bye\":true}");
      close(c->fd);
      c->fd = -1;
   }
   else
      creply(c, "{\"ok\":false,\"error\":\"unknown command '%s' - try hello, "
                "scale, state, target, bye\"}", cmd);
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

/* ---- main loop ------------------------------------------------------ */

static void run_loop(void)
{
   struct timespec next;
   long period_ns = 1000000000L / cfg.rate_hz;

   clock_gettime(CLOCK_MONOTONIC, &next);
   while (running)
   {
      next.tv_nsec += period_ns;
      while (next.tv_nsec >= 1000000000L) { next.tv_nsec -= 1000000000L; next.tv_sec++; }
      clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next, NULL);

      serve_clients();
      if (bus_up) pd();
      else if (cfg.simulate) sim_step();   /* the firmware under test moves
                                              during the disconnect, so the
                                              stand-in has to as well */
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
      "  --trigger=NAME     what makes the hand execute (default disconnect)\n"
      "  --socket=PATH      unix socket clients connect to (default %s)\n"
      "  --rate-hz=N        PDO cycle rate, 50..2000 (default 1000)\n"
      "  --dc-cycle-us=N    sync0 period; 0 follows the PDO rate\n"
      "  --force=N          0..1000 (default 500)\n"
      "  --speed=N          50..1000 (default 1000)\n"
      "  --hold-ms=N        disconnect: target rides this long before the drop\n"
      "  --settle-ms=N      disconnect: how long the link stays down\n"
      "  --simulate         no bus; a stand-in slave for testing clients\n"
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
      else if (!strncmp(a, "--force=", 8))
      { if (int_arg(val, "force", 0, 1000, &cfg.force)) return 1; }
      else if (!strncmp(a, "--speed=", 8))
      { if (int_arg(val, "speed", 50, 1000, &cfg.speed)) return 1; }
      else if (!strncmp(a, "--hold-ms=", 10))
      { if (int_arg(val, "hold-ms", 0, 5000, &cfg.hold_ms)) return 1; }
      else if (!strncmp(a, "--settle-ms=", 12))
      { if (int_arg(val, "settle-ms", 0, 5000, &cfg.settle_ms)) return 1; }
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

   for (i = 0; i < MAX_CLIENTS; i++) clients[i].fd = -1;
   if (parse_args(argc, argv)) return 1;

   memset(&sa, 0, sizeof sa);
   sa.sa_handler = on_signal;
   sigaction(SIGINT, &sa, NULL);
   sigaction(SIGTERM, &sa, NULL);
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
         fprintf(stderr, "  iface=%s state=0x%02x AL=0x%04x  "
                         "dc: hasdc=%d configdc=%d DCactive=%d pdelay=%d\n",
                 cfg.iface, ctx.slavelist[1].state,
                 ctx.slavelist[1].ALstatuscode,
                 dc_hasdc, dc_configured, dc_active, dc_pdelay);
      if (!cfg.simulate) ecx_close(&ctx);
      hs_unlock(lock_fd);
      return 2;
   }
   if (!cfg.simulate)
      logf_("OPERATIONAL  dc: hasdc=%d configdc=%d DCactive=%d pdelay=%d",
            dc_hasdc, dc_configured, dc_active, dc_pdelay);
   wake_if_asleep();

   listen_fd = socket_open(cfg.sock_path);
   if (listen_fd < 0)
   {
      bus_close();
      hs_unlock(lock_fd);
      return 4;
   }
   logf_("ready - listening on %s", cfg.sock_path);

   run_loop();

   logf_("shutting down (the hand keeps whatever pose it was last given)");
   for (i = 0; i < MAX_CLIENTS; i++) if (clients[i].fd >= 0) close(clients[i].fd);
   close(listen_fd);
   unlink(cfg.sock_path);
   bus_close();
   hs_unlock(lock_fd);
   return 0;
}
