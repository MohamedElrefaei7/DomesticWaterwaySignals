# Top-level aws_ebs_volume + aws_volume_attachment, never a nested ebs_block_device on
# aws_instance (CLAUDE.md § 8 / decision 1). A nested block has no independent lifecycle:
# replacing the instance destroys the volume with it, and lifecycle.prevent_destroy — a
# resource-level meta-argument — cannot be applied to a nested block at all. The instance is
# disposable; the data is not.

resource "aws_ebs_volume" "data" {
  availability_zone = var.availability_zone
  size              = var.data_volume_size_gb
  type              = "gp3"

  # Set now, not later: changing `encrypted` forces replacement, which is exactly the case
  # prevent_destroy below turns into a hard failure instead of silently doing the wrong thing.
  encrypted = true

  tags = {
    Name = "domestic-waterway-signals-data"
  }

  lifecycle {
    # If a future change forces replacement of this volume, apply must fail loudly rather than
    # replace it. The fix is to reconsider the change, never to delete this block.
    prevent_destroy = true
  }
}

resource "aws_volume_attachment" "data" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.data.id
  instance_id = aws_instance.main.id
}
