# The backup bucket, and the instance's scoped access to it.
#
# This bucket holds the only copy of the database that is not on one EBS volume in one AZ. Every
# decision below follows from that sentence.

data "aws_caller_identity" "current" {}

# Deterministic and globally unique without a `random_id`, whose state — if lost — orphans the
# bucket under a name nobody can reconstruct.
resource "aws_s3_bucket" "backups" {
  bucket = "${var.project_name}-backups-${data.aws_caller_identity.current.account_id}"

  tags = {
    Name = "${var.project_name}-backups"
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "backups" {
  bucket = aws_s3_bucket.backups.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id

  rule {
    apply_server_side_encryption_by_default {
      # SSE-S3, not SSE-KMS. A customer-managed key needs a key policy granting the instance role
      # kms:GenerateDataKey, and when it does not, the failure is an opaque AccessDenied on upload
      # that reads as an S3 permission problem. Adding a failure surface to the first thing that
      # has to work is the wrong trade; moving to KMS later is a small change.
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "backups" {
  bucket = aws_s3_bucket.backups.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "backups" {
  bucket     = aws_s3_bucket.backups.id
  depends_on = [aws_s3_bucket_public_access_block.backups]

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.backups.arn,
          "${aws_s3_bucket.backups.arn}/*",
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

# RETENTION IS A LIFECYCLE RULE, NOT A PRINCIPAL ACTION — and that is the whole reason the instance
# policy below grants no delete. S3 executes these itself; nothing holding the instance role can
# reach them. The tempting alternative is `s3:DeleteObject` "so the retention job can clean up",
# which puts retention in code you can read at the cost of letting anything with the instance role
# erase every backup.
#
# THIS IS THE OPPOSITE OF THE STATE BUCKET'S RULE, DELIBERATELY. Backup objects are large and a
# superseded one is dead weight; state object versions are kilobytes and each is a recovery point.
# See the inverse assertion in bootstrap/main.tf.
resource "aws_s3_bucket_lifecycle_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id

  # Versioning must exist before rules that talk about noncurrent versions.
  depends_on = [aws_s3_bucket_versioning.backups]

  rule {
    id     = "daily-expire-35-days"
    status = "Enabled"

    filter {
      prefix = "backups/daily/"
    }

    expiration {
      days = 35
    }

    # WITHOUT THIS, VERSIONING RETAINS EVERY OVERWRITTEN OBJECT FOREVER. Expiring the current
    # version only makes it noncurrent; the bytes stay and the bill arrives four months later.
    noncurrent_version_expiration {
      noncurrent_days = 7
    }
  }

  rule {
    id     = "monthly-expire-400-days"
    status = "Enabled"

    filter {
      prefix = "backups/monthly/"
    }

    # 400 days, not 365: "we can restore any month of the last year" stays true for a backup taken
    # on the first of a month and read on the last day of the twelfth month after it.
    expiration {
      days = 400
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

# ---------------------------------------------------------------------------------------------
# The instance's access to the bucket
# ---------------------------------------------------------------------------------------------
#
# A CUSTOMER-MANAGED POLICY, NOT AN INLINE ONE. iam.tf's own note says to add managed policies in
# the commit that needs them; this is that commit.
#
# NO DELETE ACTION OF ANY KIND, AND NO `s3:*`. The job writes objects and reads them back to
# verify; retention is the lifecycle rule above. `s3:GetObjectAttributes` is what makes the upload
# verification possible without downloading the object, and the monthly server-side copy needs
# GetObject + PutObject, both already here.
#
# SCOPED TO THIS BUCKET ONLY. It must not reach the Terraform state bucket: an instance that can
# write state is an instance that can lie about what infrastructure exists.
resource "aws_iam_policy" "backups" {
  name_prefix = "dws-backups-"
  description = "Write and verify database backups. No delete - retention is a lifecycle rule."

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ListBackupBucket"
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
        ]
        Resource = [aws_s3_bucket.backups.arn]
      },
      {
        Sid    = "ReadWriteBackupObjects"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject",
          "s3:GetObjectAttributes",
        ]
        Resource = ["${aws_s3_bucket.backups.arn}/*"]
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "backups" {
  role       = aws_iam_role.instance.name
  policy_arn = aws_iam_policy.backups.arn
}
