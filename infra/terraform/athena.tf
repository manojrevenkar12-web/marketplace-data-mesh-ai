# Athena workgroups per purpose. The scan cutoff is the same limit the platform checks before a query runs
# (config/platform.yaml query.bytes_limit), enforced by Athena itself.

resource "aws_athena_workgroup" "purpose" {
  for_each      = var.athena_bytes_limit
  name          = "${var.name}-${each.key}"
  force_destroy = false

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true
    bytes_scanned_cutoff_per_query     = max(each.value, 10485760)
    engine_version {
      selected_engine_version = "Athena engine version 3"
    }
    result_configuration {
      output_location = "s3://${aws_s3_bucket.this["results"].bucket}/${each.key}/"
      encryption_configuration {
        encryption_option = "SSE_KMS"
        kms_key_arn       = aws_kms_key.lake.arn
      }
    }
  }
}
