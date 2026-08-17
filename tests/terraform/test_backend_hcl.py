"""Part 2 — Terraform state in a versioned, locked S3 backend.

Two configurations, parsed separately: the main one under infra/terraform/ (the `tf` fixture in
conftest) and the bootstrap one under infra/terraform/bootstrap/, which creates the state bucket
and keeps its own state local. They are separate because a bucket cannot hold the state that
describes it.

Source text via python-hcl2, no AWS credentials and no terraform binary — CLAUDE.md § 8.
"""

from pathlib import Path

import hcl2
import pytest
from conftest import ParsedConfig, block, unwrap

BOOTSTRAP_DIR = Path(__file__).resolve().parents[2] / "infra" / "terraform" / "bootstrap"
BACKEND_FILE = "backend.tf"


@pytest.fixture(scope="session")
def bootstrap():
    """The bootstrap configuration, parsed the same way the main one is.

    Asserts it found files at all. A fixture that silently resolves an empty directory makes every
    assertion below pass over nothing — the vacuous-pass shape this project has shipped twice
    (CLAUDE.md § 21).
    """
    files = {}
    tf_files = sorted(BOOTSTRAP_DIR.glob("*.tf"))
    assert tf_files, (
        f"no .tf files under {BOOTSTRAP_DIR} - every assertion in this module would pass over an "
        f"empty configuration"
    )
    for path in tf_files:
        with path.open() as f:
            files[path.name] = hcl2.load(f)
    return ParsedConfig(files)


def _backend(tf):
    """The `terraform { backend "s3" { ... } }` block from backend.tf, as a plain dict.

    Read from the raw parse rather than from ParsedConfig, which ingests resources, data sources,
    variables and outputs - a backend is none of those.
    """
    assert BACKEND_FILE in tf.files, (
        f"{BACKEND_FILE} is not among the parsed files ({sorted(tf.files)}) - state is still local"
    )
    terraform_blocks = tf.files[BACKEND_FILE].get("terraform", [])
    for entry in terraform_blocks:
        for backend in entry.get("backend", []):
            for type_key, attrs in backend.items():
                if type_key.startswith("__"):
                    continue
                return unwrap(type_key), attrs
    raise AssertionError(f"{BACKEND_FILE} declares no backend block")


def _state_bucket(bootstrap):
    buckets = bootstrap.resources_of_type("aws_s3_bucket")
    assert len(buckets) == 1, (
        f"the bootstrap configuration should create exactly one bucket, found {sorted(buckets)}"
    )
    return next(iter(buckets.items()))


# ---------------------------------------------------------------------------------------------
# The backend
# ---------------------------------------------------------------------------------------------


def test_backend_config_uses_s3(tf):
    """State is remote, in S3, encrypted, under a stated key."""
    backend_type, attrs = _backend(tf)

    assert backend_type == "s3", f"backend type is {backend_type!r}, expected s3"
    assert unwrap(attrs.get("bucket")), "the backend names no bucket"
    assert unwrap(attrs.get("key")), "the backend names no state key"
    assert unwrap(attrs.get("region")), "the backend names no region"
    assert attrs.get("encrypt") is True, (
        f"encrypt is {attrs.get('encrypt')!r}. State holds resource attributes in cleartext."
    )


def test_backend_has_locking_enabled(tf):
    """Either native S3 locking or a DynamoDB table — but one of them, not neither.

    Without a lock, two concurrent applies both read the same state, both write, and the second
    silently discards the first's record of what it created. The resources exist; nothing knows
    about them. Accepting either mechanism is deliberate: this asserts the PROPERTY, so a future
    move between mechanisms does not require rewriting the guard.
    """
    _, attrs = _backend(tf)

    use_lockfile = attrs.get("use_lockfile")
    dynamodb_table = attrs.get("dynamodb_table")

    assert use_lockfile is True or dynamodb_table, (
        "the backend enables NEITHER native S3 locking (`use_lockfile = true`) nor a DynamoDB "
        "lock table (`dynamodb_table`). Concurrent applies will race and one will lose its "
        "record of what it created."
    )


def test_backend_bucket_matches_bootstrap_bucket(tf, bootstrap):
    """The one string this design is forced to write twice must agree in both places.

    A backend block cannot interpolate — it is evaluated before variables, locals and data sources
    exist — so the bucket name is a literal in backend.tf and a variable default in bootstrap/.
    Two files holding one fact drift silently, and the symptom here is a `terraform init` against
    a bucket nobody created, which reads as a permissions problem.
    """
    _, attrs = _backend(tf)
    backend_bucket = unwrap(attrs["bucket"])

    declared = bootstrap.variables.get("state_bucket_name")
    assert declared is not None, "bootstrap declares no state_bucket_name variable"
    bootstrap_bucket = unwrap(declared.get("default"))

    assert backend_bucket == bootstrap_bucket, (
        f"backend.tf points at {backend_bucket!r} while bootstrap/ creates "
        f"{bootstrap_bucket!r}. `terraform init` would target a bucket that does not exist."
    )


# ---------------------------------------------------------------------------------------------
# The state bucket
# ---------------------------------------------------------------------------------------------


def test_state_bucket_versioning_enabled(bootstrap):
    """Versioning is the recovery path for a corrupted or truncated state write."""
    versioning = bootstrap.resources_of_type("aws_s3_bucket_versioning")
    assert versioning, "the state bucket has no aws_s3_bucket_versioning resource"

    (name, attrs), = versioning.items()
    configuration = block(attrs, "versioning_configuration")
    assert configuration is not None, f"{name} declares no versioning_configuration block"
    assert unwrap(configuration.get("status")) == "Enabled", (
        f"{name}.versioning_configuration.status is "
        f"{unwrap(configuration.get('status'))!r}, expected 'Enabled'. Without it, a bad state "
        f"write overwrites the last good one and there is nothing to roll back to."
    )


def test_state_bucket_public_access_block_all_four_true(bootstrap):
    """All four flags. Three of four leaves a route to exposing the state file publicly."""
    blocks = bootstrap.resources_of_type("aws_s3_bucket_public_access_block")
    assert blocks, "the state bucket has no aws_s3_bucket_public_access_block"

    (name, attrs), = blocks.items()
    for flag in (
        "block_public_acls",
        "block_public_policy",
        "ignore_public_acls",
        "restrict_public_buckets",
    ):
        assert attrs.get(flag) is True, (
            f"{name}.{flag} is {attrs.get(flag)!r}, expected true. This bucket holds the file "
            f"describing every resource in the account, in cleartext."
        )


def test_state_bucket_has_prevent_destroy(bootstrap):
    """The bucket holds the only record of what infrastructure exists."""
    name, attrs = _state_bucket(bootstrap)

    lifecycle = block(attrs, "lifecycle")
    assert lifecycle is not None, f"aws_s3_bucket.{name} declares no lifecycle block"
    assert lifecycle.get("prevent_destroy") is True, (
        f"aws_s3_bucket.{name}.lifecycle.prevent_destroy is "
        f"{lifecycle.get('prevent_destroy')!r}, expected true"
    )


def test_state_bucket_has_no_lifecycle_expiry(bootstrap):
    """THE INVERSE ASSERTION, and the reason it exists is the reflex it guards against.

    Part 3's backup bucket carries a lifecycle rule with `noncurrent_version_expiration`, because
    superseded backup objects are large dead weight. The natural next move is to apply the same
    rule here "for consistency". Each state object version is a RECOVERY POINT of a few kilobytes,
    and the day one is wanted is the day somebody is recovering from a bad apply — so retention
    here is indefinite on purpose, and that intent needs a test or the next reader cannot tell it
    from an omission.
    """
    rules = bootstrap.resources_of_type("aws_s3_bucket_lifecycle_configuration")
    assert rules == {}, (
        f"the state bucket has a lifecycle configuration ({sorted(rules)}). Expiring state object "
        f"versions deletes the recovery points that justify versioning in the first place."
    )


def test_state_bucket_policy_denies_insecure_transport(bootstrap):
    """A bucket policy denying any request where aws:SecureTransport is false."""
    policies = bootstrap.resources_of_type("aws_s3_bucket_policy")
    assert policies, "the state bucket has no aws_s3_bucket_policy"

    (name, attrs), = policies.items()
    # jsonencode({...}) parses as an interpolation string; match on its text.
    document = attrs.get("policy", "")

    assert "aws:SecureTransport" in document, (
        f"aws_s3_bucket_policy.{name} does not mention aws:SecureTransport"
    )
    assert '"Deny"' in document or "Deny" in document, (
        f"aws_s3_bucket_policy.{name} has no Deny effect"
    )
