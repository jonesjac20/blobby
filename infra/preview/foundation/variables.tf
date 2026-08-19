variable "region" {
  description = "Must match infra/prod (us-east-1). Preview tasks land in that VPC."
  type        = string
  default     = "us-east-1"
}

variable "name" {
  description = "Name tag / resource prefix for preview foundation (cluster, SG, roles)."
  type        = string
  default     = "blobby-preview"
}

variable "prod_vpc_name" {
  description = "Name tag on the existing production VPC and the public subnet suffix."
  type        = string
  default     = "blobby-prod"
}

variable "github_repository" {
  description = "owner/repo. OIDC trust is limited to pull_request jobs of preview.yml in this repo."
  type        = string
  default     = "jonesjac20/blobby"
}
