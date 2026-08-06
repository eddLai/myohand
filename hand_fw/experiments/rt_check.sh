#!/bin/sh
# rt_check - what stands between the PDO loop and a steady 1 kHz.
#
# Read-only. It reports and recommends; it changes nothing, because the
# one measure worth the most - isolating a core - is a boot-time change on
# this board and a bad reboot costs more than the jitter does (see the
# warning at the end).
#
# Usage: ./experiments/rt_check.sh

set -u
say() { printf '%-32s %s\n' "$1" "$2"; }
note() { printf '%-32s %s\n' "" "$1"; }

echo "=== kernel ==="
say "release" "$(uname -r)"
if uname -v | grep -q PREEMPT_RT; then
    say "preemption" "PREEMPT_RT - the good case"
else
    say "preemption" "not PREEMPT_RT - cheap measures only, as planned"
fi

echo
echo "=== cores ==="
say "online" "$(nproc) CPUs"
cmdline=$(cat /proc/cmdline)
case "$cmdline" in
    *isolcpus=*)
        say "isolcpus" "$(echo "$cmdline" | tr ' ' '\n' | grep isolcpus=)"
        note "run handd with --cpu= set to an isolated core"
        ;;
    *)
        say "isolcpus" "NOT SET - every core also carries general work"
        note "handd --cpu=3 --rt-prio=80 still helps: it stops the loop"
        note "migrating and puts it ahead of other work on that core."
        note "Isolating core 3 outright needs isolcpus=3 nohz_full=3"
        note "in the kernel command line - see the warning below."
        ;;
esac

echo
echo "=== frequency ==="
for c in /sys/devices/system/cpu/cpu*/cpufreq; do
    [ -d "$c" ] || continue
    cpu=$(basename "$(dirname "$c")")
    say "$cpu" "$(cat "$c/scaling_governor") @ $(cat "$c/scaling_cur_freq") kHz"
done 2>/dev/null
gov=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || echo "?")
if [ "$gov" = "userspace" ] || [ "$gov" = "performance" ]; then
    note "fixed clock - nothing to do"
else
    note "a scaling governor adds latency on wake-up; prefer performance"
fi

echo
echo "=== realtime scheduling ==="
rt=$(cat /proc/sys/kernel/sched_rt_runtime_us 2>/dev/null || echo "?")
say "sched_rt_runtime_us" "$rt of $(cat /proc/sys/kernel/sched_rt_period_us 2>/dev/null)"
if [ "$rt" != "-1" ]; then
    note "RT tasks are throttled to that share. A 1 kHz loop that does"
    note "not spin is nowhere near the cap, so this is fine as it is."
fi
# not `ulimit -r`: dash, which is /bin/sh here, does not have that option
rtprio=$(awk '/Max realtime priority/ {print $4}' /proc/self/limits)
say "max realtime priority" "${rtprio:-unknown}"
if [ "${rtprio:-0}" = "0" ]; then
    note "unprivileged SCHED_FIFO is refused by rlimit, so handd needs"
    note "cap_sys_nice - which is what 'make cap' now grants it."
fi

echo
echo "=== the handd side ==="
cat <<'EOF'
  ./handd --iface=eth1 --cpu=3 --rt-prio=80 --lock-memory
  Each of those three says in its own log line whether it took effect.
  If SCHED_FIFO or mlockall is refused, run `make cap` and try again -
  do not read jitter numbers from a run that printed a WARNING.
EOF

echo
echo "=== before you reboot to add isolcpus ==="
cat <<'EOF'
  This board boots a FIT image (/boot/firmware/image.fit) with a
  boot.scr.uimg, not extlinux, so the kernel command line comes from the
  boot script rather than a file you can edit in place.

  More importantly: eth1 exists only while a PL design containing an AXI
  Ethernet core is loaded, and today that is AMD's kd240-bist. Confirm
  with `xmutil listapps` that it comes back by itself after a reboot
  BEFORE you reboot for the sake of isolcpus - otherwise the hand's only
  dedicated network port disappears and the reboot costs far more than
  the jitter it was meant to remove.
EOF
