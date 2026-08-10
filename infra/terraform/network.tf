# Purpose-built VPC, not the default VPC (CLAUDE.md § 8 / decision 10). The default VPC's
# default security group carries an allow-all-from-itself rule that no test reading this repo
# could ever see — the tests would be green while the hole existed. Everything network-shaped
# lives here, in the repo, where it can be read and tested.

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr_block
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "domestic-waterway-signals"
  }
}

resource "aws_subnet" "public" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = var.public_subnet_cidr_block
  availability_zone = var.availability_zone

  # No auto-assigned public IP (CLAUDE.md § 8 / decision 9). The EIP is the instance's only
  # public address — an auto-assigned one works today and silently changes on the next
  # stop/start, producing an outage that looks like a DNS problem.
  map_public_ip_on_launch = false

  tags = {
    Name = "domestic-waterway-signals-public"
  }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "domestic-waterway-signals"
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = {
    Name = "domestic-waterway-signals-public"
  }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}
