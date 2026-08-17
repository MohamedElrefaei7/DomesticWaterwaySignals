# The Terraform state bucket, and nothing else.
#
# THIS CONFIGURATION'S OWN STATE STAYS LOCAL, DELIBERATELY. A bucket cannot hold the state that
# describes it: the backend has to exist before the configuration that uses it can initialise.
# The obvious instinct on finding a local `terraform.tfstate` in this directory is to "fix" it by
# adding a backend block pointing at the bucket next to it. Do not. That is a circular dependency
# that surfaces as an unrecoverable `terraform init` the first time the state is needed.
#
# THE LOCAL STATE HERE IS DISPOSABLE. It describes one bucket whose name is deterministic. If it
# is lost, the bucket is IMPORTED (`terraform import aws_s3_bucket.state <name>`), never
# recreated — and losing it costs nothing else, because nothing in this directory carries data.
# That is the entire reason the state bucket is bootstrapped separately rather than folded into
# the main configuration: it makes the one piece of state that cannot be remote also the one
# piece whose loss does not matter.
#
# Applied once, by a human, from this directory. It is not part of the main configuration's plan.

terraform {
  required_version = ">= 1.10.0, < 2.0.0" # 1.10 is where `use_lockfile` arrives

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }
  }

  # NO `backend` BLOCK. See above. This is the omission that makes the rest work.
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  description = "Region for the state bucket. Must match the `region` in ../backend.tf."
  type        = string
  default     = "us-east-1"
}

variable "state_bucket_name" {
  description = <<-EOT
    Name of the Terraform state bucket. Must be byte-identical to the `bucket` value in
    ../backend.tf — a backend block cannot interpolate, so the string is written twice and
    tests/terraform/test_backend_hcl.py asserts the two copies agree.
  EOT
  type        = string
  default     = "domestic-waterway-signals-tfstate"
}

resource "aws_s3_bucket" "state" {
  bucket = var.state_bucket_name

  tags = {
    Name = "domestic-waterway-signals-tfstate"
  }

  lifecycle {
    # The bucket holds the only record of what infrastructure exists. Destroying it is never the
    # right move, and an apply that proposes it must fail rather than proceed.
    prevent_destroy = true
  }
}

# VERSIONING IS THE RECOVERY PATH, not a nicety. A truncated or corrupted state write is a real
# failure mode of a shared backend — an interrupted upload, two applies racing a lock that was not
# held — and without versioning the previous good state is simply gone.
resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

# NO LIFECYCLE CONFIGURATION ON THIS BUCKET, AND THAT IS THE DECISION.
#
# Part 3's backup bucket expires noncurrent versions, because backup objects are large and
# superseded ones are dead weight. The reflex after writing that rule is to apply the same one
# here for consistency. Do not: each state object version is a RECOVERY POINT, they are kilobytes,
# and the day one is wanted is the day somebody is recovering from a bad apply and reaching for
# the version from before it. Retention here is indefinite on purpose.
# `test_state_bucket_has_no_lifecycle_expiry` guards this as an inverse assertion.

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      # SSE-S3, matching the backup bucket in Part 3. A customer-managed KMS key would need a key
      # policy granting every principal that touches state `kms:GenerateDataKey`, and when it
      # does not, the failure is an opaque AccessDenied that reads as an S3 permission problem.
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket = aws_s3_bucket.state.id

  # All four. Three of four leaves a route to public exposure of the file that describes every
  # resource in the account, in cleartext.
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Belt and braces over the default. Two lines, and the kind of thing an auditor asks about.
resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id

  # Ordering matters: applying a bucket policy before `block_public_policy` is in force leaves a
  # window in which a policy could be public. The dependency closes it.
  depends_on = [aws_s3_bucket_public_access_block.state]

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.state.arn,
          "${aws_s3_bucket.state.arn}/*",
        ]
        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      }
    ]
  })
}

output "state_bucket_name" {
  description = "Paste-ready confirmation that this matches ../backend.tf's `bucket`."
  value       = aws_s3_bucket.state.id
}
