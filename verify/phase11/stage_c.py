"""Stage C — the state migration into the S3 backend, before and after.

WHAT STAGE C IS AND WHY IT HAS A VERIFIER AT ALL. `prevent_destroy` is an attribute of STATE, not
of a resource (CLAUDE.md § 8). Moving state is therefore the one operation in this project that can
make every other guard stop protecting anything, without changing a line of configuration and
without any resource being touched. The failure is silent: `terraform init -migrate-state` that
carried nothing leaves a working configuration whose next plan proposes building a second copy of
infrastructure that is already running.

    python3 -m verify.phase11 c-pre                     # before `terraform init -migrate-state`
    terraform -chdir=infra/terraform init -migrate-state   # human
    terraform -chdir=infra/terraform plan -out=c-post.tfplan          # human
    terraform -chdir=infra/terraform show -json c-post.tfplan > c-post.plan.json   # human
    python3 -m verify.phase11 c-post c-post.plan.json   # after

`c-post` TAKES A PLAN FILE, AND IT HAS TO. The condition being checked is C5's "No changes.", which
is a property of a PLAN. `terraform show -json` with no plan file emits a STATE document, and a
state document has no `resource_changes` key at all — so "assert there are no resource changes"
over it is true on every input including a migration that carried nothing. Measured on Terraform
1.15.8, this project's own state: state doc keys are ['checks', 'format_version',
'terraform_version', 'values']. `tfjson.require_plan` refuses the wrong document type rather than
passing over it.

AND "No changes." IS NOT AN EMPTY `resource_changes` LIST. Measured on 1.15.8: an unchanged plan
carries one `["no-op"]` entry per resource and reports `"applyable": false`. Asserting the list is
empty would fail on a correct migration of seventeen resources, and the natural repair for a check
that fails when everything is right is to delete it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Sequence

from verify.phase11 import shell, tfjson
from verify.phase11.protected import PROTECTED_ADDRESSES
from verify.phase11.result import Check, CheckResult, Precondition, failed, passed

REPO_ROOT = Path(__file__).resolve().parents[2]
TERRAFORM_DIR = REPO_ROOT / "infra" / "terraform"
BACKEND_TF = TERRAFORM_DIR / "backend.tf"
BOOTSTRAP_TF = TERRAFORM_DIR / "bootstrap" / "main.tf"

# The two files Terraform leaves behind when state is local. Both must be gone after a migration:
# a stale `terraform.tfstate` beside a configured S3 backend is a second copy of the truth, and it
# is the copy somebody restores from when the remote one looks wrong.
LOCAL_STATE_FILES = ("terraform.tfstate", "terraform.tfstate.backup")

WHAT = "c-post plan"


# ---------------------------------------------------------------------------------------------
# backend.tf's literals
# ---------------------------------------------------------------------------------------------
#
# READ, NEVER RESTATED. The bucket name is already written twice - once in backend.tf, which cannot
# interpolate, and once as bootstrap/main.tf's default - and tests/terraform/test_backend_hcl.py
# asserts the two agree. A third copy in this file would be a third thing to keep in step, and the
# one that drifts is always the one nobody is looking at.
#
# A REGEX RATHER THAN `python-hcl2`, DELIBERATELY, AND THIS IS THE NARROW CASE WHERE THAT IS RIGHT:
# hcl2 is in requirements-dev.txt, and these verifiers run on the instance from the runtime venv
# where it is not installed. An import error at that moment reads as a broken verifier. The pattern
# is anchored to `bucket =` / `key =` at the start of a line's content, and a miss RAISES rather
# than defaulting.

_BACKEND_LITERAL = r'^\s*{key}\s*=\s*"([^"]+)"'


def _literal(path: Path, key: str) -> str:
    if not path.exists():
        raise Precondition(f"{path} does not exist; cannot read the backend's {key}")
    match = re.search(
        _BACKEND_LITERAL.format(key=re.escape(key)),
        path.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if match is None:
        raise Precondition(
            f"no `{key} = \"...\"` literal found in {path}. This is read rather than restated so "
            f"there is one copy of the value; if the file's shape changed, fix the reader rather "
            f"than hardcoding the string here."
        )
    return match.group(1)


def state_bucket_name() -> str:
    return _literal(BACKEND_TF, "bucket")


def state_key() -> str:
    return _literal(BACKEND_TF, "key")


def bootstrap_bucket_default() -> str:
    """bootstrap/main.tf's `variable "state_bucket_name"` default.

    SCOPED TO THAT VARIABLE BLOCK, not to the first `default =` in the file. The first one belongs
    to `variable "aws_region"` and is "us-east-1" - a file-wide search returns it happily, and the
    agreement check then compares a bucket name against a region and reports drift that is not
    there. Caught by `test_backend_literals_are_read_from_the_files_and_agree` while this was
    being written.
    """
    if not BOOTSTRAP_TF.exists():
        raise Precondition(f"{BOOTSTRAP_TF} does not exist; cannot read the bootstrap bucket name")
    text = BOOTSTRAP_TF.read_text(encoding="utf-8")

    start = text.find('variable "state_bucket_name"')
    if start == -1:
        raise Precondition(
            f'no `variable "state_bucket_name"` block in {BOOTSTRAP_TF}. This is read rather than '
            f"restated so there is one copy of the value."
        )
    # The next `variable`/`resource`/`output` at column zero ends the block.
    end = len(text)
    for keyword in ('\nvariable "', '\nresource "', '\noutput "', "\ndata "):
        position = text.find(keyword, start + 1)
        if position != -1:
            end = min(end, position)

    match = re.search(_BACKEND_LITERAL.format(key="default"), text[start:end], re.MULTILINE)
    if match is None:
        raise Precondition(
            f'no `default = "..."` inside `variable "state_bucket_name"` in {BOOTSTRAP_TF}'
        )
    return match.group(1)


# ---------------------------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------------------------


def check_backend_literals_agree(backend: str, bootstrap: str) -> CheckResult:
    """The two written copies of the state bucket name are the same string.

    A `backend` block cannot interpolate, so this value is written twice by necessity (§ 8). It is
    asserted here as well as in the unit tests because the unit test proves the repo is consistent
    and this proves the repo the operator is standing in is the one they think it is.
    """
    name = "backend.tf and bootstrap/main.tf name the same bucket"
    expected = f"bootstrap default == backend bucket == {backend!r}"
    if backend != bootstrap:
        return failed(name, expected, f"backend.tf={backend!r} bootstrap/main.tf={bootstrap!r}")
    return passed(name, expected, backend)


def check_local_state_holds_protected(state: dict[str, Any]) -> CheckResult:
    """Before the migration: the local state is the thing being moved, so it must be complete."""
    observed = tfjson.managed_addresses(state)
    unlisted = sorted(observed - PROTECTED_ADDRESSES)
    missing = sorted(PROTECTED_ADDRESSES - observed)

    name = "local state holds exactly the protected addresses"
    expected = f"{len(PROTECTED_ADDRESSES)} managed addresses"
    if unlisted or missing:
        return failed(
            name,
            expected,
            f"{len(observed)} present; in state but unlisted: {unlisted}; "
            f"listed but absent: {missing}",
        )
    return passed(name, expected, f"{len(observed)} addresses, sets equal in both directions")


def check_bucket_versioning_enabled(versioning: dict[str, Any], bucket: str) -> CheckResult:
    """Versioning is the recovery path for a truncated state write, so it is checked BEFORE moving.

    A bucket with versioning disabled reports identically to one with it enabled on every day
    except the one where somebody needs the previous state. `aws s3api get-bucket-versioning`
    returns an EMPTY document when versioning was never enabled - not `Status: Disabled` - so a
    naive `!= "Suspended"` check passes on the never-enabled case.
    """
    status = versioning.get("Status")
    name = "the state bucket has versioning enabled"
    expected = 'Status == "Enabled"'
    if status != "Enabled":
        return failed(
            name,
            expected,
            f"bucket={bucket} Status={status!r} (an empty response means versioning was never "
            f"enabled, which is not the same as Suspended)",
        )
    return passed(name, expected, f"bucket={bucket} Status=Enabled")


def check_local_state_files_absent(present: Sequence[str]) -> CheckResult:
    """After the migration: nothing local is left claiming to be the state."""
    name = "no local state file remains"
    expected = f"none of {list(LOCAL_STATE_FILES)} present in {TERRAFORM_DIR}"
    if present:
        return failed(
            name,
            expected,
            f"{len(present)} still present: {sorted(present)}. A local state beside a configured "
            f"S3 backend is a second copy of the truth, and it is the one somebody restores from.",
        )
    return passed(name, expected, "none present")


def check_plan_has_no_pending_changes(plan: dict[str, Any]) -> CheckResult:
    """C5's "No changes.", read from the plan JSON.

    Every entry must be `["no-op"]`. NOT "the list is empty" - measured on Terraform 1.15.8, an
    unchanged plan carries one no-op entry per resource, so the empty-list form fails on a correct
    migration. `applyable` is asserted alongside, because it is Terraform's own one-word verdict on
    the same question and it costs nothing to read.
    """
    changes = tfjson.resource_changes(plan)
    pending = [tfjson.describe(entry) for entry in changes if not tfjson.is_no_op(entry)]
    applyable = plan.get("applyable")

    name = "the plan proposes no changes"
    expected = "every resource_change is ['no-op'], and applyable is false"
    if pending:
        return failed(
            name,
            expected,
            f"{len(pending)} of {len(changes)} pending: {'; '.join(sorted(pending))}. "
            f"A plan that wants to CREATE something that already exists means the migration did "
            f"not carry the state, and applying would build a second copy.",
        )
    if applyable is True:
        return failed(
            name,
            expected,
            f"every change is no-op but terraform reports applyable={applyable!r}",
        )
    if not changes:
        return failed(
            name,
            expected,
            "resource_changes is EMPTY. A plan over migrated state carries one no-op entry per "
            f"resource ({len(PROTECTED_ADDRESSES)} expected), so an empty list means the plan was "
            "computed against empty state - the migration carried nothing.",
        )
    return passed(name, expected, f"{len(changes)} resource_changes, all no-op, applyable={applyable!r}")


def check_object_versions_exist(versions: Sequence[dict[str, Any]], bucket: str, key: str) -> CheckResult:
    """At least one object version exists at the backend key.

    Two facts at once: state actually arrived at the key backend.tf names, and the bucket is
    versioning it. Versioning is the recovery path for a truncated state write (bootstrap/main.tf),
    and a bucket without it looks identical until the day it matters.
    """
    name = "the backend key holds at least one object version"
    expected = f"s3://{bucket}/{key} has >= 1 version"
    if not versions:
        return failed(
            name,
            expected,
            "0 versions. Either the state never arrived at this key, or the bucket is not "
            "versioning - `list-object-versions` returns no Versions in both cases.",
        )
    ids = [v.get("VersionId") for v in versions]
    if ids and all(version_id == "null" for version_id in ids):
        return failed(
            name,
            expected,
            f"{len(versions)} object(s), every VersionId is the literal 'null', which is what S3 "
            f"reports for objects written while versioning was OFF",
        )
    return passed(name, expected, f"{len(versions)} version(s), latest VersionId={ids[0]!r}")


# ---------------------------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------------------------


def _aws_json(argv: list[str], *, what: str) -> Any:
    """Run an allow-listed AWS read and parse its JSON, or raise Precondition.

    Every failure here is a precondition: no credentials, no such bucket, no network. None of them
    is a finding about the infrastructure, and reporting them as exit 1 would send the operator to
    investigate the wrong thing.
    """
    completed = shell.run(argv)
    if completed.returncode != 0:
        raise Precondition(
            f"{what}: `{' '.join(argv)}` exited {completed.returncode}: "
            f"{completed.stderr.strip() or '(no stderr)'}"
        )
    if not completed.stdout.strip():
        # An empty body is meaningful for get-bucket-versioning and is handled by the check; the
        # caller decides.
        return {}
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise Precondition(f"{what}: output of `{' '.join(argv)}` is not JSON: {exc}") from exc


def _remote_state() -> dict[str, Any]:
    # `cwd=` rather than a `-chdir=` flag built by interpolation, so every element of argv is a
    # literal in the source. `test_verifiers_never_invoke_plan_or_apply` walks the AST and matches
    # the CONSTANT prefix of each argv against the allow-list; an interpolated element in the
    # subcommand position would make that walk unable to tell what is being run.
    completed = shell.run(["terraform", "show", "-json"], cwd=TERRAFORM_DIR)
    if completed.returncode != 0:
        raise Precondition(
            f"c-post: `terraform show -json` exited {completed.returncode}: "
            f"{completed.stderr.strip() or '(no stderr)'}. After a migration this reads the S3 "
            f"backend, so an error here is usually an uninitialised or unreachable backend."
        )
    try:
        return tfjson.require_state(json.loads(completed.stdout), what="c-post remote state")
    except json.JSONDecodeError as exc:
        raise Precondition(f"c-post: `terraform show -json` output is not JSON: {exc}") from exc


def _present_local_state_files() -> list[str]:
    return [name for name in LOCAL_STATE_FILES if (TERRAFORM_DIR / name).exists()]


def checks_c_pre() -> Sequence[Check]:
    bucket = state_bucket_name()
    local = TERRAFORM_DIR / "terraform.tfstate"
    if not local.exists():
        raise Precondition(
            f"c-pre: {local} does not exist. Stage C migrates LOCAL state into the backend; if "
            f"there is no local state, either the migration already happened (run c-post) or this "
            f"is not the machine the state lives on."
        )
    state = tfjson.require_state(
        tfjson.load_document(local, what="c-pre local state"), what="c-pre local state"
    )
    versioning = _aws_json(
        ["aws", "s3api", "get-bucket-versioning", "--bucket", bucket],
        what="c-pre state bucket versioning",
    )

    return [
        lambda: check_backend_literals_agree(bucket, bootstrap_bucket_default()),
        lambda: check_local_state_holds_protected(state),
        lambda: check_bucket_versioning_enabled(versioning, bucket),
    ]


def checks_c_post(planfile: str) -> Sequence[Check]:
    bucket = state_bucket_name()
    key = state_key()
    plan = tfjson.require_plan(tfjson.load_document(planfile, what=WHAT), what=WHAT)
    present = _present_local_state_files()
    remote = _remote_state()
    listing = _aws_json(
        ["aws", "s3api", "list-object-versions", "--bucket", bucket, "--prefix", key],
        what="c-post object versions",
    )
    versions = [
        version
        for version in (listing.get("Versions") or [])
        if version.get("Key") == key
    ]

    return [
        lambda: check_local_state_files_absent(present),
        lambda: check_plan_has_no_pending_changes(plan),
        lambda: check_local_state_holds_protected(remote),
        lambda: check_object_versions_exist(versions, bucket, key),
    ]
