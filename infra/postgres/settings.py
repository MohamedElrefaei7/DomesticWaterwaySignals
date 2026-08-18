"""The cluster's settings, under version control - and what that can and cannot mean here.

---------------------------------------------------------------------------------------------
READ THIS BEFORE TRYING TO MOUNT postgresql.conf. THAT IS THE OBVIOUS FIX AND IT BREAKS A REBUILD.
---------------------------------------------------------------------------------------------

Postgres reads `postgresql.conf` from PGDATA, which on this instance is
`/var/lib/postgresql/data/` - on the data volume, written by `timescaledb-tune` when the
`timescale/timescaledb` image initialised the volume on 2026-08-11. All 33 non-default settings
this cluster runs come from that file, and NONE of them were in version control until this
module existed.

The instinct on reading that is to bind-mount `postgresql.conf` into the container from the repo.
Do not:

  - Mounting a file over `$PGDATA/postgresql.conf` BREAKS `initdb` ON A FRESH VOLUME, because
    initdb writes that file itself and cannot write through a read-only mount. A fresh volume is
    precisely the case this module exists to make reproducible, so the fix would break the case
    it was for.
  - `include_dir` would be the clean mechanism and is NOT AVAILABLE: the generated file has it
    commented out, and `include_dir` cannot itself be set by `ALTER SYSTEM` - it is one of the
    settings Postgres refuses there, so there is no way to turn it on without editing the file
    that cannot be mounted.

SO THE ACHIEVABLE PROPERTY IS NOT "THE SETTINGS LIVE IN GIT". IT IS:

    THE COMMITTED VALUES ARE AUTHORITATIVE, AND ANY DIVERGENCE FROM THEM IS DETECTED.

That is the same shape as the image digests (CLAUDE.md § 12) and the postgresql-client version pin
(§ 3): the artifact lives somewhere this repo cannot hold it, so the repo holds the value it must
have and a gate reads what is actually running. `verify/preflight.py`'s required-settings gate is
that gate.

---------------------------------------------------------------------------------------------
ALTER SYSTEM IS THE APPLICATION MECHANISM
---------------------------------------------------------------------------------------------

`ALTER SYSTEM SET` writes `postgresql.auto.conf`, which Postgres reads AFTER `postgresql.conf` and
which therefore takes precedence over it. Measured on this instance 2026-08-18: that file is
EMPTY, so nothing the tuner chose is lost by using it - the two files do not overlap at all today.

The alternative is hand-editing `postgresql.conf` on the data volume. That is the untracked
hand-edit this whole module exists to make detectable, performed as the fix for it.

Applying a change is a human step, documented in `docs/runbooks/cluster-settings.md`. Nothing in
this repo issues `ALTER SYSTEM`; CLAUDE.md § 1 keeps that with the human, as it keeps
`terraform apply`.

---------------------------------------------------------------------------------------------
TWO LISTS, AND THE DISTINCTION IS LOAD-BEARING
---------------------------------------------------------------------------------------------

  REQUIRED_SETTINGS   The deliberate overrides. ENFORCED by the preflight gate. Each one is a
                      decision with a reason, and the reason is stored beside the value.

  TUNER_BASELINE      All 33 settings as `timescaledb-tune` derived them for THIS instance's
                      memory and cpu count. RECORDED, NOT ENFORCED. It lives in the sibling
                      `tuner-baseline.json`, and it is captured from a running cluster by
                      `verify/preflight.py --write-baseline`, never typed.

MERGING THEM WOULD MAKE THE GATE FAIL ON A CORRECT STATE. The tuner's output is a function of the
instance size: a rebuild onto a larger instance derives a larger `shared_buffers`, correctly, and
a gate enforcing the baseline would go red at exactly the moment everything was working. A guard
that goes red on a correct state is the shape recorded in CONTEXT.md from `d-pre`, and it gets
disabled rather than fixed.

What the baseline IS for: after a rebuild, `--resolve-baseline` prints the running cluster beside
the committed one, so a re-derivation that differs is VISIBLE. Today that difference is silent -
there is no diff anywhere, because there was no committed side to diff against.

---------------------------------------------------------------------------------------------
WHY THIS IS A .py AND NOT A .toml
---------------------------------------------------------------------------------------------

Because the enforced half is not data. `REQUIRED_SETTINGS` carries a REASON per value, that reason
is prose of the kind a `.toml` string mangles across lines, and it is imported directly by the
gate that enforces it - a TOML file would need a parser, a schema, and a test that the schema
matches, to hold three fields.

The RECORDED half is data, is written by a machine, and is never hand-edited - so it is a sibling
`.json`, and the split is deliberate: the file a human owns is source with its reasoning in it,
and the file a machine owns is a JSON dump with no reasoning to lose when it is rewritten.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

TUNER_BASELINE_PATH = Path(__file__).resolve().parent / "tuner-baseline.json"


@dataclass(frozen=True)
class RequiredSetting:
    """One deliberate override. `minimum`, not equality - see below.

    A FLOOR RATHER THAN AN EXACT VALUE, deliberately. Every setting here is sized against a worst
    case, so a cluster running MORE than the floor is running a correct state; failing it would
    mean a future session that raised the value for a good reason had to edit this file in the
    same commit or watch the gate go red. Equality would buy a guarantee nobody wants: that
    nobody may ever be more generous than us.
    """

    name: str
    minimum: int
    reason: str


REQUIRED_SETTINGS: tuple[RequiredSetting, ...] = (
    RequiredSetting(
        name="max_locks_per_transaction",
        minimum=512,
        reason=(
            "THE ARITHMETIC IS THE REASON. A BARE 512 GETS TIDIED TO 256 BY SOMEBODY ECONOMISING "
            "ON MEMORY WHO CANNOT SEE WHAT IT WAS SIZED AGAINST.\n"
            "\n"
            "The cluster's lock table is allocated once at startup and holds\n"
            "\n"
            "    slots = max_locks_per_transaction * (max_connections + max_prepared_transactions)\n"
            "\n"
            "slots IN TOTAL, shared across the whole cluster - it is NOT a per-transaction limit, "
            "despite the name. One query that touches many relations can exhaust it on its own.\n"
            "\n"
            "Measured on this instance 2026-08-18, at the tuner's max_connections = 25 and "
            "max_prepared_transactions = 0:\n"
            "\n"
            "    128 * (25 + 0) =  3,200 slots   the default this cluster ran until now\n"
            "    512 * (25 + 0) = 12,800 slots   this floor\n"
            "\n"
            "Against that, the demand: gauge_readings_iv held 986 chunks for 258,739 rows (262 "
            "rows per chunk, 18.9 years at a 7-day interval), and a query over the whole table "
            "takes a lock per chunk PLUS one per index per chunk - roughly 2,000 slots for a bare "
            "`SELECT min(ts), max(ts), count(*)`. At 3,200 total, shared, that query failed "
            "outright with `ERROR: out of shared memory`, intermittently, because the threshold "
            "depends on what else was connected at the time.\n"
            "\n"
            "Migration 0027 consolidates those chunks to a 365-day interval, which takes the "
            "worst case to roughly 40 slots. THIS FLOOR IS NOT REDUCED WHEN THAT LANDS: it is "
            "the headroom that makes the next large hypertable a decision rather than an "
            "incident, and 0027 could not have been applied without it.\n"
            "\n"
            "THE COST, so it is not re-litigated by guesswork: the lock table costs roughly "
            "270 bytes per slot, allocated in shared memory at startup and never grown. 12,800 "
            "slots is about 3.3 MB, against the 864 KB the 3,200-slot default took - so the "
            "CHANGE is under 2.5 MB, on an instance with 1.9 GB of memory and a shared_buffers "
            "already at 477 MB. It is well under one percent of what shared_buffers alone takes."
        ),
    ),
)

# ---------------------------------------------------------------------------------------------
# The recorded baseline
# ---------------------------------------------------------------------------------------------
#
# The committed placeholder is one that CANNOT be mistaken for a capture, for the same reason the
# committed image digest is one that cannot resolve (CLAUDE.md § 12): a missed step must fail
# loudly rather than fall back to something plausible. An empty `{}` here would read as "captured,
# and there was nothing to record", which is the shape CLAUDE.md § 22 describes - a gate over an
# empty collection reporting the whole set as verified.

BASELINE_NEVER_CAPTURED = "NEVER-CAPTURED"


@dataclass(frozen=True)
class TunerBaseline:
    """What `--write-baseline` captured, or the fact that it never ran.

    `captured_at` is None exactly when `settings` is empty, and `is_captured` is the only thing
    any caller should branch on - so that a future reader cannot accidentally treat the
    placeholder's empty mapping as a cluster with no non-default settings.
    """

    captured_at: str | None
    instance: str | None
    settings: dict[str, str]

    @property
    def is_captured(self) -> bool:
        return self.captured_at is not None and bool(self.settings)


def load_tuner_baseline(path: Path = TUNER_BASELINE_PATH) -> TunerBaseline:
    raw = json.loads(path.read_text(encoding="utf-8"))
    captured_at = raw.get("captured_at")
    if captured_at == BASELINE_NEVER_CAPTURED:
        captured_at = None
    return TunerBaseline(
        captured_at=captured_at,
        instance=raw.get("instance"),
        settings=dict(raw.get("settings") or {}),
    )
