#!/bin/sh
# dc_ready - pre-flight for the DC persistent-OP experiment.
#
# Read-only throughout: it checks the link, asks the bus what is on it,
# and prints the commands to run. It never drives the hand. Running the
# probe stays a deliberate manual step because it moves an axis.
#
# The one thing this cannot read from a register is whether the hand is
# wired directly or still sitting behind the lab switch - and that is the
# whole point of the experiment, since DC cannot work through a switch.
# So it infers it from background traffic: a direct link carries nothing
# but EtherCAT, while a switch port is flooded with LAN broadcast.
#
# Usage: ./experiments/dc_ready.sh [iface]     (default: $ECAT_IFACE, else eth1)

set -u
IFACE=${1:-${ECAT_IFACE:-eth1}}
HERE=$(dirname "$0")
fail=0

say() { printf '%-34s %s\n' "$1" "$2"; }

echo "=== link: $IFACE ==="
if [ ! -e "/sys/class/net/$IFACE" ]; then
    say "interface" "MISSING - is kd240-bist still the active bitstream?"
    echo
    echo "  eth1/eth2 exist only while a PL design with an AXI Ethernet"
    echo "  core is loaded. Check with: xmutil listapps"
    exit 1
fi

carrier=$(cat "/sys/class/net/$IFACE/carrier" 2>/dev/null || echo "?")
oper=$(cat "/sys/class/net/$IFACE/operstate" 2>/dev/null || echo "?")
say "carrier" "$carrier  (operstate=$oper)"
if [ "$carrier" != "1" ]; then
    say "" "NO LINK - check the cable, or try the other J25 jack"
    fail=1
fi
ethtool "$IFACE" 2>/dev/null | grep -E "Speed|Duplex" | sed 's/^[ \t]*/  /'

echo
echo "=== topology guess ==="
# A direct EtherCAT link is silent until a master starts. A switch port
# sees the LAN's broadcast and multicast within a couple of seconds.
r1=$(cat "/sys/class/net/$IFACE/statistics/rx_packets")
sleep 3
r2=$(cat "/sys/class/net/$IFACE/statistics/rx_packets")
delta=$((r2 - r1))
say "background rx in 3 s" "$delta packets"
if [ "$delta" -gt 20 ]; then
    say "" "looks like a SWITCH port - DC will fail here (AL=0x002d)"
    say "" "move the hand's RJ45 to a direct link before the DC run"
    fail=1
else
    say "" "quiet - consistent with a direct link"
fi

echo
echo "=== bus ==="
"$HERE/ecat_scan" "$IFACE" || fail=1

echo
echo "=== other masters ==="
echo "  The flock bus lock in hand_safety.c is per-host. It cannot see a"
echo "  master on .28 / .112 / .20 reaching the same slave through the"
echo "  switch, and two masters make the slave refuse OPERATIONAL."
echo "  Confirm nobody else is driving the hand before you start."

echo
if [ "$fail" -ne 0 ]; then
    echo "NOT READY - fix the above first."
    exit 1
fi

cat <<EOF
READY. Run the control first, then the decisive one:

  cd $(cd "$HERE/.." && pwd)
  ./experiments/ecat_persistent_probe $IFACE 2 10        # no DC, control
  ./experiments/ecat_persistent_probe $IFACE 2 10 1000   # Sync0 1 ms

Read the verdict line the probe prints itself. DISCONNECT-GATED on the
DC run means the firmware really does gate on disconnect; anything that
moves during the open window means it does not, and the driver should
become a resident daemon. INCONCLUSIVE means the wake never cleared
STATUS=7 and that run does not count.

The probe parks the axis away from its start position on purpose, so the
hand will not return by itself. Check the last CSV row: STA 5/6 or a high
current means an axis is pushing against something - open it.
EOF
