# 2026-08-06 — the execution trigger, settled

Raw output from the run that closed the "why does this hand only move when
you disconnect" question. KD240 `.228`, hand direct into `eth1`, no switch,
single slave, middle finger (axis 2). Written up in the ExoPulse_docs vault
under `Project_Management/Inspire_RH56F1/01_Hand_Control/EtherCAT/
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

## What the set proves together

Six explanations were eliminated on hardware, not by argument: switch
topology, distributed clocks, master cadence, `ENABLE_SET`, output image
size, and sync mode. What is left is that this firmware applies its
outputs only when the sync-manager watchdog expires — 99.9 ms as shipped.

The protocol floor is therefore about **200-400 ms per pose** (one watchdog
plus one trip back to OPERATIONAL), not the 2-3 s this tree has been
paying. Continuous following at 50-100 Hz is not reachable this way.

## Reproducing

`/tmp` on the board has `fs.protected_regular=2`, so root cannot open the
bus lock file that `ubuntu` owns. Run the tools as `ubuntu`, and note that
`chown` clears file capabilities — `chown` first, then `setcap`.

    cd /home/ubuntu/ray/myohand/inspire_hand
    make probe && sudo make cap-probe
    sudo -u ubuntu ./experiments/sii_dump eth1

Hand health after the whole run, unchanged from before it:

    cur=[0,0,0,0,0,0]  err=[0,0,0,0,0,0]  sta=[2,2,1,2,2,2]  tmp=[48,48,48,48,24,46]
