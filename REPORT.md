# RUDP Project Progress Report

**Report date:** 2026-11-14  
**Repository:** `GhufranMianoor/RUDP`

## Summary

The `ahmed_week1` branch was merged into `main` and pushed to `origin/main`.
The merge completed without conflicts.

## Work Completed

The Week 1 Member 2 implementation was integrate

- Added RTT sampling support in `rudp/rtt.py`.
  - Tracks sent and acknowledged packets.
  - Records RTT statistics.
  - Applies Karn's algorithm so retransmitted packets do not produce ambiguous RTT samples.
- Added the congestion-controller interface and implementations in `rudp/controller.py`.
  - Defines controller protocols and congestion states.
  - Adds `NullController`.
  - Adds a TCP Reno-style controller with slow start, congestion avoidance, duplicate-ACK handling, loss handling, and timeout handling.
- Connected the new controller and RTT functionality to the existing sender, connection, hooks, and package exports.
- Updated `apps/send_file.py` to use the new functionality.
- Added the design documentation in `docs/congestion_controller_design.md`.
- Added focused tests for RTT sampling, congestion control, and sender integration.

## Integration Details

- Source branch: `ahmed_week1`
- Target branch: `main`
- Merge commit: `c61014e`
- Remote status: `main` is synchronized with `origin/main`
- Files changed by the merge: 11
- Net change: 1,276 additions and 41 deletions

## Verification

The complete test suite was executed after the merge:

```text
26 passed in 0.10s
```

The working tree was clean after the push.

## Current Repository State

`main` contains the original RUDP implementation plus the Week 1 RTT sampling
and congestion-control work. The branch is ready for subsequent development
and future Week 1 or Week 2 enhancements.
