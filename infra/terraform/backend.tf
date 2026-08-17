# Terraform state lives in S3, versioned and locked — not on one laptop.
#
# WHY THIS LANDS BEFORE THE BACKUP BUCKET (CONTEXT.md § Up Next item 4, which called it the
# highest-value piece of unowned work in infra/): `prevent_destroy` is an attribute of STATE, not
# of the resource. If this file's state is lost, Terraform no longer knows the data volume or the
# EIP exist, so `prevent_destroy` protects nothing and the next `apply` proposes creating a second
# copy of infrastructure that is already running. Everything Part 3 adds — a backup bucket whose
# whole purpose is to survive the loss of one EBS volume — depends on that not happening.
#
# THE BUCKET NAME IS A LITERAL, AND HAS TO BE. A `backend` block is evaluated before variables,
# locals, data sources and providers exist: Terraform has to know where state lives before it can
# read anything that could tell it. So `var.` and `data.` are unavailable here by design, and the
# account-id suffix used for the Part 3 backup bucket is not an option for this one.
#
# That means the bootstrap configuration under bootstrap/ holds this same string a second time.
# Two files holding one fact drift silently, so `tests/terraform/test_backend_hcl.py` reads both
# and asserts they agree. A collision on this globally-unique name fails the bootstrap apply
# immediately and loudly, before any state has moved — a clean failure whose fix is one string in
# two files.

terraform {
  backend "s3" {
    bucket = "domestic-waterway-signals-tfstate"
    key    = "infra/terraform.tfstate"

    # Literal for the same reason the bucket name is: no `var.aws_region` here. It agrees with
    # that variable's default, and the alarm in Part 3 is pinned to us-east-1 anyway because
    # Route53 health-check metrics exist nowhere else.
    region = "us-east-1"

    # SSE on the state object. State holds resource attributes in cleartext — this stack's
    # plausibly includes the database password and the SNS endpoint — so this is not decorative.
    encrypt = true

    # NATIVE S3 LOCKING, not a DynamoDB lock table. Terraform 1.10 added conditional-write
    # locking against a `.tflock` object beside the state object; the installed version is
    # 1.15.8, and DynamoDB-based locking is deprecated as of 1.11. A lock table would be a second
    # resource, a second failure mode, and a second line on the bill for a mechanism S3 now
    # provides natively.
    use_lockfile = true
  }
}
