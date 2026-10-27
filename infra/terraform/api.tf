# The platform API (catalog, ask, triage, review, access requests) on ECS Fargate behind an internal load
# balancer, with PostgreSQL for access requests, audit and feedback (IAM database authentication).

resource "aws_ecs_cluster" "this" {
  name = var.name
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/${var.name}/api"
  retention_in_days = 90
  kms_key_id        = aws_kms_key.lake.arn
}

resource "aws_security_group" "alb" {
  name        = "${var.name}-alb"
  description = "Internal load balancer for the platform API"
  vpc_id      = aws_vpc.this.id
  ingress {
    description = "HTTPS from the corporate network through the VPC"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }
  egress {
    description = "To the API tasks"
    from_port   = 8080
    to_port     = 8080
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }
}

resource "aws_security_group" "api" {
  name        = "${var.name}-api"
  description = "Platform API tasks"
  vpc_id      = aws_vpc.this.id
  ingress {
    description     = "From the load balancer"
    from_port       = 8080
    to_port         = 8080
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }
  egress {
    description = "AWS APIs and the database"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_lb" "api" {
  name                       = "${var.name}-api"
  internal                   = true
  load_balancer_type         = "application"
  security_groups            = [aws_security_group.alb.id]
  subnets                    = aws_subnet.private[*].id
  drop_invalid_header_fields = true
}

resource "aws_lb_target_group" "api" {
  name        = "${var.name}-api"
  port        = 8080
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.this.id
  health_check {
    path    = "/healthz"
    matcher = "200"
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.api.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.api_certificate_arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${var.name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "1024"
  memory                   = "2048"
  execution_role_arn       = aws_iam_role.api_execution.arn
  task_role_arn            = aws_iam_role.api_task.arn
  container_definitions = jsonencode([{
    name         = "api"
    image        = var.api_image
    essential    = true
    portMappings = [{ containerPort = 8080, protocol = "tcp" }]
    environment = [
      { name = "MESH_MODEL", value = "live" },
      { name = "MESH_ENGINE", value = "athena" },
      { name = "MESH_DB_HOST", value = aws_db_instance.api.address },
      { name = "MESH_DB_IAM_AUTH", value = "1" },
      { name = "AWS_REGION", value = var.region },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.api.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "api"
      }
    }
    readonlyRootFilesystem = true
  }])
}

resource "aws_ecs_service" "api" {
  name            = "${var.name}-api"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = 2
  launch_type     = "FARGATE"
  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.api.id]
    assign_public_ip = false
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8080
  }
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  depends_on = [aws_lb_listener.https]
}

resource "aws_security_group" "db" {
  name        = "${var.name}-db"
  description = "PostgreSQL for the platform API"
  vpc_id      = aws_vpc.this.id
  ingress {
    description     = "From the API tasks"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.api.id]
  }
}

resource "aws_db_subnet_group" "api" {
  name       = "${var.name}-api"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_instance" "api" {
  identifier                          = "${var.name}-api"
  engine                              = "postgres"
  engine_version                      = "16"
  instance_class                      = "db.t4g.small"
  allocated_storage                   = 20
  db_name                             = "mesh"
  username                            = "mesh_admin"
  manage_master_user_password         = true
  iam_database_authentication_enabled = true
  storage_encrypted                   = true
  kms_key_id                          = aws_kms_key.lake.arn
  db_subnet_group_name                = aws_db_subnet_group.api.name
  vpc_security_group_ids              = [aws_security_group.db.id]
  backup_retention_period             = 14
  deletion_protection                 = true
  skip_final_snapshot                 = false
  final_snapshot_identifier           = "${var.name}-api-final"
  multi_az                            = var.environment == "prod"
  auto_minor_version_upgrade          = true
  performance_insights_enabled        = true
  performance_insights_kms_key_id     = aws_kms_key.lake.arn
}
