# 2026-08-06 — the execution trigger, settled

Raw output from the run that closed the "why does this hand only move when
you disconnect" question. KD240 `.228`, hand direct into `eth1`, no switch,
single slave, middle finger (axis 2). Written up in the ExoPulse_docs vault
under `L5_HMInteraction/Inspire_RH56F1/01_Hand_Control/EtherCAT/
Execution_Trigger_Settled.md`; this directory is the evidence behind it.

Read them in this order — each one closes something.

| File | Tool | What it settles |
|---|---|---|
| `sii_dump.txt` | `sii_dump eth1` | **CoE is alive.** `mbx_proto=0x0004`, every SDO answers, `DeviceName="LAN9252_16HBI"`. Also dumps the SII categories: the RxPDO names only 6 of the 19 words it reserves |
| `ecat_interrogate.txt` | `ecat_interrogate eth1` | **`0x1C32:01 = 1`, SM-Synchron** — the slave declares it applies outputs on data arrival. Plus `min cycle = 100 us`, the watchdog registers (`2498 x 40ns x 1000 = 99.9 ms`), and the EEPROM/live SM mismatch |
| `compliant_op_coe.txt` | `compliant_op eth1 coe` | The **compliant** 18-byte CoE map is refused: `AL=0x001e`, Invalid Output Configuration. So `mbx_proto=0` is required, and the reason has nothing to do with a dead mailbox |
| `compliant_op_sii.txt` | `compliant_op eth1 sii` | The 38-byte image the drivers use reaches OP in **120 ms** with process data at 1 kHz throughout — and still moves nothing, draws nothing |
| `op_execute_hunt.txt` | `op_execute_hunt eth1 2` | **`ENABLE_SET` is not it.** 13 candidate values, a rising edge, and a write-order swap: all flat. Then the disconnect lands the axis on 1508 from a commanded 1509 |
| `watchdog_trigger.txt` | `watchdog_trigger eth1 2` | **The SM watchdog is the trigger and the link never has to drop.** Starving process data 100 ms with the socket open and OP held moved the axis 98 counts, `AL=0x0000` throughout |
| `syncmode_freerun.txt` | `syncmode_test eth1 0` | Writing `0x1C32:01 = 0` for Free Run was **accepted** (reads back 0), reached OP, held 8 s — and changed nothing. Sync mode is not it either |
| `wd_pace_baseline.txt` | `wd_pace eth1 2 0 6 100 1000` | The 100 ms window is narrow: it applies, but the next cycle drops out of OP with `AL=0x001b` |
| `wd_pace_wd20ms.txt` | `wd_pace eth1 2 20 6` | The watchdog register **is writable** and a 20 ms setting does trigger the apply (`dANG=167`) — but the slave leaves OP each time, and at 20 ms even the recovery path starves it again |
| `coe_startup.txt` | `coe_startup eth1 1000000` | `0x1C32:02` is **read-only** (`SDO abort 0x06010002`), so it is the slave's *measurement* of the SM2 interval, not a setting nobody wrote. It read 18 ms while we drove at 1 ms — and `0x1C32:12`, the cycle-exceeded counter, gained 4843 in eight seconds |
| **`rate_sweep.txt`** | `rate_sweep eth1 2 4 1,2,3,4,5,6,8` | **The answer.** 1 ms: no motion, no current, cycle-exceeded +2244. **2 ms and every rate below it: ~180 counts of travel, 56-71 mA, cycle-exceeded 0, OPERATIONAL held throughout.** No watchdog anywhere near it |

| `why_1khz_coarse.txt` | `why_1khz eth1 2 4` | **The mechanism.** SM2's status byte sampled every frame: buffers rotate evenly at every rate and state 3 never appears, so the sync manager is healthy and the fault is above it. `rdInUse` — the slave's own controller caught mid-read — is 18% of samples at 1050 us and 0% at 1700 |
| `why_1khz_boundary.txt` | `why_1khz eth1 2 4 1050,...,1900` | Both edges pinned: nothing moves below ~1.05 ms, and the cycle-exceeded counter reaches exactly zero at 1600 us |
| `rate_sweep_all.txt` | `rate_sweep eth1 -1 4 1,2,3,4,6` | **All six axes through the interlock.** Same boundary as one: 1 ms dead, 2 ms and slower fine. Current roughly doubles, as six actuators should |
| `why_1khz_all.txt` | `why_1khz eth1 -1 4 1000,...,2000` | Six axes at microsecond resolution — 1000 us fails, 1600 us is clean, identical to the single-axis run. The limit is a fixed per-cycle cost, not per-axis work |

## What the set proves together

Read in order, these files record a wrong conclusion being reached and
then overturned, so keep the order.

Six explanations were eliminated on hardware: switch topology, distributed
clocks, master cadence, `ENABLE_SET`, output image size, and sync mode.
Those eliminations all still stand. What did not stand is the inference
drawn from them — that the sync-manager watchdog must therefore be the
trigger. A seventh possibility had not been considered.

`rate_sweep` found it. **This hand cannot be driven at 1 kHz.** Its
application needs more than a millisecond per cycle, and SM-Synchron
starts a new cycle on every arriving frame, so a 1 kHz feed interrupts it
forever and outputs are never applied. At 2 ms and slower it works
perfectly, with the link up and OPERATIONAL held the whole time.

Three regimes, and they do not move when all six axes are driven at once:
below about 1.05 ms nothing travels at all, between there and 1.5 ms it
travels while the slave complains on every cycle, and at 1.6 ms and slower
the complaint counter is exactly zero. `handd` defaults to 500 Hz, which
sits inside the clean band with a quarter of it as margin.

So the cost of a pose is **2 ms, not 2-3 s**, and the only time constant
left is the mechanism's own 800 ms full-travel. The "disconnect to
execute" behaviour this tree was built around was ours, not the
firmware's — starving the link for 100 ms worked because it finally
stopped interrupting the slave, not because a timeout fired.

## Reproducing

`/tmp` on the board has `fs.protected_regular=2`, so root cannot open the
bus lock file that `ubuntu` owns. Run the tools as `ubuntu`, and note that
`chown` clears file capabilities — `chown` first, then `setcap`.

    cd /home/ubuntu/ray/myohand/inspire_hand
    make probe && sudo make cap-probe
    sudo -u ubuntu ./experiments/sii_dump eth1

Hand health after the whole run, unchanged from before it:

    cur=[0,0,0,0,0,0]  err=[0,0,0,0,0,0]  sta=[2,2,1,2,2,2]  tmp=[48,48,48,48,24,46]
