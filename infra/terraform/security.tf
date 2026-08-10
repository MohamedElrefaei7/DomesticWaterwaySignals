# Security group with no inline ingress/egress blocks (CLAUDE.md § 8 / decision 5). Each rule is
# its own resource so a widened CIDR or range shows up as its own diff and its own failing test,
# instead of being buried inside a list mutation on the security group itself.

resource "aws_security_group" "instance" {
  name_prefix = "domestic-waterway-signals-"
  description = "Domestic Waterway Signals - single instance security group"
  vpc_id      = aws_vpc.main.id

  tags = {
    Name = "domestic-waterway-signals"
  }

  lifecycle {
    create_before_destroy = true
  }
}

# Ingress is an allowlist of exactly three ports (CLAUDE.md § 8 / decision 4): SSH from the
# admin CIDR only, HTTP and HTTPS from anywhere. Postgres is never exposed - timescaledb is
# reachable only over the Compose network.

resource "aws_vpc_security_group_ingress_rule" "ssh_admin" {
  security_group_id = aws_security_group.instance.id
  description       = "SSH from admin CIDR only"
  cidr_ipv4         = var.ssh_admin_cidr
  from_port         = 22
  to_port           = 22
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "http" {
  security_group_id = aws_security_group.instance.id
  description       = "HTTP (redirects to HTTPS at Caddy)"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "https" {
  security_group_id = aws_security_group.instance.id
  description       = "HTTPS"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

# Explicit egress is mandatory (CLAUDE.md § 8 / decision 3). Terraform's security-group rule
# resources treat the rule set as authoritative and revoke the AWS API's default allow-all
# egress. Without this, the instance never reaches the SSM endpoints, never registers, and
# Session Manager - the recovery path - is dead before it's needed.
resource "aws_vpc_security_group_egress_rule" "all_outbound" {
  security_group_id = aws_security_group.instance.id
  description       = "All outbound - required for SSM, package fetches, and API calls to USGS/USDA"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}
