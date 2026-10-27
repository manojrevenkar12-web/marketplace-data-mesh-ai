# One bucket per domain (the domain team's data products), a landing bucket for source drops, Athena results,
# and the MWAA bucket for DAGs. All private, KMS-encrypted, TLS-only, versioned.

locals {
  buckets = merge(
    { for d in var.domains : "lake-${d}" => "${var.name}-lake-${d}-${local.account_id}" },
    {
      landing = "${var.name}-landing-${local.account_id}"
      results = "${var.name}-athena-results-${local.account_id}"
      airflow = "${var.name}-airflow-${local.account_id}"
    },
  )
}

resource "aws_s3_bucket" "this" {
  for_each = local.buckets
  bucket   = each.value
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each                = aws_s3_bucket.this
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.lake.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  versioning_configuration {
    status = "Enabled"
  }
}

# staged partitions that never passed their checks are removed after a week
resource "aws_s3_bucket_lifecycle_configuration" "lake" {
  for_each = { for d in var.domains : d => aws_s3_bucket.this["lake-${d}"].id }
  bucket   = each.value
  rule {
    id     = "expire-staging"
    status = "Enabled"
    filter {
      prefix = "_staging/"
    }
    expiration {
      days = 7
    }
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "results" {
  bucket = aws_s3_bucket.this["results"].id
  rule {
    id     = "expire-results"
    status = "Enabled"
    filter {}
    expiration {
      days = 30
    }
  }
}

data "aws_iam_policy_document" "tls_only" {
  for_each = aws_s3_bucket.this
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      each.value.arn,
      "${each.value.arn}/*",
    ]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  policy   = data.aws_iam_policy_document.tls_only[each.key].json
}
