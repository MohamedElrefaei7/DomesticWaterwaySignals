# CONTEXT.md — running log

This is the **log**: current state, decisions as they are made, and `§ Up Next`. Stable contracts
live in `CLAUDE.md`. If something here hardens into an invariant, move it there and note the move.

**Last updated:** 2026-08-09

---

## Current state

Phase 0. Repo initialized. No infrastructure, no application code, no data.

- `CLAUDE.md` seeded with the contracts carried forward from the prior project (`Trade_Analysis_Project`).
- `.gitignore` committed, verified not self-excluding.
- AWS budget alert: **not yet configured** — must exist before anything is provisioned.

---

## The thesis (one paragraph, so it stays in view)

River stage on the Mississippi system physically constrains how much grain a barge can carry, and
that constraint propagates into published barge freight rates within days. Water falls → draft
restrictions → light-loading and shorter tows → effective capacity drops while harvest volume does
not → rates rise → Gulf basis widens. Every arrow is mechanical, so a *broken* relationship is
informative rather than noise. Fast feature: 15-minute USGS gauge stage. Slow target: USDA's weekly
barge freight rate as percent of tariff. The 2022 and 2023 low-water events are labelled natural
experiments to validate against.

**Résumé framing that governs tradeoffs:** a real-time inland-waterway signal system on a single
corridor, where a fast-moving physical constraint leads a slow-moving published index, with honest
confidence gating that says "insufficient history" rather than manufacturing conviction.

---

## Key decisions

- **Data sources selected for cleanliness, not richness.** The prior project died on raw AIS: five
  distinct corruption mechanisms in arrival detection, 34.8% of stored arrivals with zero supporting
  position pings in the preceding six hours. Selection criterion here is data that arrives already
  clean and structured, from a publisher with an institutional obligation to keep publishing.
- **Full historical backfill on day one.** USGS from 2007-10-01, USDA Socrata with history. The prior
  project's later phases were gated on data accumulation; that gate does not exist here.
- **USACE LPMS routed around** — weekly lock movements come from USDA Table 10 instead. See
  `CLAUDE.md § 6`.
- **CBOT futures / Gulf basis is an extension, not the core.** Barge rate as percent of tariff is a
  legitimate target on its own.

---

## Open questions

- **Raw 15-minute gauge readings vs. hourly aggregates on ingest.** Must be decided before Phase 3.
  Size estimate: ~96 readings/day × ~6,880 days × ~15 sites × 2 params ≈ **20M rows** for the full
  raw backfill — roughly half the prior project's 38.5M, and this row is narrower. Hourly would be
  ~5M. Volume is not the deciding factor at this scale.
- **Whether USGS instantaneous-values requests can span the full period of record in one call.**
  Verify empirically in Phase 3 and chunk the backfill by date window if not. The plan currently
  assumes a single request; that assumption has not been tested.

---

## § Up Next

**Phase 1 — Terraform.** EC2 instance, separate data volume with `prevent_destroy`, security group
with no default-open ingress, EIP, SSM role. Plus tests that parse the HCL and go **red** on: a
widened ingress CIDR, any Postgres ingress rule, and the data volume being consolidated into the root
volume. Touches no application code.

Then, in order: Phase 2 orchestration skeleton (migration runner, `job_runs`, `@job`, APScheduler with
persistent store, cadence table, heartbeat) **before the first ingest client**, so the first data that
ever lands is already observed. Phase 3 USGS ingest and backfill, including enabling TimescaleDB
compression and **measuring** the ratio — that measurement is what justifies TimescaleDB over a
managed database, and it must be a number taken here, not a vendor claim.

---

## Housekeeping — open, non-blocking

- AWS budget alert not yet configured. Blocks Phase 1.
- Domain not purchased. Blocks Phase 10 only.