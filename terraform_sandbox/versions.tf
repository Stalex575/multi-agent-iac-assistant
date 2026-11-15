# Fixed provider block injected into every generated config.
# The generator is instructed NOT to emit terraform{} / provider{} blocks;
# keeping the provider pinned here makes `terraform validate` reproducible offline.
terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}
