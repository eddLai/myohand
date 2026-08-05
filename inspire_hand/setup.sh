#!/usr/bin/env bash
# One-shot environment setup for the inspire_hand control stack.
# Rebuilds everything a clean clone needs: venv + pip deps, vendored SOEM
# clone + cmake build, C binaries, raw-socket caps.
# Usage: ./setup.sh          (cap step asks for sudo once)
#
# ⚠️ DO NOT RUN THIS ON THE KD240.
#
# Step 1 installs the vision dependencies, and the board has 1.9 GB of RAM
# with no swap. Any mediapipe without an aarch64 wheel makes pip build from
# source, which OOMs the board; that is what killed the 2026-08-05 attempt
# with mediapipe==0.10.21 pinned. requirements.txt now asks for a range
# that has an aarch64 wheel, but pip resolving a whole vision stack on that
# machine is still not worth the risk when the board already has a working
# venv at ~/rh56f1_kd240/ethercat/myohand/inspire_hand/venv.
#
# On the board, build the C side only:
#   export PATH="$HOME/rh56f1_kd240/ethercat/buildenv/bin:$PATH"  # cmake>=3.28
#   make all && make cap
set -euo pipefail
cd "$(dirname "$0")"

if [ -z "${SETUP_FORCE:-}" ] && [ "$(uname -m)" = "aarch64" ]; then
    echo "Refusing to run on aarch64 - see the header of this file." >&2
    echo "The C side is just: make all && make cap" >&2
    echo "Override with SETUP_FORCE=1 if you really mean it." >&2
    exit 1
fi

echo "[1/5] python venv + vision deps"
test -d venv || python3 -m venv venv
venv/bin/pip install -q -r requirements.txt

echo "[2/5] vendor clones (SOEM + mediapipe demo)"
test -d soem_build/SOEM || \
    git clone -q https://github.com/OpenEtherCATsociety/SOEM.git soem_build/SOEM
test -d hand-gesture-recognition-using-mediapipe || \
    git clone -q https://github.com/Kazuhito00/hand-gesture-recognition-using-mediapipe.git

echo "[3/5] cmake build of SOEM"
export PATH="$PWD/venv/bin:$PATH"
test -f soem_build/build/libsoem.a || {
    cmake -S soem_build/SOEM -B soem_build/build -DCMAKE_BUILD_TYPE=Release \
          -DSOEM_BUILD_SAMPLES=OFF > /dev/null
    cmake --build soem_build/build -j"$(nproc)" > /dev/null
}

echo "[4/5] C binaries (hand_ctl + hand_set)"
make all

echo "[5/5] raw-socket capabilities (sudo once)"
make cap

echo "done. try: ./hand_ctl state"
