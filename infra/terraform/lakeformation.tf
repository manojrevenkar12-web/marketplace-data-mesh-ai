# Lake Formation tag-based access control. Tags and grants come from config/access.yaml and the contracts
# through products.auto.tfvars.json, the same objects the platform's query engine evaluates locally.

resource "aws_lakeformation_data_lake_settings" "this" {
  admins = var.lake_admins
  # No create_*_default_permissions blocks: new databases and tables get no IAMAllowedPrincipals grant,
  # so Lake Formation permissions alone decide who reads what.
}

resource "aws_lakeformation_resource" "domain" {
  for_each                = toset(var.domains)
  arn                     = aws_s3_bucket.this["lake-${each.value}"].arn
  role_arn                = aws_iam_role.lake_access.arn
  hybrid_access_enabled   = false
  use_service_linked_role = false
}

resource "aws_lakeformation_lf_tag" "this" {
  for_each   = var.lf_tags
  key        = each.key
  values     = each.value
  depends_on = [aws_lakeformation_data_lake_settings.this]
}

resource "aws_lakeformation_resource_lf_tags" "table_domain" {
  for_each = var.tables
  table {
    database_name = aws_glue_catalog_database.domain[each.value.domain].name
    name          = aws_glue_catalog_table.product[each.key].name
  }
  lf_tag {
    key   = aws_lakeformation_lf_tag.this["domain"].key
    value = each.value.domain
  }
}

resource "aws_lakeformation_resource_lf_tags" "column_sensitivity" {
  for_each = local.column_tags
  table_with_columns {
    database_name = aws_glue_catalog_database.domain[each.value.domain].name
    name          = aws_glue_catalog_table.product[each.value.table].name
    column_names  = each.value.columns
  }
  lf_tag {
    key   = aws_lakeformation_lf_tag.this["sensitivity"].key
    value = each.value.value
  }
}

# Tag-policy grants: SELECT on every column whose domain and sensitivity tags match.
resource "aws_lakeformation_permissions" "tag_grant" {
  for_each    = { for g in var.tag_grants : g.key => g }
  principal   = aws_iam_role.principal[each.value.principal].arn
  permissions = ["SELECT", "DESCRIBE"]

  lf_tag_policy {
    resource_type = "TABLE"
    expression {
      key    = "domain"
      values = each.value.domains
    }
    expression {
      key    = "sensitivity"
      values = each.value.sensitivities
    }
  }
  depends_on = [aws_lakeformation_resource_lf_tags.table_domain, aws_lakeformation_resource_lf_tags.column_sensitivity]
}

# Row filters: a data cells filter per filtered principal and table, and SELECT on that filter.
resource "aws_lakeformation_data_cells_filter" "row" {
  for_each = { for f in var.cell_filters : f.key => f }
  table_data {
    database_name    = aws_glue_catalog_database.domain[each.value.domain].name
    name             = substr(each.key, 0, 255)
    table_catalog_id = local.account_id
    table_name       = each.value.table
    column_names     = each.value.columns
    row_filter {
      filter_expression = each.value.filter
    }
  }
  depends_on = [aws_glue_catalog_table.product]
}

resource "aws_lakeformation_permissions" "row_filter" {
  for_each    = { for f in var.cell_filters : f.key => f }
  principal   = aws_iam_role.principal[each.value.principal].arn
  permissions = ["SELECT"]
  data_cells_filter {
    database_name    = aws_glue_catalog_database.domain[each.value.domain].name
    name             = aws_lakeformation_data_cells_filter.row[each.key].table_data[0].name
    table_catalog_id = local.account_id
    table_name       = each.value.table
  }
}

# The pipeline role writes and registers every product (the platform-pipelines principal's grant gives it
# SELECT; publishing also needs data location access and ALTER on the tables).
resource "aws_lakeformation_permissions" "pipelines_location" {
  for_each    = toset(var.domains)
  principal   = aws_iam_role.mwaa.arn
  permissions = ["DATA_LOCATION_ACCESS"]
  data_location {
    arn = aws_lakeformation_resource.domain[each.value].arn
  }
}

resource "aws_lakeformation_permissions" "pipelines_tables" {
  principal   = aws_iam_role.mwaa.arn
  permissions = ["ALTER", "INSERT", "DESCRIBE", "SELECT"]
  lf_tag_policy {
    resource_type = "TABLE"
    expression {
      key    = "domain"
      values = var.domains
    }
  }
  depends_on = [aws_lakeformation_resource_lf_tags.table_domain]
}
