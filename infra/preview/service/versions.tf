terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # bucket and key come from `terraform init -backend-config=...` in preview.yml.
  backend "s3" {
    region = "us-east-1"
  }
}
