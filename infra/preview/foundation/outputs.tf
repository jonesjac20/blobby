output "preview_role_arn" {
  description = "GitHub Actions variable PREVIEW_ROLE_ARN."
  value       = aws_iam_role.gha.arn
}

output "tf_state_bucket" {
  description = "GitHub Actions variable PREVIEW_TF_STATE_BUCKET."
  value       = aws_s3_bucket.state.id
}

output "cluster_name" {
  value = aws_ecs_cluster.preview.name
}

output "security_group_id" {
  value = aws_security_group.preview.id
}

output "vpc_id" {
  description = "Existing prod VPC the preview SG and tasks use."
  value       = data.aws_vpc.prod.id
}

output "subnet_id" {
  value = data.aws_subnet.public.id
}

output "github_variables" {
  description = "Paste these as repo Actions Variables (not Secrets)."
  value       = <<-EOT
    PREVIEW_ROLE_ARN=${aws_iam_role.gha.arn}
    PREVIEW_TF_STATE_BUCKET=${aws_s3_bucket.state.id}
  EOT
}
