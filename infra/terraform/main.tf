locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  azs        = slice(data.aws_availability_zones.available.names, 0, 2)

  # (table key, sensitivity) pairs for column tags
  column_tags = merge([
    for key, t in var.tables : {
      for s in distinct([for c in t.columns : c.sensitivity]) :
      "${key}:${s}" => {
        table   = key
        domain  = t.domain
        name    = t.name
        value   = s
        columns = [for c in t.columns : c.name if c.sensitivity == s]
      }
    }
  ]...)
}

resource "aws_kms_key" "lake" {
  description             = "${var.name} lake, landing and query results"
  enable_key_rotation     = true
  deletion_window_in_days = 30
}

resource "aws_kms_alias" "lake" {
  name          = "alias/${var.name}-lake"
  target_key_id = aws_kms_key.lake.key_id
}
