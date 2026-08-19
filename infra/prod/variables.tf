variable "region" {
  description = "AWS region for the production EC2. Keep this the same for CLI, AMI, and apply."
  type        = string
  default     = "us-east-1"
}

variable "instance_type" {
  description = "Instance size. t3.small (2 GB) is required for the always-on 17-bot lobby; t3.micro OOMs."
  type        = string
  default     = "t3.small"
}

variable "key_name" {
  description = "Existing EC2 key pair name for SSH. Create it outside Terraform (see terraform.tfvars.example). Null skips a key."
  type        = string
  default     = null
}

variable "ssh_cidr" {
  description = "If set, open 22/tcp from this CIDR only (your home IP /32). Null leaves SSH closed."
  type        = string
  default     = null
}

variable "enable_ssm" {
  description = "Attach AmazonSSMManagedInstanceCore so you can Session Manager in without port 22. Needs extra IAM on the Terraform user (iam-policy.json)."
  type        = bool
  default     = false
}

variable "name" {
  description = "Name tag / resource prefix."
  type        = string
  default     = "blobby-prod"
}

locals {
  ssh_enabled = var.ssh_cidr != null && var.ssh_cidr != ""
}
