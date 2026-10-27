variable "region" {
  type    = string
  default = "eu-central-1"
}

variable "environment" {
  type    = string
  default = "prod"
}

variable "name" {
  type        = string
  default     = "mesh"
  description = "Prefix for every resource name."
}

variable "lake_admins" {
  type        = list(string)
  description = "IAM ARNs of the Lake Formation administrators (the platform team's deploy role)."
}

variable "principal_trust" {
  type        = map(list(string))
  default     = {}
  description = "Principal name -> IAM ARNs (SSO permission-set roles, CI roles) allowed to assume that principal's role."
}

variable "vpc_cidr" {
  type    = string
  default = "10.42.0.0/16"
}

variable "api_image" {
  type        = string
  description = "Container image of the platform API (ECR URI with tag)."
}

variable "api_certificate_arn" {
  type        = string
  description = "ACM certificate for the internal load balancer."
}

variable "alarm_email" {
  type    = string
  default = ""
}

variable "airflow_version" {
  type    = string
  default = "2.10.3"
}

# ---- generated from the contracts and config/access.yaml by `mesh infra render` (products.auto.tfvars.json)

variable "domains" {
  type = list(string)
}

variable "lf_tags" {
  type = map(list(string))
}

variable "tables" {
  type = map(object({
    domain      = string
    name        = string
    description = string
    owner       = string
    version     = number
    partition   = string
    columns = list(object({
      name        = string
      type        = string
      comment     = string
      sensitivity = string
    }))
  }))
}

variable "principals" {
  type = map(object({
    description = string
    kind        = string
  }))
}

variable "tag_grants" {
  type = list(object({
    key           = string
    principal     = string
    domains       = list(string)
    sensitivities = list(string)
  }))
}

variable "cell_filters" {
  type = list(object({
    key       = string
    principal = string
    domain    = string
    table     = string
    filter    = string
    columns   = list(string)
  }))
}

variable "athena_bytes_limit" {
  type = map(number)
}
