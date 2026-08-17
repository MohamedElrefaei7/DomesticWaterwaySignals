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

# ------------------------------------------------------------------------------------------
# Phase 11 — backups, monitoring, budget
# ------------------------------------------------------------------------------------------

variable "project_name" {
  description = <<-EOT
    Name prefix for resources whose names must be globally or account-unique. Used to build the
    backup bucket name together with the account id, which is read from a data source rather
    than written down.
  EOT
  type        = string
  default     = "domestic-waterway-signals"
}

variable "domain_name" {
  description = "Public hostname the external health check probes. Must resolve to the EIP."
  type        = string
  default     = "bargeanalysis.com"
}

variable "alert_email" {
  description = <<-EOT
    Address subscribed to the alarm topic. NO DEFAULT: an alert destination nobody chose is an
    alert nobody reads. Set it in terraform.tfvars, which is git-ignored.

    An SNS email subscription is created PENDING and delivers nothing until the confirmation
    link is clicked. That is a Theme 1 shape — the alarm reports as configured while its only
    delivery path is inert — so confirm it and check the subscription ARN is not the literal
    string `PendingConfirmation`.
  EOT
  type        = string
}

variable "monthly_budget_usd" {
  description = <<-EOT
    Monthly cost threshold for the budget alarm. There is a running instance, an EIP and an EBS
    volume billing continuously, and two S3 buckets joining them in this phase.
  EOT
  type        = number
  default     = 25
}
