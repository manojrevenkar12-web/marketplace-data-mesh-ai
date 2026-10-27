# The pipelines publish EMF metrics per run (namespace Mesh): failed runs and late products page the platform
# on-call; every alarm links to its runbook section.

resource "aws_sns_topic" "alarms" {
  name              = "${var.name}-alarms"
  kms_master_key_id = aws_kms_key.lake.id
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alarm_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

resource "aws_cloudwatch_metric_alarm" "runs_failed" {
  alarm_name          = "${var.name}-pipeline-runs-failed"
  alarm_description   = "A data product run failed or was quarantined; open the triage in the console. docs/RUNBOOK.md#failed-run"
  namespace           = "Mesh"
  metric_name         = "RunsFailed"
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_metric_alarm" "products_late" {
  alarm_name          = "${var.name}-products-late"
  alarm_description   = "A data product missed its freshness SLA. docs/RUNBOOK.md#late-product"
  namespace           = "Mesh"
  metric_name         = "ProductsLate"
  statistic           = "Maximum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_metric_alarm" "assistant_rejections" {
  alarm_name          = "${var.name}-assistant-rejected-queries"
  alarm_description   = "Many assistant queries were refused for asking beyond the user's grants: possible instructions in metadata. docs/RUNBOOK.md#assistant-rejections"
  namespace           = "Mesh"
  metric_name         = "AssistantQueriesDenied"
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 5
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_metric_alarm" "athena_scanned" {
  for_each            = aws_athena_workgroup.purpose
  alarm_name          = "${var.name}-athena-${each.key}-scanned"
  alarm_description   = "Athena scanned more than usual in the ${each.key} workgroup. docs/RUNBOOK.md#cost"
  namespace           = "AWS/Athena"
  metric_name         = "ProcessedBytes"
  dimensions          = { WorkGroup = each.value.name }
  statistic           = "Sum"
  period              = 86400
  evaluation_periods  = 1
  threshold           = 2e12
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}
