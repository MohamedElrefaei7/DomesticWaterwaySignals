# External uptime monitoring, from outside the instance, alerting on what the payload SAYS.
#
# THE HEALTH CHECK IS A STRING MATCH, NOT A STATUS CHECK, AND THAT IS THE POINT OF THIS FILE.
# `/api/health` returns 200 when degraded, by contract (CLAUDE.md § 20) — the 200 is deliberate,
# because an uptime monitor that goes red on a stale ingest job is indistinguishable from one that
# goes red because the API is down, and those need different responses at different hours. So a
# status-code monitor on this endpoint is a check that CANNOT FAIL: CLAUDE.md § 2's theme 2,
# expressed in Terraform.

# ROUTE53 HEALTH-CHECK METRICS EXIST ONLY IN us-east-1, whatever region the health check itself is
# managed from. The stack is already us-east-1 so this costs nothing today — but it is pinned to an
# explicit aliased provider rather than left to var.aws_region, because the day somebody adds a
# second region and moves the default, an alarm built on the regional provider silently monitors a
# metric namespace that has nothing in it.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}

resource "aws_route53_health_check" "api" {
  provider = aws.us_east_1

  type              = "HTTPS_STR_MATCH"
  fqdn              = var.domain_name
  port              = 443
  resource_path     = "/api/health"
  request_interval  = 30
  failure_threshold = 3

  # The token, matched against the FIRST 5,120 BYTES of the body. `degraded` is the first field of
  # HealthResponse and the nested models use `overdue` and `stale`, so this string appears exactly
  # once and only at the top level.
  #
  # It is the field the API ALREADY HAS. No `status` field was added and no ok token was invented:
  # CLAUDE.md § 20 forbids a bare `{"status":"ok"}` precisely because that shape is what let the
  # prior project record "Completed" while the stack had been down for two and a half months.
  #
  # `tests/terraform/test_monitoring_hcl.py` asserts this literal against a body rendered through
  # the real app, not against another literal — a literal-to-literal comparison catches a typo and
  # misses a change of response class or JSON separators, which is the failure that would leave
  # this monitor permanently green.
  search_string = "\"degraded\":false"

  # SNI, because the origin serves several names from one address and the wrong certificate is a
  # failure that reads as a TLS problem rather than as a misconfigured check.
  enable_sni = true

  measure_latency = true

  tags = {
    Name = "${var.project_name}-api-health"
  }
}

resource "aws_sns_topic" "alerts" {
  provider = aws.us_east_1
  name     = "${var.project_name}-alerts"
}

# AN EMAIL SUBSCRIPTION IS CREATED PENDING AND DELIVERS NOTHING UNTIL THE LINK IS CLICKED, while
# the alarm above it reports as fully configured — Theme 1 in one resource. After apply, check that
# the subscription ARN is not the literal string `PendingConfirmation`.
resource "aws_sns_topic_subscription" "alerts_email" {
  provider  = aws.us_east_1
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_cloudwatch_metric_alarm" "api_health" {
  provider = aws.us_east_1

  alarm_name  = "${var.project_name}-api-health"
  namespace   = "AWS/Route53"
  metric_name = "HealthCheckStatus"

  dimensions = {
    HealthCheckId = aws_route53_health_check.api.id
  }

  # Minimum over one minute: the metric is reported per checker region, and Minimum going to 0
  # means at least one region could not match the string.
  statistic = "Minimum"
  period    = 60

  # THREE PERIODS, NOT ONE. A single edge location having a bad thirty seconds is not an incident,
  # and a monitor that pages for one gets muted — after which the next real outage is silent.
  evaluation_periods  = 3
  threshold           = 1
  comparison_operator = "LessThanThreshold"

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  # AN ALARM STUCK IN INSUFFICIENT_DATA IS INDISTINGUISHABLE FROM A HEALTHY ONE ON A DASHBOARD, and
  # is exactly how a monitor dies quietly — the health check is deleted, the metric stops arriving,
  # and the alarm sits grey forever reporting nothing wrong.
  insufficient_data_actions = [aws_sns_topic.alerts.arn]

  alarm_description = join(" ", [
    "api /api/health stopped matching the degraded:false token, or stopped reporting.",
    "This does NOT mean the API is down - /api/health returns 200 while degraded by contract.",
    "It means the payload says something is wrong, or nothing is answering at all.",
  ])

  treat_missing_data = "breaching"
}

# CONTEXT.md § Up Next item 6, open since Phase 10 with status unknown. There is a running
# instance, an EIP and an EBS volume billing continuously, and two S3 buckets joining them in this
# phase. A budget is two resources and the alternative is finding out from a statement.
resource "aws_budgets_budget" "monthly" {
  name         = "${var.project_name}-monthly"
  budget_type  = "COST"
  limit_amount = var.monthly_budget_usd
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # Actual spend crossing the threshold.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  # And a forecast, which is the one that arrives while there is still time to act.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }
}

output "backup_bucket_name" {
  description = "Backup bucket name, for the job's configuration and for CONTEXT.md."
  value       = aws_s3_bucket.backups.id
}

output "api_health_check_id" {
  description = "Route53 health check id, for `aws route53 get-health-check-status`."
  value       = aws_route53_health_check.api.id
}

output "alerts_topic_arn" {
  description = "SNS topic arn, for `aws sns list-subscriptions-by-topic`."
  value       = aws_sns_topic.alerts.arn
}
