# Glue Data Catalog: a database per domain, a table per data product, from the contracts.

resource "aws_glue_catalog_database" "domain" {
  for_each    = toset(var.domains)
  name        = "${var.name}_${each.value}"
  description = "Data products of the ${each.value} domain"
}

resource "aws_glue_catalog_table" "product" {
  for_each      = var.tables
  name          = each.value.name
  database_name = aws_glue_catalog_database.domain[each.value.domain].name
  description   = each.value.description
  owner         = each.value.owner
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    classification          = "parquet"
    "parquet.compression"   = "ZSTD"
    "mesh.contract_version" = tostring(each.value.version)
    "mesh.owner"            = each.value.owner
  }

  partition_keys {
    name    = each.value.partition
    type    = "date"
    comment = "partition: the business day"
  }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.this["lake-${each.value.domain}"].bucket}/${each.value.name}/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"
    compressed    = true

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    }

    dynamic "columns" {
      for_each = each.value.columns
      content {
        name    = columns.value.name
        type    = columns.value.type
        comment = columns.value.comment
        parameters = {
          "mesh.classification" = columns.value.sensitivity
        }
      }
    }
  }
}
