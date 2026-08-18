data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "prod" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name = var.name
  }
}

resource "aws_internet_gateway" "prod" {
  vpc_id = aws_vpc.prod.id

  tags = {
    Name = var.name
  }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.prod.id
  cidr_block              = "10.0.1.0/24"
  availability_zone       = sort(data.aws_availability_zones.available.names)[0]
  map_public_ip_on_launch = true

  tags = {
    Name = "${var.name}-public"
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.prod.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.prod.id
  }

  tags = {
    Name = "${var.name}-public"
  }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

resource "aws_security_group" "prod" {
  name        = var.name
  description = "blobby production: game :8000, optional SSH"
  vpc_id      = aws_vpc.prod.id

  ingress {
    description = "game"
    from_port   = 8000
    to_port     = 8000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  dynamic "ingress" {
    for_each = local.ssh_enabled ? [var.ssh_cidr] : []
    content {
      description = "ssh from operator"
      from_port   = 22
      to_port     = 22
      protocol    = "tcp"
      cidr_blocks = [ingress.value]
    }
  }

  egress {
    description = "apt, GHCR, GitHub runner"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = var.name
  }
}

data "aws_iam_policy_document" "ec2_assume" {
  count = var.enable_ssm ? 1 : 0

  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ssm" {
  count              = var.enable_ssm ? 1 : 0
  name               = "${var.name}-ssm"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume[0].json
}

resource "aws_iam_role_policy_attachment" "ssm" {
  count      = var.enable_ssm ? 1 : 0
  role       = aws_iam_role.ssm[0].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "ssm" {
  count = var.enable_ssm ? 1 : 0
  name  = "${var.name}-ssm"
  role  = aws_iam_role.ssm[0].name
}

resource "aws_instance" "prod" {
  ami                         = data.aws_ami.ubuntu.id
  instance_type               = var.instance_type
  subnet_id                   = aws_subnet.public.id
  vpc_security_group_ids      = [aws_security_group.prod.id]
  key_name                    = var.key_name
  iam_instance_profile        = var.enable_ssm ? aws_iam_instance_profile.ssm[0].name : null
  associate_public_ip_address = true
  # replace() so Windows CRLF in user_data.sh cannot break cloud-init on Ubuntu.
  user_data                   = replace(file("${path.module}/user_data.sh"), "\r\n", "\n")
  user_data_replace_on_change = false

  root_block_device {
    volume_size = 16
    volume_type = "gp3"
  }

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  tags = {
    Name = var.name
  }

  lifecycle {
    precondition {
      condition     = var.enable_ssm || (local.ssh_enabled && var.key_name != null)
      error_message = "Set ssh_cidr and key_name for SSH, or enable_ssm = true. Otherwise the instance has no login path."
    }
  }
}

resource "aws_eip" "prod" {
  instance = aws_instance.prod.id
  domain   = "vpc"

  tags = {
    Name = var.name
  }
}
