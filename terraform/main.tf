# =============================================================================
# terraform/main.tf
# Provisions AWS infrastructure for the Telegram Deployment Bot:
#   - VPC with public subnet
#   - EC2 instance (bot host)
#   - Security Group (SSH + HTTPS only)
#   - ECR repository for Docker images
#   - IAM role for EC2 with ECR pull permissions
#   - IAM OIDC role for GitHub Actions (no long-lived keys!)
# =============================================================================

terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Store state in S3 — never local in production
  backend "s3" {
    bucket = "myorg-terraform-state"
    key    = "deploy-bot/terraform.tfstate"
    region = "us-east-1"
    encrypt = true
    # Enable DynamoDB locking to prevent concurrent applies
    dynamodb_table = "terraform-state-lock"
  }
}

provider "aws" {
  region = var.aws_region
}

# ── Variables ──────────────────────────────────────────────────────────────────
variable "aws_region"        { default = "us-east-1" }
variable "environment"       { default = "production" }
variable "project_name"      { default = "deploy-bot" }
variable "ec2_instance_type" { default = "t3.small" }
variable "ssh_public_key"    { description = "SSH public key for EC2 access" }
variable "github_org"        { description = "GitHub organization name" }
variable "github_repo"       { description = "GitHub repository name" }

# ── Data Sources ───────────────────────────────────────────────────────────────
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"]  # Canonical (Ubuntu)

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-22.04-amd64-*"]
  }
}

data "aws_caller_identity" "current" {}

# ── Networking ─────────────────────────────────────────────────────────────────
resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags = { Name = "${var.project_name}-vpc" }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.1.0/24"
  map_public_ip_on_launch = true
  tags = { Name = "${var.project_name}-public-subnet" }
}

resource "aws_internet_gateway" "gw" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${var.project_name}-igw" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.gw.id
  }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# ── Security Group ─────────────────────────────────────────────────────────────
resource "aws_security_group" "bot_sg" {
  name        = "${var.project_name}-sg"
  description = "Deploy bot security group — minimal access"
  vpc_id      = aws_vpc.main.id

  # SSH — restrict to your IP in production (not 0.0.0.0/0)
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "SSH access — RESTRICT TO YOUR IP IN PRODUCTION"
  }

  # HTTPS for Telegram webhook (if using webhook mode)
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTPS for Telegram webhooks"
  }

  # All outbound allowed (bot needs to reach Telegram API + GitHub)
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project_name}-sg" }
}

# ── SSH Key Pair ───────────────────────────────────────────────────────────────
resource "aws_key_pair" "deploy" {
  key_name   = "${var.project_name}-key"
  public_key = var.ssh_public_key
}

# ── IAM Role for EC2 (ECR pull + CloudWatch logs) ─────────────────────────────
resource "aws_iam_role" "ec2_role" {
  name = "${var.project_name}-ec2-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "ec2_ecr_policy" {
  name = "ECRPullPolicy"
  role = aws_iam_role.ec2_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = [
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "ecr:GetAuthorizationToken"
        ]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      }
    ]
  })
}

resource "aws_iam_instance_profile" "ec2_profile" {
  name = "${var.project_name}-ec2-profile"
  role = aws_iam_role.ec2_role.name
}

# ── ECR Repository ─────────────────────────────────────────────────────────────
resource "aws_ecr_repository" "app" {
  name                 = "myapp"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true  # Automatically scan for CVEs on every push
  }

  lifecycle {
    prevent_destroy = true  # Don't accidentally nuke your image registry
  }
}

resource "aws_ecr_lifecycle_policy" "cleanup" {
  repository = aws_ecr_repository.app.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep only 10 images per environment prefix"
      selection = {
        tagStatus   = "tagged"
        tagPrefixList = ["staging-", "production-"]
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}

# ── EC2 Instance ───────────────────────────────────────────────────────────────
resource "aws_instance" "bot" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.ec2_instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.bot_sg.id]
  key_name               = aws_key_pair.deploy.key_name
  iam_instance_profile   = aws_iam_instance_profile.ec2_profile.name

  root_block_device {
    volume_size = 30
    volume_type = "gp3"
    encrypted   = true
  }

  # Bootstrap script — installs Docker and starts the bot
  user_data = <<-EOF
    #!/bin/bash
    apt-get update -y
    apt-get install -y docker.io docker-compose awscli git
    systemctl enable docker
    systemctl start docker
    usermod -aG docker ubuntu

    # Create deploy user (used by bot SSH commands)
    useradd -m -s /bin/bash deploy
    usermod -aG docker deploy

    # Create working directories
    mkdir -p /opt/myapp /var/lib/deploybot /var/log/deploybot
    chown deploy:deploy /opt/myapp /var/lib/deploybot /var/log/deploybot
  EOF

  tags = {
    Name        = "${var.project_name}"
    Environment = var.environment
    ManagedBy   = "Terraform"
  }
}

# ── IAM OIDC Role for GitHub Actions (no long-lived AWS keys!) ────────────────
data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

resource "aws_iam_role" "github_actions" {
  name = "GitHubActions-${var.project_name}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = data.aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        }
        StringLike = {
          "token.actions.githubusercontent.com:sub" =
            "repo:${var.github_org}/${var.github_repo}:*"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "github_actions_policy" {
  name = "GitHubActionsDeployPolicy"
  role = aws_iam_role.github_actions.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = [
          "ecr:GetAuthorizationToken",
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
          "ecr:PutImage"
        ]
        Resource = "*"
      }
    ]
  })
}

# ── Outputs ────────────────────────────────────────────────────────────────────
output "bot_public_ip"        { value = aws_instance.bot.public_ip }
output "ecr_repository_url"   { value = aws_ecr_repository.app.repository_url }
output "github_actions_role"  { value = aws_iam_role.github_actions.arn }
