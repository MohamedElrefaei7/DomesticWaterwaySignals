terraform {
  # Pinned so a `terraform init` months from now resolves the same provider generation
  # instead of silently picking up a new AWS provider major/minor. See CLAUDE.md § 5 —
  # this is the same "every image tag is pinned" lesson applied to the provider itself.
  required_version = ">= 1.9.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }
  }
}

provider "aws" {
  region = var.aws_region
}
