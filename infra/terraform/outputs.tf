output "lake_buckets" {
  value = { for d in var.domains : d => aws_s3_bucket.this["lake-${d}"].bucket }
}

output "glue_databases" {
  value = { for d, db in aws_glue_catalog_database.domain : d => db.name }
}

output "principal_roles" {
  value = { for n, r in aws_iam_role.principal : n => r.arn }
}

output "athena_workgroups" {
  value = { for k, w in aws_athena_workgroup.purpose : k => w.name }
}

output "mwaa_environment" {
  value = aws_mwaa_environment.this.name
}

output "api_internal_url" {
  value = "https://${aws_lb.api.dns_name}"
}
