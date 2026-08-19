variable "region" {
  description = "Must match foundation / prod."
  type        = string
  default     = "us-east-1"
}

variable "image" {
  description = "Full image URI including unique pr-<n>-<sha> tag. Never latest."
  type        = string
}

variable "pr_number" {
  description = "GitHub pull request number. Names the ECS service and task family."
  type        = number
}

variable "cluster_name" {
  type    = string
  default = "blobby-preview"
}

variable "prod_vpc_name" {
  type    = string
  default = "blobby-prod"
}

variable "preview_sg_name" {
  type    = string
  default = "blobby-preview"
}

variable "execution_role_name" {
  type    = string
  default = "blobby-preview-execution"
}

variable "log_group_name" {
  type    = string
  default = "/ecs/blobby-preview"
}
