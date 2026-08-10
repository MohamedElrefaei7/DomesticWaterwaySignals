# CONTEXT.md — running log

This is the **log**: current state, decisions as they are made, and `§ Up Next`. Stable contracts
live in `CLAUDE.md`. If something here hardens into an invariant, move it there and note the move.

**Last updated:** 2026-08-10 (re-verified same day, post-`/clear`)

---

## Current state

**Phase 1 defined, not applied.** No infrastructure has been created; no `terraform apply` has run.

- `CLAUDE.md` seeded with the contracts carried forward from the prior project (`Trade_Analysis_Project`).
- `.gitignore` committed, verified not self-excluding.
- `infra/terraform/` defines the full environment: purpose-built VPC + public subnet, a
  three-port ingress allowlist with mandatory egress, a separate `prevent_destroy`+encrypted EBS
  data volume, an EC2 instance with IMDSv2 required and a pinned AMI, a `prevent_destroy` EIP, and
  an SSM-only instance role. See `CLAUDE.md § 8`.
- `tests/terraform/` (`pytest`, offline, no AWS credentials/network/binary needed) — 15 tests
  green, and all 10 load-bearing decisions confirmed by mutation: each was reverted, the named
  test was observed to fail, then restored.
- `terraform init && terraform validate` succeed locally (AWS provider resolved to 5.100.0).
  `.terraform.lock.hcl` is committed and correctly not gitignored.
- **Re-verified from a fresh session:** `terraform plan` produces exactly 17 resource addresses,
  matching the spec (VPC, subnet, IGW, route table, route table association, security group, 3
  ingress rules, 1 egress rule, IAM role, instance profile, policy attachment, instance, EIP, EBS
  volume, `aws_volume_attachment.data`) — `test_data_volume_is_a_top_level_resource` asserts both
  the volume and the attachment exist, so the pairing is guarded. `test_ssh_ingress_cidr_is_a_variable_reference_not_a_literal`
  was re-mutated (port range widened to 0–65535): it fails on the `assert ssh_rules` not-found guard
  before touching `cidr_ipv4`, not on an unguarded lookup — confirmed not a vacuous pass.
  `.terraform.lock.hcl` was missing `h1:` hashes for any platform but the one that ran `init`
  (darwin_arm64); ran `terraform providers lock -platform=linux_amd64 -platform=darwin_arm64` and
  confirmed the file grew (one `h1:` hash → two). See `CLAUDE.md § 8`.
- Live-verified: `terraform plan` with a deliberately broad `ssh_admin_cidr` is rejected by the
  variable `validation` block (exit 1, no resources created). One correction to the plan as
  written: Terraform authenticates and prints the full resource-creation preview *before* raising
  the validation error, not before — see the Housekeeping note below.
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

**The provisioning commit.** UUID-based mount of the data volume (never a device path), an
interface-scoped `DOCKER-USER` iptables setup (discover the interface at boot; never hardcode it),
and ufw with the admin-CIDR SSH rule opened before the default-deny activates. This is a script run
after `apply`, not `user_data` — see `CLAUDE.md § 8` and decision 9's note in the Phase 1 commit
about the no-public-IP window before the EIP associates.

A human needs to: configure the AWS budget alert, fill in real values in
`infra/terraform/terraform.tfvars` (region, AZ, `ssh_admin_cidr`, a verified current AMI ID), and
run `terraform apply`.

Then, in order: Phase 2 orchestration skeleton (migration runner, `job_runs`, `@job`, APScheduler with
persistent store, cadence table, heartbeat) **before the first ingest client**, so the first data that
ever lands is already observed. Phase 3 USGS ingest and backfill, including enabling TimescaleDB
compression and **measuring** the ratio — that measurement is what justifies TimescaleDB over a
managed database, and it must be a number taken here, not a vendor claim.

---

## Housekeeping — open, non-blocking

- AWS budget alert not yet configured. Blocks `terraform apply`.
- Domain not purchased. Blocks Phase 10 only.
- **State is local this commit.** No S3 backend, no state locking — that is explicitly out of
  scope for Phase 1. If `terraform.tfstate` is lost, `prevent_destroy` protects nothing, because
  Terraform no longer knows the data volume or the EIP exist. Do not treat local state as durable
  once anything has actually been applied.