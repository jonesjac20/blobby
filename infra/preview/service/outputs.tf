output "service_name" {
  value = aws_ecs_service.preview.name
}

output "cluster_name" {
  value = data.aws_ecs_cluster.preview.cluster_name
}

output "task_definition_arn" {
  value = aws_ecs_task_definition.preview.arn
}
