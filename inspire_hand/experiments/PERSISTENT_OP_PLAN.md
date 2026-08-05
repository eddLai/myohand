# Persistent-OP probe — does the RH56F1 need a disconnect to move?

## Hypothesis under test

`hand_ctl.c` and `hand_set.c` both assume the RH56F1 only applies a
commanded pose after the EtherCAT master disconnects (comment in
`hand_set.c`: "parks the pose and exits so the SM-watchdog timeout
triggers execution"). The alternative is that the firmware applies PDO
targets immediately and the disconnect was never necessary.

This matters because if the assumption is wrong, the entire driver layer
is built around a 2-3 s per-pose penalty that doesn't need to exist.

## What was already tried, and why it isn't conclusive

This is not a fresh question — earlier experiments in this directory
already held the link open:

- `hand_op4.c`, `hand_op5.c`, `hand_op6.c` reach OPERATIONAL, write a
  full pose, cycle PDOs for 3000 ms, and print ANG/CUR/STA before and
  after.
- `hand_op3.c` goes further: it sweeps twelve candidate values of the
  enable word (`0,1,2,3,15,63,255,256,257,4096,-1,165`) plus an
  alternate PDO layout, holds each for 1500 ms while continuously
  sampling ANGLEACT, and reports the maximum deviation. Nothing crossed
  its 30-count "moved" threshold. So "scan the PDO map for a trigger
  bit", listed below as a follow-up, is partly done already.

**The gap:** every one of those predates the `STATUS=7` standby
discovery. `hand_ctl.c` documents that boot leaves all six axes in
STATUS=7 and that a pose is ignored until they are wiggled out of it —
up to 12 s across all six axes. None of `hand_op3..6` does that. Their
flat traces are therefore consistent with "the hand was asleep" and say
nothing about disconnect-gating.

Adding the wake sequence in front of the same measurement is the one
variable those runs were missing, and is the only reason to rerun this.

## Method

`ecat_persistent_probe.c`, in order:

1. **Bring-up** — same sequence as `hand_ctl.c` (init → config → SAFE_OP
   → OPERATIONAL). No-change targets (`-1`) are parked in the output
   buffer before the first frame goes out, since a zeroed buffer reads
   as "close every axis".
2. **Refuse a stalled axis** — the probe skips `hs_stall_relief`, so if
   the chosen axis reports STA 5/6 or over 400 mA it aborts rather than
   pushing into an existing stall.
3. **Wake** — `hand_ctl`'s wiggle loop, reproduced verbatim, until no
   axis reads STATUS=7 (12 s budget). Whether it succeeded is reported;
   a failed wake invalidates the rest of the run and is called out as
   such. It is copied rather than corrected on purpose — it feeds raw
   ANGLEACT (890..1850) where a target (0..2000) belongs, but that quirk
   is part of the known-good path and changing it would add a second
   variable.
4. **Oscillate** — reads the axis's ANGLEACT, converts via
   `hs_ang_to_target` to get an oscillation center, then alternates ±300
   targets around it every 500 ms while cycling PDOs at **1 kHz** (the
   cadence every working binary here uses; cycle rate is entangled with
   the watchdog under test, so it must not drift). Only the selected
   axis moves; the other five stay at `-1`.
5. **Park across the close** — before `ecx_close`, the axis is commanded
   to `center + AMP`, unambiguously away from where it started, and that
   target is carried through the disconnect. (Zeroing back to `-1` here
   would make the disconnect latch "no change", and the post-close branch
   of the experiment could never fire.)
6. **Reconnect and read** — waits 4 s after the close, then re-runs
   bring-up in the same process and reads ANGLEACT again. This is what
   makes both outcomes observable in one run, and avoids depending on
   `hand_ctl state`, which hard-codes the wrong interface (see below).

Every sample carries `target, ANGLEACT, POS, STA, CUR, ERR` — a flat
ANGLEACT alone cannot distinguish standby from a force-stop from an
error from genuine disconnect-gating.

Samples are buffered in RAM and printed only after the link is down.
Printing inside the PDO loop can stall a cycle past the SM watchdog,
which would perturb the exact mechanism being measured.

## Reading the output

The probe prints its own verdict line; the CSV below it is for
confirming that verdict, not for eyeballing raw numbers.

- **`LIVE`** (`max_dANG_open` > 30) → the axis tracked the target with
  the link open. The disconnect-to-execute assumption is false.
  `hand_ctl`/`hand_set` should become a persistent daemon holding
  OPERATIONAL and streaming cyclic PDOs — latency drops from ~2-3 s per
  pose to ~ms.
- **`DISCONNECT-GATED`** (flat while open, `dANG_postclose` > 30) → the
  firmware really does gate execution on the disconnect or the SM
  watchdog expiring. Persistent-OP won't help as-is. Next step: check for
  a PDO bit that triggers execution without a full teardown — but read
  `hand_op3.c` first, it already ruled out thirteen candidates. Failing
  that, accept the reconnect cost and optimize around it (keep a warm
  process that only redoes the connect/disconnect inner loop instead of
  spawning a `subprocess` per pose).
- **`INCONCLUSIVE`** → wake never cleared STATUS=7. The run says nothing;
  fix the wake first.
- **`NO MOTION EITHER WAY`** → neither window moved. Check the `sta`,
  `err`, and `cur` columns before concluding anything — this is the
  outcome `hand_op3..6` produced, and the reason this probe exists.

## Result — 2026-08-05, axis 2 (middle), over `eno1`

**`DISCONNECT-GATED`.** Trace: `probe_2026-08-05_eno1_axis2.csv`.

    axis=2 center=525 amp=300 duration_s=10
    wake=ok after 1ms
    ang_start=998 ang_preclose=998 max_dANG_open=0 (moved > 30)
    ang_postclose=896 dANG_postclose=102

Over 10.5 s and ~500 samples with the master holding OPERATIONAL and
cycling PDOs at 1 kHz, the commanded target alternated 225 ↔ 825 and
`ANGLEACT` read **998 on every single sample** — deviation exactly 0
counts. `CUR` was **0 mA throughout**: the actuator was never energised,
so this is not "commanded and failed to reach", it is "the target was not
acted on at all". 4.4 s after `ecx_close` the axis had moved to 896.

Two confounds are ruled out by the trace itself:

- **Not standby.** `STA` read 1-2 for the whole run and never 7. This is
  the ambiguity that makes `hand_op3..6` uninterpretable, and it is the
  reason this probe logs status per sample. (Caveat: STA=2 is simply not
  the *documented* standby value; what it does mean is unknown.)
- **Not a unit mistake.** The axis sat at ANGLEACT 998 = target 225 on
  the 0..2000 scale. Under that scale the swing to 825 commands *open*;
  under the alternative reading below it commands *closed*. Either way
  the target differed from the current position and live execution would
  have produced motion. It produced none.

So the assumption `hand_ctl` / `hand_set` were built on is **correct**:
the firmware really does gate execution on the master disconnecting.
A persistent cyclic-PDO daemon is off the table. Per the branch below,
the remaining win is to stop paying for a process spawn per pose — keep a
warm process that only repeats the connect/disconnect inner loop. Note
the probe's own reconnect reached OPERATIONAL in well under a second, and
`hand_ctl`'s 12 s wake loop cost nothing here because no axis was in
STATUS=7, so the achievable floor looks well below the current 2-3 s.

### Still open

- **Rerun on a direct link.** This ran over `eno1` through the switch.
  The stated criterion says a non-`LIVE` verdict there needs confirming
  on `enp17s0`. The switch confound pushes *against* this result rather
  than for it — jitter tripping the SM watchdog would show up as spurious
  motion or erratic behaviour, not as perfect stillness at 0 mA — but the
  rerun is still owed.
- **The target scale may not be 0..2000.** Two observations suggest the
  target field is an `ANGLEACT`-style setpoint (~890..1850), not the
  0..2000 scale the whole codebase assumes: `hand_ctl pose … 1000 …`
  settled the axis at ANGLEACT 998, and a park target of 825 — below the
  ~890 closed floor — drove it to the closed stop at 896 instead of
  anywhere near 825. If that holds, `hs_ang_to_target` is a spurious
  conversion, README's "0=closed, 2000=open" is wrong, and `hand_ctl`'s
  wake loop feeding raw ANGLEACT as targets was never a unit bug at all.
  Worth its own probe; it does not affect the result above.
- One axis, one run, one unit.

## Safety bounds

- Single axis, ±300 targets (≈ ±144 ANGLEACT counts, ~15% of travel)
  around wherever the axis already is — never a full-range jump. The
  amplitude is fixed in the source, not CLI-controlled.
- If the axis starts within one amplitude of a travel limit, the swing
  goes one-way off that limit instead of straddling it: endpoints become
  `[here, here + 2*AMP]` near closed and `[here - 2*AMP, here]` near
  open. Without this an axis resting against its stop (fingers idle at
  ANGLEACT ~896, target ~12) gets commanded to 0 on every low half-cycle,
  pressing into the stop and heating the actuator for no measurement.
- Reuses `hs_lock` (exclusive bus access) and `hs_profile` (per-axis
  force/speed limits) from the existing driver layer.
- Aborts on an axis that is already stalled, rather than relieving it.
- Axes 0-2 (pinky, ring, middle) have no interlock partner and move
  strictly alone. Axes 3-5 (index, thumb-bend, thumb-rot) do, so their
  targets go through `hs_interlock` on every flip and the full guarded
  vector is written — the guard's answer to a clash is to move a
  *different* axis clear, so writing only the probed axis would compute
  the protection and then discard it. The guard note is printed if it
  fires. Default axis is middle finger.
- Bounded run time (default 10 s, hard cap 60 s).

## Which machine, which interface

The hand is on **`ntk@120.126.83.112`**. As of 2026-08-05 it answers on
**`eno1`**, reached through the lab switch (hand on switch port 15), and
**not** on `enp17s0`. Verified with the read-only enumerator
`experiments/ecat_scan` in the shared checkout on `.112`, which finds
slaves without moving anything:

    ./experiments/ecat_scan eno1
    FOUND 1 EtherCAT slave(s) on eno1:
      slave 1  name="SSC_Device"  vendor=0x00000001 product=0x00009252
               rev=0x00020111  state=0x01
    ./experiments/ecat_scan enp17s0
    NO EtherCAT slave on enp17s0 (config_init=-1)

Do not infer the interface from source or from link state — both lie
here. `hand_ctl.c` on `.112` has been hand-edited to `enp17s0` (the
committed `enp59s0f1` is from a long-gone dev box), yet `enp17s0` has no
carrier and no slave. Run the scanner instead; it settles the question in
one second and touches nothing.

`.20` is not the hand machine. `.228`/`kd240` has no checkout.

### Why the current topology is not good enough for *this* experiment

`eno1` also carries `120.126.83.112/24` — the campus LAN and the SSH
path in — and the route to the hand crosses a managed switch. Enumeration
survives that fine. Sustained 1 kHz cyclic PDO does not: switch jitter or
a dropped frame can delay a cycle past the SM watchdog, and a watchdog
trip is *precisely the mechanism this experiment is trying to measure*.
Running over the switch can therefore manufacture the effect under test.

That gives an asymmetry worth exploiting:

- A **`LIVE`** verdict over `eno1` is still trustworthy. If the axis
  tracks the target with the link open, motion is motion — jitter cannot
  fake it, and the hypothesis is falsified then and there.
- A **`DISCONNECT-GATED`**, **`NO MOTION`**, or erratic verdict over
  `eno1` proves nothing and must be rerun on a direct link.

So a run over `eno1` is worth doing as a cheap first shot, but the
definitive run needs the hand's RJ45 patched straight into `enp17s0`:

    cat /sys/class/net/enp17s0/carrier    # 1 = direct link is up

The probe takes the interface as argv[1] rather than hard-coding it,
precisely so this ambiguity cannot silently repeat, and does its own
post-close read instead of shelling out to `hand_ctl state`, which would
fail on whatever interface that binary happens to have baked in.

## Run

    gcc -O2 -I soem_build/SOEM/include -I soem_build/build/include \
        -I soem_build/SOEM/osal -I soem_build/SOEM/osal/linux \
        -I soem_build/SOEM/oshw/linux -I . \
        experiments/ecat_persistent_probe.c hand_safety.c \
        -o experiments/ecat_persistent_probe \
        soem_build/build/libsoem.a -lpthread -lrt
    sudo setcap cap_net_raw,cap_net_admin+eip experiments/ecat_persistent_probe
    ./experiments/ecat_persistent_probe enp17s0 2 10 | tee probe.csv
