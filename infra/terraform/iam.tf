# One IAM role per access principal (assumed through SSO permission sets or CI), the role Lake Formation uses
# to reach the buckets, the MWAA execution role and the API task role.

data "aws_iam_policy_document" "principal_trust" {
  for_each = var.principals
  statement {
    actions = ["sts:AssumeRole", "sts:TagSession"]
    principals {
      type        = "AWS"
      identifiers = lookup(var.principal_trust, each.key, ["arn:${local.partition}:iam::${local.account_id}:root"])
    }
  }
}

resource "aws_iam_role" "principal" {
  for_each             = var.principals
  name                 = "${var.name}-${each.key}"
  description          = each.value.description
  assume_role_policy   = data.aws_iam_policy_document.principal_trust[each.key].json
  max_session_duration = 3600
}

# What every principal needs to query through Athena; which data it may read is Lake Formation's decision.
data "aws_iam_policy_document" "query" {
  statement {
    actions   = ["athena:StartQueryExecution", "athena:GetQueryExecution", "athena:GetQueryResults", "athena:StopQueryExecution", "athena:GetWorkGroup"]
    resources = [for w in aws_athena_workgroup.purpose : w.arn]
  }
  statement {
    actions   = ["glue:GetDatabase", "glue:GetDatabases", "glue:GetTable", "glue:GetTables", "glue:GetPartition", "glue:GetPartitions"]
    resources = ["*"]
  }
  statement {
    actions   = ["lakeformation:GetDataAccess"]
    resources = ["*"]
  }
  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.this["results"].arn, "${aws_s3_bucket.this["results"].arn}/*"]
  }
  statement {
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.lake.arn]
  }
}

resource "aws_iam_policy" "query" {
  name   = "${var.name}-query"
  policy = data.aws_iam_policy_document.query.json
}

resource "aws_iam_role_policy_attachment" "principal_query" {
  for_each   = aws_iam_role.principal
  role       = each.value.name
  policy_arn = aws_iam_policy.query.arn
}

# Lake Formation's own role for the registered bucket locations
data "aws_iam_policy_document" "lakeformation_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lakeformation.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lake_access" {
  name               = "${var.name}-lake-access"
  assume_role_policy = data.aws_iam_policy_document.lakeformation_trust.json
}

data "aws_iam_policy_document" "lake_access" {
  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
    resources = flatten([for d in var.domains : [aws_s3_bucket.this["lake-${d}"].arn, "${aws_s3_bucket.this["lake-${d}"].arn}/*"]])
  }
  statement {
    actions   = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.lake.arn]
  }
}

resource "aws_iam_role_policy" "lake_access" {
  role   = aws_iam_role.lake_access.id
  policy = data.aws_iam_policy_document.lake_access.json
}

# MWAA execution role: reads the landing zone, writes the lake through Lake Formation, logs and metrics.
data "aws_iam_policy_document" "mwaa_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["airflow.amazonaws.com", "airflow-env.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "mwaa" {
  name               = "${var.name}-mwaa-execution"
  assume_role_policy = data.aws_iam_policy_document.mwaa_trust.json
}

data "aws_iam_policy_document" "mwaa" {
  statement {
    actions   = ["s3:GetObject", "s3:ListBucket", "s3:GetBucketLocation", "s3:GetBucketPublicAccessBlock", "s3:GetAccountPublicAccessBlock"]
    resources = [aws_s3_bucket.this["airflow"].arn, "${aws_s3_bucket.this["airflow"].arn}/*", aws_s3_bucket.this["landing"].arn, "${aws_s3_bucket.this["landing"].arn}/*"]
  }
  statement {
    actions   = ["glue:GetTable", "glue:GetPartition", "glue:GetPartitions", "glue:BatchCreatePartition", "glue:CreatePartition", "glue:UpdatePartition", "glue:BatchDeletePartition"]
    resources = ["*"]
  }
  statement {
    actions   = ["lakeformation:GetDataAccess"]
    resources = ["*"]
  }
  statement {
    actions   = ["logs:CreateLogStream", "logs:CreateLogGroup", "logs:PutLogEvents", "logs:GetLogEvents", "logs:GetLogRecord", "logs:GetLogGroupFields", "logs:GetQueryResults", "logs:DescribeLogGroups"]
    resources = ["arn:${local.partition}:logs:${var.region}:${local.account_id}:log-group:airflow-${var.name}-*"]
  }
  statement {
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
  }
  statement {
    actions   = ["sqs:ChangeMessageVisibility", "sqs:DeleteMessage", "sqs:GetQueueAttributes", "sqs:GetQueueUrl", "sqs:ReceiveMessage", "sqs:SendMessage"]
    resources = ["arn:${local.partition}:sqs:${var.region}:*:airflow-celery-*"]
  }
  statement {
    actions   = ["kms:Decrypt", "kms:DescribeKey", "kms:GenerateDataKey*", "kms:Encrypt"]
    resources = [aws_kms_key.lake.arn]
  }
}

resource "aws_iam_role_policy" "mwaa" {
  role   = aws_iam_role.mwaa.id
  policy = data.aws_iam_policy_document.mwaa.json
}

# API task role: queries as the requesting principal (it assumes that principal's role with the user as the
# session name), and calls Claude on Bedrock through the EU inference profiles only.
data "aws_iam_policy_document" "ecs_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "api_task" {
  name               = "${var.name}-api-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_trust.json
}

resource "aws_iam_role" "api_execution" {
  name               = "${var.name}-api-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_trust.json
}

resource "aws_iam_role_policy_attachment" "api_execution" {
  role       = aws_iam_role.api_execution.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "api_task" {
  statement {
    sid       = "ActAsTheRequestingPrincipal"
    actions   = ["sts:AssumeRole", "sts:TagSession"]
    resources = [for r in aws_iam_role.principal : r.arn]
  }
  statement {
    sid     = "ClaudeInTheEuOnly"
    actions = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream", "bedrock:Converse", "bedrock:ConverseStream"]
    resources = [
      "arn:${local.partition}:bedrock:${var.region}:${local.account_id}:inference-profile/eu.anthropic.*",
      "arn:${local.partition}:bedrock:eu-*::foundation-model/anthropic.*",
    ]
  }
  statement {
    actions   = ["rds-db:connect"]
    resources = ["arn:${local.partition}:rds-db:${var.region}:${local.account_id}:dbuser:${aws_db_instance.api.resource_id}/mesh_api"]
  }
  statement {
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "api_task" {
  role   = aws_iam_role.api_task.id
  policy = data.aws_iam_policy_document.api_task.json
}
