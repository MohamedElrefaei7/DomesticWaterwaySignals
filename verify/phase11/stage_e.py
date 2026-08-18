"""Stage E — after `git pull` on the instance: the pins held, and nothing was re-pulled.

    python3 -m verify.phase11 e

PREFLIGHT'S COUNT IS PARSED, NEVER REIMPLEMENTED. `verify/preflight.py` gate 1 already enumerates
every `image:` in docker-compose.yml and every `FROM` in every Dockerfile - that enumeration IS the
fix for CLAUDE.md § 22's "a gate that checks a subset while its summary reports the whole set". A
second implementation of the same count here would be a second copy of one fact, and the copy that
drifts is always the one nobody is looking at. So this invokes preflight and reads what it says.

MEASURED 2026-08-17: six references across three files - docker-compose.yml (timescaledb, caddy),
Dockerfile.api (build, runtime) and Dockerfile.frontend (build, artifact).

UPDATED, PHASE 12: EIGHT references across FOUR files. `Dockerfile.scheduler` (build, runtime)
joins them - the scheduler image, which carries pg_dump so that the scheduler container never needs
the Docker socket. The constants below moved with it, deliberately and in the commit that added the
file: they are the tripwire for a Dockerfile landing without anybody noticing it was not walked.

NO IMAGE WAS RE-PULLED, and the reason this is worth a check rather than a shrug: a running
container's image ID is what Docker actually resolved, and the digest in the Compose file is what
this repo says it should have resolved. If those disagree, the pin did not hold - and the guard
that should have caught it is preflight gate 1, so a mismatch here means that guard failed. § 5's
"every image tag is pinned, and resolved from the machine that runs it" is not worth anything if
nobody ever compares the two afterwards.

THE FREE-SPACE BASELINE IS WRITTEN UNDER `/mnt/data`, NOT `/tmp`. Stages G and H read it: the
backup writes an archive to staging and the restore test downloads one, and "did the disk survive
that" is only answerable against a number taken beforehand. `/tmp` is cleared on reboot and on some
systems is a tmpfs sized as a fraction of RAM, so a baseline there is a number that may be gone by
the time it is wanted - and it would be gone silently, leaving Stage G with nothing to compare
against and no way to tell that from "the baseline said zero". It is not a tracked file either: a
verifier that writes into the repo puts unreviewed values in the log.

THIS IS THE ONE FILE THIS PACKAGE WRITES, and it is listed by name in
`tests/verify/test_result.py`'s `PERMITTED_WRITES` allow-list.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Sequence

from verify.phase11 import shell
from verify.phase11.result import Check, CheckResult, Precondition, failed, passed

DATA_DIR = Path("/mnt/data")
BASELINE_PATH = DATA_DIR / "phase11-verify-baseline.json"

# preflight's own summary line. Parsed rather than recomputed; the parenthesised count and the file
# list both come from `verify/preflight.py --dry-run`'s first bullet.
ENUMERATION_LINE = re.compile(
    r"every image reference across (?P<files>.+?) was enumerated \((?P<count>\d+) found\)"
)

EXPECTED_REFERENCES = 8
EXPECTED_FILES = 4


def parse_preflight_enumeration(text: str) -> tuple[int, list[str]]:
    """`(count, files)` from preflight's summary line, or raise Precondition.

    Raising on a line that does not match is deliberate: if preflight's output changes shape, the
    honest outcome is "I could not tell", not a count of zero that would report the stack as having
    no image references at all.
    """
    match = ENUMERATION_LINE.search(text)
    if match is None:
        raise Precondition(
            "Stage E: could not find preflight's enumeration line in its output. This parses "
            "preflight rather than reimplementing the count, so a change to that line's shape "
            "stops this check rather than making it agree with itself. observed output:\n"
            + text[:800]
        )
    files = [name.strip() for name in match.group("files").split(",") if name.strip()]
    return int(match.group("count")), files


def check_preflight_enumeration(count: int, files: Sequence[str]) -> CheckResult:
    name = "preflight enumerates every image reference in the stack"
    expected = f"{EXPECTED_REFERENCES} references across {EXPECTED_FILES} files"
    observed = f"{count} references across {len(files)} files: {list(files)}"
    if count != EXPECTED_REFERENCES or len(files) != EXPECTED_FILES:
        return failed(
            name,
            expected,
            observed
            + ". A count below this means a reference is not being gated - which is the failure "
            "§ 22 records from gate 1 checking one reference out of five while reporting the "
            "stack as pinned. A count above it means something new arrived and needs a pin.",
        )
    return passed(name, expected, observed)


def compose_digests(compose_text: str) -> dict[str, str]:
    """service -> digest, from the `image:` lines. Regex, matching preflight's own choice.

    A YAML round trip would discard every comment in docker-compose.yml, and that file's comments
    are load-bearing (its own header says so).
    """
    digests: dict[str, str] = {}
    service = None
    for line in compose_text.splitlines():
        service_match = re.match(r"^  ([a-zA-Z0-9_-]+):\s*$", line)
        if service_match:
            service = service_match.group(1)
            continue
        image_match = re.match(r"^\s*image:\s*(\S+)", line)
        if image_match and service:
            reference = image_match.group(1)
            if "@" in reference:
                digests[service] = reference.split("@", 1)[1]
    return digests


def check_no_image_was_repulled(
    running: dict[str, str], pinned: dict[str, str]
) -> CheckResult:
    """Every running container's resolved digest is the one this repo pins.

    A mismatch means the pin did not hold, which means preflight gate 1 did not catch it - so this
    is worth surfacing loudly rather than as a note. Only services that both pin an image and are
    running are compared: `api` and `frontend-build` BUILD rather than pull, so their pins live in
    `FROM` lines and are gated by preflight, not here.
    """
    name = "no running container drifted from its pinned digest"
    comparable = sorted(set(running) & set(pinned))
    expected = f"{len(comparable)} pinned service(s) running the digest docker-compose.yml names"

    if not comparable:
        return failed(
            name,
            expected,
            f"no service is both pinned and running. running={sorted(running)} "
            f"pinned={sorted(pinned)}. A comparison over an empty set is watching nothing.",
        )

    drifted = [
        f"{service}: running {running[service]}, pinned {pinned[service]}"
        for service in comparable
        if running[service] != pinned[service]
    ]
    if drifted:
        return failed(
            name,
            expected,
            "; ".join(drifted)
            + ". The pin did not hold, which means verify/preflight.py gate 1 did not catch it.",
        )
    return passed(name, expected, f"{len(comparable)} service(s) match: {comparable}")


def check_baseline_was_written(path: Path, payload: dict[str, Any]) -> CheckResult:
    """The baseline landed under /mnt/data, and it carries a real number.

    The path is asserted rather than assumed. `/tmp` is cleared on reboot and may be a tmpfs; a
    baseline that vanishes leaves Stage G comparing against nothing, and reporting that as zero.
    """
    name = "the free-space baseline is recorded under /mnt/data"
    expected = f"a JSON baseline at {BASELINE_PATH} with a positive free_bytes"
    if DATA_DIR not in path.parents:
        return failed(
            name,
            expected,
            f"baseline path is {path}, which is not under {DATA_DIR}. /tmp is cleared on reboot "
            f"and may be a tmpfs sized from RAM, so a baseline there can be gone by the time "
            f"Stage G wants it - silently.",
        )
    free = payload.get("free_bytes")
    if not isinstance(free, int) or free <= 0:
        return failed(name, expected, f"free_bytes={free!r} in {path}")
    return passed(name, expected, f"{path}: free_bytes={free:,} taken at {payload.get('taken_at')}")


# ---------------------------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------------------------


def _preflight_output() -> str:
    """Invoke preflight's dry run and return its text.

    `--dry-run` rather than a full run: the full gates need `.env` at mode 600 and a mounted data
    volume, and this stage is asking one question - how many references preflight ENUMERATES. It is
    invoked in-process rather than through `shell.run`, because it is this repo's own module and
    the allow-list exists to constrain what is run against the SYSTEM.
    """
    import contextlib
    import io

    from verify import preflight

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = preflight.main(["--dry-run"])
    if code != 0:
        raise Precondition(f"Stage E: preflight --dry-run exited {code}")
    return buffer.getvalue()


def _running_digests() -> dict[str, str]:
    """service -> the image digest Docker actually resolved, from `docker compose ps`."""
    listed = shell.run(["docker", "compose", "ps", "--format", "json"], cwd=Path.cwd())
    if listed.returncode != 0:
        raise Precondition(
            f"Stage E: `docker compose ps` exited {listed.returncode}: "
            f"{listed.stderr.strip() or '(no stderr)'}"
        )

    digests: dict[str, str] = {}
    for entry in _json_lines(listed.stdout):
        service = entry.get("Service")
        image = entry.get("Image", "")
        if service and "@" in image:
            digests[service] = image.split("@", 1)[1]
        elif service:
            inspected = shell.run(
                ["docker", "image", "inspect", image, "--format", "{{json .RepoDigests}}"]
            )
            if inspected.returncode == 0:
                for repo_digest in json.loads(inspected.stdout or "[]"):
                    if "@" in repo_digest:
                        digests[service] = repo_digest.split("@", 1)[1]
                        break
    return digests


def _json_lines(text: str) -> list[dict[str, Any]]:
    """`docker compose ps --format json` emits either a JSON array or one object per line.

    Both shapes are real - Compose changed this between versions - so both are handled rather than
    one being assumed. Getting it wrong yields an empty list, which is a check watching nothing.
    """
    text = text.strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        entries = []
        for line in text.splitlines():
            line = line.strip()
            if line:
                entries.append(json.loads(line))
        return entries
    return parsed if isinstance(parsed, list) else [parsed]


def _write_baseline() -> tuple[Path, dict[str, Any]]:
    import datetime
    import shutil

    if not DATA_DIR.is_dir():
        raise Precondition(
            f"Stage E: {DATA_DIR} is not a directory. `nofail` in fstab (§ 9) means boot proceeds "
            f"without the volume and the mount point exists as an empty directory on the root "
            f"disk, which every layer above reads as a healthy, empty world - so this refuses "
            f"rather than writing a baseline describing the wrong filesystem."
        )
    usage = shutil.disk_usage(DATA_DIR)
    payload = {
        "free_bytes": int(usage.free),
        "total_bytes": int(usage.total),
        "taken_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "note": (
            "Stage E's baseline. Stages G and H compare against it. Under /mnt/data rather than "
            "/tmp on purpose - see verify/phase11/stage_e.py."
        ),
    }
    BASELINE_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return BASELINE_PATH, payload


def read_baseline(path: Path = BASELINE_PATH) -> dict[str, Any]:
    if not path.exists():
        raise Precondition(
            f"no baseline at {path}. Stage E records it; run `python3 -m verify.phase11 e` first."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def checks() -> Sequence[Check]:
    count, files = parse_preflight_enumeration(_preflight_output())
    running = _running_digests()
    pinned = compose_digests(Path("docker-compose.yml").read_text(encoding="utf-8"))
    baseline_path, baseline = _write_baseline()

    return [
        lambda: check_preflight_enumeration(count, files),
        lambda: check_no_image_was_repulled(running, pinned),
        lambda: check_baseline_was_written(baseline_path, baseline),
    ]
