#!/usr/bin/env bash
# Install the handd systemd unit, filled in for wherever this checkout is.
#
# Installs and verifies by default; it does NOT enable or start anything.
# Starting handd opens the EtherCAT bus and takes the hand, and that is
# not a thing a setup script should do behind someone's back - especially
# on a bus that four machines can reach.
#
#   sudo ./systemd/install.sh              # install the unit, leave it stopped
#   sudo ./systemd/install.sh --enable     # also enable it at boot
#
# Afterwards:
#   sudo systemctl start handd && journalctl -u handd -f
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
INSTALL_DIR=$(cd "$HERE/.." && pwd)
UNIT=/etc/systemd/system/handd.service
DEFAULTS=/etc/default/handd

# Run the daemon as whoever owns the checkout, not as root: handd carries
# its capabilities on the inode and does not need more than that.
RUN_USER=$(stat -c '%U' "$INSTALL_DIR/handd" 2>/dev/null || stat -c '%U' "$INSTALL_DIR")

if [ ! -x "$INSTALL_DIR/handd" ]; then
    echo "handd is not built in $INSTALL_DIR - run 'make all && make cap' first" >&2
    exit 1
fi

sed -e "s|@INSTALL_DIR@|$INSTALL_DIR|g" -e "s|@USER@|$RUN_USER|g" \
    "$HERE/handd.service.in" > "$UNIT"
echo "wrote $UNIT  (ExecStart=$INSTALL_DIR/handd, User=$RUN_USER)"

if [ -e "$DEFAULTS" ]; then
    echo "kept existing $DEFAULTS - edit it if the interface changed"
else
    install -m 0644 "$HERE/handd.default" "$DEFAULTS"
    echo "wrote $DEFAULTS  - check the interface name in it before starting"
fi

systemd-analyze verify "$UNIT"
systemctl daemon-reload

if [ "${1:-}" = "--enable" ]; then
    systemctl enable handd
    echo "enabled at boot (still not started)"
else
    echo "not enabled. To start it at boot: sudo systemctl enable handd"
fi

cat <<EOF

Nothing has been started. Before you do:
  - confirm the interface in $DEFAULTS matches where the hand answers
    (./experiments/ecat_scan eth1)
  - confirm no other machine is holding the bus; the flock in
    hand_safety.c is per-host and cannot see .28 / .112 / .20
EOF
