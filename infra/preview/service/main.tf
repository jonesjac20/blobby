data "aws_ecs_cluster" "preview" {
  cluster_name = var.cluster_name
}

data "aws_vpc" "prod" {
  tags = {
    Name = var.prod_vpc_name
  }
}

data "aws_subnet" "public" {
  vpc_id = data.aws_vpc.prod.id

  tags = {
    Name = "${var.prod_vpc_name}-public"
  }
}

data "aws_security_group" "preview" {
  name   = var.preview_sg_name
  vpc_id = data.aws_vpc.prod.id
}

data "aws_iam_role" "execution" {
  name = var.execution_role_name
}

data "aws_cloudwatch_log_group" "preview" {
  name = var.log_group_name
}

locals {
  family = "blobby-pr-${var.pr_number}"
}

resource "aws_ecs_task_definition" "preview" {
  family                   = local.family
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "1024"
  memory                   = "2048"
  execution_role_arn       = data.aws_iam_role.execution.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    {
      name      = "game"
      image     = var.image
      essential = true
      portMappings = [
        {
          containerPort = 8000
          hostPort      = 8000
          protocol      = "tcp"
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = data.aws_cloudwatch_log_group.preview.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "pr-${var.pr_number}"
        }
      }
    },
    {
      name      = "bots"
      image     = var.image
      essential = false
      dependsOn = [
        {
          containerName = "game"
          condition     = "START"
        }
      ]
      command = [
        "sh",
        "-c",
        "while true; do python -m bots.simple_bot --url http://127.0.0.1:8000/ws --name bot --count 24; sleep 2; done"
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = data.aws_cloudwatch_log_group.preview.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "pr-${var.pr_number}-bots"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "preview" {
  name             = local.family
  cluster          = data.aws_ecs_cluster.preview.arn
  task_definition  = aws_ecs_task_definition.preview.arn
  desired_count    = 1
  launch_type      = "FARGATE"
  platform_version = "LATEST"

  force_new_deployment               = true
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  network_configuration {
    subnets          = [data.aws_subnet.public.id]
    security_groups  = [data.aws_security_group.preview.id]
    assign_public_ip = true
  }

  tags = {
    Name = local.family
  }
}
