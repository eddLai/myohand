# Persistent-OP probe — does the RH56F1 need a disconnect to move?

## Hypothesis under test

`hand_ctl.c` and `hand_set.c` both assume the RH56F1 only applies a
commanded pose after the EtherCAT master disconnects (comment in
`hand_set.c`: "parks the pose and exits so the SM-watchdog timeout
triggers execution"). That assumption has never been tested against the
alternative: that the firmware applies PDO targets immediately, and
disconnecting was never necessary — `hand_ctl`/`hand_set` just never
tried holding the link open.

This matters because if the assumption is wrong, the entire driver layer
is built around a 2-3 s per-pose penalty that doesn't need to exist.

## Method

`ecat_persistent_probe.c`: same bring-up sequence as `hand_ctl.c` (init →
config → SAFE_OP → OPERATIONAL), but instead of writing one pose and
exiting, it holds the link open and:

1. Reads the current `ANGLEACT` for one axis, converts it to a target via
   `hs_ang_to_target`, and uses that as the oscillation center (so the
   probe never commands a large jump from wherever the axis already is).
2. Every ~500 ms, alternates the target ±100 units around that center,
   and keeps cycling `ecx_send_processdata` / `ecx_receive_processdata`
   at ~1 kHz throughout — the link is never closed during this phase.
3. Logs `t_ms, commanded_target, ANGLEACT` on every cycle.
4. After the oscillation window, closes the link and logs `ANGLEACT` one
   more time immediately post-`ecx_close`.

Only one axis moves per run (selected on the command line); the other
five are held at `-1` (no-change). Default axis is middle finger — it
has no interlock partner, so a bug can't wedge it against another axis.

## Reading the log

- **`ANGLEACT` tracks the oscillating target while the link is still
  open** → the disconnect-to-execute assumption is false. PDO writes
  take effect live. `hand_ctl`/`hand_set` should be rewritten as a
  persistent daemon holding OPERATIONAL and streaming cyclic PDOs —
  latency drops from ~2-3 s/pose to ~ms.
- **`ANGLEACT` stays flat for the whole open-link window, then jumps
  only in the read taken right after `ecx_close`** → the firmware really
  does gate execution on disconnect (or on the SM watchdog expiring).
  Persistent-OP won't help as-is; next step is checking whether there's
  a PDO bit that triggers execution without a full teardown (worth
  scanning the object dictionary / PDO map — see `ecat_pdomap.py`,
  `ecat_smcat.py` in this directory), or accepting the reconnect-per-pose
  cost and optimizing everything else (skip the `subprocess` spawn per
  pose, keep a warm process that only redoes the connect/disconnect
  inner loop).

## Safety bounds

- Single axis, ±100 units around wherever it already is — never a
  full-range jump.
- Reuses `hs_lock` (exclusive bus access) and `hs_profile` (per-axis
  force/speed limits) from the existing driver layer.
- Skips `hs_interlock`/`hs_stall_relief` — not needed for a single
  isolated axis wiggle, and default axis has no interlock partner.
- Bounded run time (default 10 s), hard-coded oscillation amplitude, no
  CLI-controlled target values.

## Before running

`hand_ctl.c` / `hand_set.c` both hard-code `IFACE "enp59s0f1"`, which
does not exist on `ntk@120.126.83.112` — the live interface there is
`eno1` (confirmed via `ip -br link show`; it's the only one showing
`LOWER_UP`). This probe takes the interface as argv[1] instead of
hard-coding it, specifically so this mismatch can't silently repeat.
Confirm the hand's RJ45 is actually the cable plugged into whichever
interface you pass before trusting a "no EtherCAT slave" result as
meaningful.

## Run

    gcc -O2 -I soem_build/SOEM/include -I soem_build/build/include \
        -I soem_build/SOEM/osal -I soem_build/SOEM/osal/linux \
        -I soem_build/SOEM/oshw/linux -I . \
        experiments/ecat_persistent_probe.c hand_safety.c \
        -o experiments/ecat_persistent_probe \
        soem_build/build/libsoem.a -lpthread -lrt
    sudo setcap cap_net_raw,cap_net_admin+eip experiments/ecat_persistent_probe
    ./experiments/ecat_persistent_probe eno1 2 10   # axis 2 = middle, 10 s
