# Amazon MWAA runs the generated daily DAG (dags/mesh_daily.py) with the platform package installed from the
# requirements file. Private network access only; logs to CloudWatch.

resource "aws_security_group" "mwaa" {
  name        = "${var.name}-mwaa"
  description = "MWAA workers and web server"
  vpc_id      = aws_vpc.this.id
  ingress {
    description = "Self-referencing: MWAA components talk to each other"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    self        = true
  }
  egress {
    description = "Out to AWS APIs through endpoints and NAT"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_mwaa_environment" "this" {
  name                  = "${var.name}-${var.environment}"
  airflow_version       = var.airflow_version
  environment_class     = "mw1.small"
  max_workers           = 4
  execution_role_arn    = aws_iam_role.mwaa.arn
  source_bucket_arn     = aws_s3_bucket.this["airflow"].arn
  dag_s3_path           = "dags/"
  requirements_s3_path  = "requirements.txt"
  webserver_access_mode = "PRIVATE_ONLY"
  kms_key               = aws_kms_key.lake.arn

  airflow_configuration_options = {
    "core.default_task_retries"       = "2"
    "celery.worker_autoscale"         = "4,1"
    "core.dag_file_processor_timeout" = "150"
  }

  network_configuration {
    security_group_ids = [aws_security_group.mwaa.id]
    subnet_ids         = aws_subnet.private[*].id
  }

  logging_configuration {
    dag_processing_logs {
      enabled   = true
      log_level = "WARNING"
    }
    scheduler_logs {
      enabled   = true
      log_level = "WARNING"
    }
    task_logs {
      enabled   = true
      log_level = "INFO"
    }
    webserver_logs {
      enabled   = true
      log_level = "WARNING"
    }
    worker_logs {
      enabled   = true
      log_level = "WARNING"
    }
  }
}
