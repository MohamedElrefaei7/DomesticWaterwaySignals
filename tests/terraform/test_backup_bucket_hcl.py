"""Part 3 — the backup bucket and the instance's scoped access to it.

The bucket holds the only copy of the database that is not on one EBS volume in one AZ. These
assert the properties that follow from that: it cannot be destroyed by a plan, superseded objects
do not accumulate forever, and nothing holding the instance role can erase a backup or reach the
Terraform state bucket.
"""

import re
from pathlib import Path

import hcl2
from conftest import block, unwrap

BACKEND_FILE = Path(__file__).resolve().parents[2] / "infra" / "terraform" / "backend.tf"

_META_KEYS = {"__is_block__", "__comments__", "__start_line__", "__end_line__"}


def blocks(attrs, key):
    """Every nested block of one kind, as plain dicts. `block()` in conftest returns only one."""
    value = attrs.get(key)
    if not isinstance(value, list):
        return []
    return [
        {k: v for k, v in entry.items() if k not in _META_KEYS}
        for entry in value
        if isinstance(entry, dict)
    ]


def _bucket(tf):
    buckets = tf.resources_of_type("aws_s3_bucket")
    assert len(buckets) == 1, (
        f"expected exactly one bucket in the main configuration, found {sorted(buckets)}. The "
        f"state bucket belongs to bootstrap/, which is a separate configuration."
    )
    return next(iter(buckets.items()))


def _backup_policy_document(tf):
    """The IAM policy the instance role gains. Not the bucket policy - a different resource."""
    policies = tf.resources_of_type("aws_iam_policy")
    assert policies, (
        "no aws_iam_policy defined. The instance cannot write a backup, and nothing here is "
        "constrained - every assertion below would pass over an empty document."
    )
    (name, attrs), = policies.items()
    return name, attrs["policy"]


def test_backup_bucket_has_prevent_destroy(tf):
    name, attrs = _bucket(tf)
    lifecycle = block(attrs, "lifecycle")

    assert lifecycle is not None, f"aws_s3_bucket.{name} declares no lifecycle block"
    assert lifecycle.get("prevent_destroy") is True, (
        f"aws_s3_bucket.{name}.lifecycle.prevent_destroy is "
        f"{lifecycle.get('prevent_destroy')!r}. This bucket holds the only copy of the database "
        f"that is not on one EBS volume in one AZ."
    )


def test_backup_bucket_versioning_enabled(tf):
    versioning = tf.resources_of_type("aws_s3_bucket_versioning")
    assert versioning, "the backup bucket has no aws_s3_bucket_versioning resource"

    (name, attrs), = versioning.items()
    configuration = block(attrs, "versioning_configuration")
    assert configuration is not None, f"{name} declares no versioning_configuration"
    assert unwrap(configuration.get("status")) == "Enabled", (
        f"{name}.versioning_configuration.status is {unwrap(configuration.get('status'))!r}, "
        f"expected 'Enabled'"
    )


def test_backup_lifecycle_has_noncurrent_version_expiration(tf):
    """Versioning without noncurrent expiry retains every overwritten object forever.

    Expiring the current version only makes it NONCURRENT. The bytes stay, indefinitely, and the
    symptom arrives on a bill four months later rather than as any kind of error.
    """
    configurations = tf.resources_of_type("aws_s3_bucket_lifecycle_configuration")
    assert configurations, "the backup bucket has no lifecycle configuration"

    (name, attrs), = configurations.items()
    rules = blocks(attrs, "rule")
    assert rules, f"{name} declares no rule blocks"

    for rule in rules:
        rule_id = unwrap(rule.get("id"))
        assert "noncurrent_version_expiration" in rule, (
            f"lifecycle rule {rule_id!r} expires current versions but never noncurrent ones, so "
            f"every superseded object is retained forever"
        )


def test_backup_lifecycle_covers_both_prefixes_with_distinct_expiry(tf):
    """`backups/daily/` and `backups/monthly/`, with DIFFERENT retention.

    One rule over the whole bucket would give the monthly copies the daily expiry, which silently
    turns "we can restore any month of the last year" into "we can restore the last five weeks" -
    a claim that stays true-looking for 35 days.
    """
    (name, attrs), = tf.resources_of_type("aws_s3_bucket_lifecycle_configuration").items()
    rules = blocks(attrs, "rule")

    by_prefix = {}
    for rule in rules:
        filter_block = block(rule, "filter")
        assert filter_block is not None, (
            f"lifecycle rule {unwrap(rule.get('id'))!r} in {name} has no filter, so it applies to "
            f"the whole bucket"
        )
        prefix = unwrap(filter_block.get("prefix"))
        expiration = block(rule, "expiration")
        assert expiration is not None, f"rule for {prefix!r} declares no expiration"
        by_prefix[prefix] = expiration.get("days")

    assert set(by_prefix) == {"backups/daily/", "backups/monthly/"}, (
        f"lifecycle rules cover {sorted(by_prefix)}, expected exactly backups/daily/ and "
        f"backups/monthly/"
    )
    assert by_prefix["backups/daily/"] != by_prefix["backups/monthly/"], (
        f"both prefixes expire at {by_prefix['backups/daily/']} days. The monthly copies exist "
        f"precisely to outlive the daily ones."
    )
    assert by_prefix["backups/monthly/"] > by_prefix["backups/daily/"], (
        f"monthly ({by_prefix['backups/monthly/']}d) does not outlive daily "
        f"({by_prefix['backups/daily/']}d)"
    )


def test_backup_bucket_public_access_block_all_four_true(tf):
    blocks_ = tf.resources_of_type("aws_s3_bucket_public_access_block")
    assert blocks_, "the backup bucket has no aws_s3_bucket_public_access_block"

    (name, attrs), = blocks_.items()
    for flag in (
        "block_public_acls",
        "block_public_policy",
        "ignore_public_acls",
        "restrict_public_buckets",
    ):
        assert attrs.get(flag) is True, (
            f"{name}.{flag} is {attrs.get(flag)!r}, expected true"
        )


def test_backup_bucket_policy_denies_insecure_transport(tf):
    policies = tf.resources_of_type("aws_s3_bucket_policy")
    assert policies, "the backup bucket has no aws_s3_bucket_policy"

    (name, attrs), = policies.items()
    document = attrs.get("policy", "")

    assert "aws:SecureTransport" in document, (
        f"aws_s3_bucket_policy.{name} does not mention aws:SecureTransport"
    )
    assert "Deny" in document, f"aws_s3_bucket_policy.{name} has no Deny effect"


def test_instance_policy_has_no_delete_action(tf):
    """No `s3:Delete*` of any kind, and no `s3:*`.

    RETENTION IS A LIFECYCLE RULE, WHICH S3 EXECUTES ITSELF. It is not a principal action, and
    lifecycle expiry is the correct place for it precisely BECAUSE the instance cannot reach it.
    The tempting version grants `s3:DeleteObject` "so the retention job can clean up", which puts
    retention in code somebody can read at the cost of letting anything holding the instance role
    erase every backup in the account.
    """
    name, document = _backup_policy_document(tf)

    assert "s3:Delete" not in document, (
        f"aws_iam_policy.{name} grants a delete action. Anything holding the instance role could "
        f"then erase every backup:\n{document}"
    )
    assert '"s3:*"' not in document and "'s3:*'" not in document, (
        f"aws_iam_policy.{name} grants s3:*, which includes every delete action there is"
    )


def test_instance_policy_resources_are_backup_bucket_scoped(tf):
    """No `Resource = "*"`. A wildcard resource reaches every bucket in the account."""
    name, document = _backup_policy_document(tf)

    for wildcard in ('Resource = "*"', 'Resource = ["*"]', '"Resource": "*"'):
        assert wildcard not in document, (
            f"aws_iam_policy.{name} uses a wildcard resource ({wildcard})"
        )

    assert "aws_s3_bucket.backups.arn" in document, (
        f"aws_iam_policy.{name} does not scope its resources to the backup bucket by reference:\n"
        f"{document}"
    )


def test_instance_policy_cannot_reach_state_bucket(tf):
    """An instance that can write Terraform state is an instance that can lie about what exists.

    Asserted against the state bucket's real name, read from backend.tf, rather than against a
    literal repeated here - so it keeps holding if that name changes.
    """
    name, document = _backup_policy_document(tf)

    backend_text = BACKEND_FILE.read_text(encoding="utf-8")
    with BACKEND_FILE.open() as f:
        parsed = hcl2.load(f)
    state_bucket = None
    for entry in parsed.get("terraform", []):
        for backend in entry.get("backend", []):
            for type_key, attrs in backend.items():
                if not type_key.startswith("__"):
                    state_bucket = unwrap(attrs.get("bucket"))
    assert state_bucket, f"could not read the state bucket name from {BACKEND_FILE}"
    assert state_bucket in backend_text  # the parse agrees with the source

    assert state_bucket not in document, (
        f"aws_iam_policy.{name} names the Terraform state bucket ({state_bucket!r}). An instance "
        f"that can write state can create resources nothing knows about, and destroy resources "
        f"Terraform believes it still manages."
    )
    assert "aws_s3_bucket.state" not in document, (
        f"aws_iam_policy.{name} references the state bucket resource directly"
    )

    # NAMING THE STATE BUCKET IS ONLY THE OBVIOUS ROUTE TO IT. A wildcard resource reaches it
    # without ever mentioning it, and the two assertions above are both green over `Resource =
    # ["*"]` - measured, when that exact mutation left this test passing while only the
    # scoping test caught it. A test named for a property has to fail on every way of losing it.
    #
    # So: EVERY Resource entry must be a reference to the backup bucket. Anything the policy can
    # reach that is not this bucket is a finding, whether or not it is spelled out.
    resource_lists = re.findall(r"Resource = \[(.*?)\]", document, flags=re.DOTALL)
    assert resource_lists, (
        f"no Resource entries found in aws_iam_policy.{name} - this test would pass over a "
        f"document it failed to parse:\n{document}"
    )
    for entry in resource_lists:
        assert "aws_s3_bucket.backups.arn" in entry, (
            f"aws_iam_policy.{name} grants `Resource = [{entry.strip()}]`, which is not scoped to "
            f"the backup bucket. Whatever else it reaches includes the Terraform state bucket."
        )
