# AMI is a pinned ID, never resolved via a most_recent data source (CLAUDE.md § 8 / decision 6).
# `most_recent = true` is `latest` on a database image wearing different clothes: Canonical
# publishes a new AMI, and an unrelated `terraform apply` months later plans to replace the
# instance. Do not add lifecycle.ignore_changes = [ami] either — that hides real drift instead
# of preventing it.

resource "aws_instance" "main" {
  ami                    = var.ami_id
  instance_type          = var.instance_type
  availability_zone      = var.availability_zone
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.instance.id]
  iam_instance_profile   = aws_iam_instance_profile.instance.name

  # No auto-assigned public IP here either — belt and suspenders with the subnet setting. The
  # EIP below is the only public address this instance ever has.
  associate_public_ip_address = false

  # IMDSv2 required (CLAUDE.md § a8 / decision 8). Default is "optional", which leaves IMDSv1
  # reachable and turns any SSRF in the API container into instance-credential theft. Hop limit
  # 2, not 1, because containers reach the metadata service through an extra network hop.
  metadata_options {
    http_tokens                 = "required"
    http_endpoint               = "enabled"
    http_put_response_hop_limit = 2
  }

  # Root volume is disposable and says so (§ 8 / decision 12) — the counterpart to the
  # data volume being the opposite. Instance replacement is a supported operation, not an
  # emergency. gp3, not gp2: cheaper per GB, 3000 baseline IOPS without paying for size.
  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_size_gb
    delete_on_termination = true
  }

  tags = {
    Name = "domestic-waterway-signals"
  }
  
  lifecycle {
    # Once the EIP associates, AWS reports this interface as having a public IP on every
    # refresh, so live state reads `true` regardless of this config's `false`. That mismatch
    # forces replacement on every subsequent plan unless it's ignored here — this is not
    # drift, it's an artifact of how associate_public_ip_address is reported post-EIP-attach.
    ignore_changes = [associate_public_ip_address]
  }
}

resource "aws_eip" "main" {
  domain   = "vpc"
  instance = aws_instance.main.id

  tags = {
    Name = "domestic-waterway-signals"
  }

  lifecycle {
    # Once DNS points at this address (Phase 10), releasing it is as destructive as losing the
    # data volume — the address is gone and cannot be reclaimed.
    prevent_destroy = true
  }

  depends_on = [aws_internet_gateway.main]
}
