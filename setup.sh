#!/usr/bin/env bash
# One-shot environment setup for the myohand stack.
# Rebuilds everything a clean clone needs: root venv + pinned pip deps,
# vendored SOEM clone + cmake build, C binaries, raw-socket caps.
# Usage: ./setup.sh          (cap step asks for sudo once)
#
# ⚠️ DO NOT RUN THIS ON THE KD240.
#
# Step 1 installs the vision dependencies, and the board has 1.9 GB of RAM
# with no swap. Any mediapipe without an aarch64 wheel makes pip build from
# source, which OOMs the board; that is what killed the 2026-08-05 attempt
# with mediapipe==0.10.21 pinned. requirements.txt now asks for a version
# that has an aarch64 wheel, but pip resolving a whole vision stack on that
# machine is still not worth the risk when the board already has a working
# venv at ~/rh56f1_kd240/ethercat/myohand/venv.
#
# On the board, build the C side only:
#   export PATH="$HOME/rh56f1_kd240/ethercat/buildenv/bin:$PATH"  # cmake>=3.28
#   make -C hand_fw all && make -C hand_fw cap
set -euo pipefail
cd "$(dirname "$0")"

if [ -z "${SETUP_FORCE:-}" ] && [ "$(uname -m)" = "aarch64" ]; then
    echo "Refusing to run on aarch64 - see the header of this file." >&2
    echo "The C side is just: make -C hand_fw all && make -C hand_fw cap" >&2
    echo "Override with SETUP_FORCE=1 if you really mean it." >&2
    exit 1
fi

echo "[1/4] python venv + pinned deps"
# mediapipe 0.10.21 is the last release with the legacy solutions API the
# camera/teleop code uses, and it ships no wheels for python >= 3.13
PY="$(command -v python3.10 || command -v python3.11 || command -v python3.12 || command -v python3)"
test -d venv || "$PY" -m venv venv
venv/bin/pip install -q -r requirements.txt

echo "[2/4] SOEM clone + cmake build"
test -d hand_fw/soem_build/SOEM || \
    git clone -q https://github.com/OpenEtherCATsociety/SOEM.git hand_fw/soem_build/SOEM
export PATH="$PWD/venv/bin:$PATH"
test -f hand_fw/soem_build/build/libsoem.a || {
    cmake -S hand_fw/soem_build/SOEM -B hand_fw/soem_build/build \
          -DCMAKE_BUILD_TYPE=Release -DSOEM_BUILD_SAMPLES=OFF > /dev/null
    cmake --build hand_fw/soem_build/build -j"$(nproc)" > /dev/null
}

echo "[3/4] C binaries (hand_ctl + hand_set)"
make -C hand_fw all

echo "[4/4] raw-socket capabilities (sudo once)"
make -C hand_fw cap

echo "done. try: ./hand_fw/hand_ctl state"
