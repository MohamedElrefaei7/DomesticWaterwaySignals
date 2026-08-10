# SSM only (CLAUDE.md § 8 / decision 11). No S3 policy — the backup bucket doesn't exist yet
# (Phase 11). No inline admin policy "to make debugging easier." Add managed policies in the
# commit that needs them, not preemptively.
#
# name_prefix, not name: a fixed-name IAM resource that needs replacement fails on
# create-before-destroy because the old name is still taken.

resource "aws_iam_role" "instance" {
  name_prefix = "dws-instance-"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "instance" {
  name_prefix = "dws-instance-"
  role        = aws_iam_role.instance.name
}
