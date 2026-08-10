variable "aws_region" {
  description = "AWS region for every resource in this configuration."
  type        = string
  default     = "us-east-1"
}

variable "availability_zone" {
  description = <<-EOT
    Single AZ referenced by both the instance and the data volume, so they can never drift
    apart (CLAUDE.md § 8 / decision 7). No default: the AZ is a deliberate placement choice,
    not something a plan should silently default.
  EOT
  type        = string
}

variable "vpc_cidr_block" {
  description = "CIDR block for the purpose-built VPC (never the default VPC — CLAUDE.md § 8 / decision 10)."
  type        = string
  default     = "10.0.0.0/16"
}

variable "public_subnet_cidr_block" {
  description = "CIDR block for the single public subnet."
  type        = string
  default     = "10.0.1.0/24"
}

variable "ssh_admin_cidr" {
  description = <<-EOT
    CIDR allowed to reach port 22. No default on purpose: a plan without an explicit value
    must fail rather than quietly choosing something. The validation below rejects anything
    broader than a /24, so a widened value fails at plan time instead of applying.
  EOT
  type        = string

  validation {
    condition     = tonumber(split("/", var.ssh_admin_cidr)[1]) >= 24
    error_message = "ssh_admin_cidr must be a /24 or narrower (a longer prefix length)."
  }
}

variable "ami_id" {
  description = <<-EOT
    Pinned AMI ID. Never resolved via a `most_recent = true` data source — see CLAUDE.md § 8 /
    decision 6. Set the current Ubuntu 24.04 LTS AMI ID for var.aws_region in terraform.tfvars.
  EOT
  type        = string
}

variable "instance_type" {
  description = "EC2 instance type for the single application/worker/db host."
  type        = string
  default     = "t3.small"
}

variable "root_volume_size_gb" {
  description = "Size of the disposable root volume (gp3)."
  type        = number
  default     = 20
}

variable "data_volume_size_gb" {
  description = "Size of the separate, persistent data volume (gp3)."
  type        = number
  default     = 50
}
