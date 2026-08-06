#!/usr/bin/env bash
# One-shot environment setup for the myohand stack.
# Rebuilds everything a clean clone needs: root venv + pinned pip deps,
# vendored SOEM clone + cmake build, C binaries, raw-socket caps.
# Usage: ./setup.sh          (cap step asks for sudo once)
set -euo pipefail
cd "$(dirname "$0")"

echo "[1/4] python venv + pinned deps"
test -d venv || python3 -m venv venv
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
