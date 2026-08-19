data "aws_caller_identity" "current" {}

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

resource "aws_security_group" "preview" {
  name        = var.name
  description = "blobby PR preview Fargate: game :8000"
  vpc_id      = data.aws_vpc.prod.id

  ingress {
    description = "game"
    from_port   = 8000
    to_port     = 8000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "GHCR pull, CloudWatch logs"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = var.name
  }
}

resource "aws_ecs_cluster" "preview" {
  name = var.name

  setting {
    name  = "containerInsights"
    value = "disabled"
  }

  tags = {
    Name = var.name
  }
}

# First ECS CreateService in an account needs this. GitHub's role cannot
# create it, so foundation (laptop user) does it once.
resource "aws_iam_service_linked_role" "ecs" {
  aws_service_name = "ecs.amazonaws.com"
}

resource "aws_cloudwatch_log_group" "preview" {
  name              = "/ecs/${var.name}"
  retention_in_days = 7

  tags = {
    Name = var.name
  }
}

data "aws_iam_policy_document" "ecs_task_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${var.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_task_assume.json

  tags = {
    Name = "${var.name}-execution"
  }
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "tls_certificate" "github" {
  url = "https://token.actions.githubusercontent.com"
}

resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.github.certificates[0].sha1_fingerprint]

  tags = {
    Name = var.name
  }
}

data "aws_iam_policy_document" "gha_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # pull_request jobs use sub "...:pull_request", not ref:refs/heads/...
    # Repos created after 2026-07-15 (or opted in) use immutable IDs in sub.
    # Do not require job_workflow_ref: that claim is for reusable workflows
    # and is often absent, which makes IAM deny AssumeRoleWithWebIdentity.
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "repo:${var.github_repository}:pull_request",
        "repo:${split("/", var.github_repository)[0]}@*/${split("/", var.github_repository)[1]}@*:pull_request",
      ]
    }
  }
}

resource "aws_iam_role" "gha" {
  name               = "${var.name}-gha"
  assume_role_policy = data.aws_iam_policy_document.gha_assume.json

  tags = {
    Name = "${var.name}-gha"
  }
}

data "aws_iam_policy_document" "gha" {
  statement {
    sid = "EcsPreview"
    actions = [
      "ecs:*",
    ]
    resources = ["*"]
  }

  statement {
    sid = "PassExecutionRole"
    actions = [
      "iam:PassRole",
      "iam:GetRole",
    ]
    resources = [aws_iam_role.execution.arn]
  }

  statement {
    sid = "LookupNetworkAndLogs"
    actions = [
      "ec2:Describe*",
      "logs:DescribeLogGroups",
      "logs:ListTagsForResource",
    ]
    resources = ["*"]
  }

  statement {
    sid = "TerraformState"
    actions = [
      "s3:ListBucket",
      "s3:GetBucketLocation",
      "s3:GetBucketVersioning",
      "s3:GetEncryptionConfiguration",
    ]
    resources = [aws_s3_bucket.state.arn]
  }

  statement {
    sid = "TerraformStateObjects"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
    ]
    resources = ["${aws_s3_bucket.state.arn}/*"]
  }

  statement {
    sid    = "NeverTouchEc2Instances"
    effect = "Deny"
    actions = [
      "ec2:TerminateInstances",
      "ec2:StopInstances",
      "ec2:StartInstances",
      "ec2:RunInstances",
      "ec2:RebootInstances",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "gha" {
  name   = "${var.name}-gha"
  role   = aws_iam_role.gha.id
  policy = data.aws_iam_policy_document.gha.json
}

resource "aws_s3_bucket" "state" {
  bucket        = "blobby-preview-tfstate-${data.aws_caller_identity.current.account_id}"
  force_destroy = true

  tags = {
    Name = "${var.name}-tfstate"
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket = aws_s3_bucket.state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}
