# myohand

EMG-driven control of an Inspire RH56F1 dexterous hand. The repo is
split by function; each folder carries its own README where there is
more to say.

| Folder | Role | Status |
|---|---|---|
| `camera/` | MediaPipe hand tracking → joint-angle decomposition (`hand_mapping.py`), calibration, mapping tests | working |
| `teleop/` | Webcam teleop app: instrument-panel UI, SYNC/CALIBRATE/HAND controls | working |
| `hand_fw/` | RH56F1 EtherCAT control stack: C core (SOEM), safety layer, Python API + HTTP server. Main technical doc lives here | working |
| `nn/` | EMG→pose network (labels from `camera/hand_mapping.thumb_features`) | planned — see its README |
| `pid/` | Closed-loop joint control on ANGLEACT feedback | planned — see its README |
| repo root | Myo-armband EMG collection (`data_recording.py`, `hardware_check.py`, `data/`, `libemg` submodule) | working, untouched by the restructure |

## Quickstart

    ./setup.sh                 # root venv + SOEM clone/cmake + C binaries + caps (sudo once)
    ./hand_fw/hand_ctl state   # hand telemetry, no motion
    ./teleop/run_teleop.sh     # webcam teleop window

Python entry points run on the root venv, e.g.
`venv/bin/python3 hand_fw/hand_api.py open`, and the camera scripts run
from their folder: `cd camera && ../venv/bin/python3 calibrate.py`.
