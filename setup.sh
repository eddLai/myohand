#!/usr/bin/env bash
# One-shot environment setup for the inspire_hand control stack.
# Rebuilds everything a clean clone needs: venv + pinned pip deps,
# vendored SOEM clone + cmake build, C binaries, raw-socket caps.
# Usage: ./setup.sh          (cap step asks for sudo once)
set -euo pipefail
cd "$(dirname "$0")"

echo "[1/5] python venv + pinned deps"
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
