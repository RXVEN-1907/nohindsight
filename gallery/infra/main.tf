# main.tf
# Report artifact storage for nohindsight, plus the shape of a nightly
# audit box. Kept small: one bucket, one policy, everything else commented.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

variable "region" {
  description = "AWS region for the report bucket."
  type        = string
  default     = "us-east-1"
}

variable "bucket_name" {
  description = "Globally unique S3 bucket name for report artifacts."
  type        = string
  default     = "nohindsight-reports"
}

provider "aws" {
  region = var.region
}

# ---------------------------------------------------------------------------
# Report bucket. Versioning is on because a report is evidence: an
# overwritten report can be diffed against its predecessor, and a bad write
# can be rolled back instead of being lost.
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "reports" {
  bucket = var.bucket_name
}

resource "aws_s3_bucket_versioning" "reports" {
  bucket = aws_s3_bucket.reports.id

  versioning_configuration {
    status = "Enabled"
  }
}

# Server-side encryption with the S3 managed key. AES256 costs nothing and
# satisfies at-rest encryption. Swap to aws:kms if a compliance regime
# demands key-level control.
resource "aws_s3_bucket_server_side_encryption_configuration" "reports" {
  bucket = aws_s3_bucket.reports.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Private by default. Reports contain positions and PnL; the bucket stays
# closed unless someone deliberately opens it.
resource "aws_s3_bucket_public_access_block" "reports" {
  bucket = aws_s3_bucket.reports.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ---------------------------------------------------------------------------
# Read/write IAM policy scoped to this bucket only. Attach it to whatever
# needs to push reports: an auditor role, a CI user, the nightly box.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "reports_rw" {
  statement {
    sid       = "ListBucket"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.reports.arn]
  }

  statement {
    sid       = "ReadWriteObjects"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.reports.arn}/*"]
  }
}

resource "aws_iam_policy" "reports_rw" {
  name   = "nohindsight-reports-rw"
  policy = data.aws_iam_policy_document.reports_rw.json
}

output "bucket_arn" {
  description = "ARN of the report bucket."
  value       = aws_s3_bucket.reports.arn
}

# ---------------------------------------------------------------------------
# Static site flip, if reports should be viewable over HTTP. Three steps:
#
# 1. Loosen the public access block above: set block_public_policy and
#    restrict_public_buckets to false.
# 2. Attach a read-only bucket policy:
#
# data "aws_iam_policy_document" "reports_public_read" {
#   statement {
#     actions   = ["s3:GetObject"]
#     resources = ["${aws_s3_bucket.reports.arn}/*"]
#     principals {
#       type        = "*"
#       identifiers = ["*"]
#     }
#   }
# }
#
# resource "aws_s3_bucket_policy" "reports_public_read" {
#   bucket = aws_s3_bucket.reports.id
#   policy = data.aws_iam_policy_document.reports_public_read.json
# }
#
# 3. Optionally front it with CloudFront instead of going public at all.
#    This is the better path: the bucket stays private and the distribution
#    reads through an origin access control. TLS comes free on the CDN.
#
# resource "aws_cloudfront_distribution" "reports" {
#   enabled         = true
#   default_root_object = "index.html"
#   origin {
#     domain_name              = aws_s3_bucket.reports.bucket_regional_domain_name
#     origin_id                = "reports"
#     origin_access_control_id = aws_cloudfront_origin_access_control.reports.id
#   }
#   default_cache_behavior {
#     target_origin_id       = "reports"
#     viewer_protocol_policy = "redirect-to-https"
#     allowed_methods        = ["GET", "HEAD"]
#     cached_methods         = ["GET", "HEAD"]
#     forwarded_values {
#       query_string = false
#       cookies { forward = "none" }
#     }
#   }
#   viewer_certificate { cloudfront_default_certificate = true }
#   restrictions {
#     geo_restriction { restriction_type = "none" }
#   }
# }
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Nightly `nh audit` on a spot instance. Shape, not a deploy: fill in the
# AMI and the instance profile, then uncomment. The user_data script
# installs Rust, builds or fetches the binary, and schedules the audit at
# 02:00. Spot is right for a two-minute nightly job: interruptions only
# cost you a day, and the report bucket above keeps the history.
#
# resource "aws_instance" "auditor" {
#   ami                  = "ami-REPLACE_ME"
#   instance_type        = "t4g.small"
#   iam_instance_profile = "REPLACE_ME_profile_with_reports_rw"
#   instance_market_options {
#     market_type = "spot"
#   }
#   user_data = <<-EOF
#     #!/bin/bash
#     set -euo pipefail
#     curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
#     export PATH="$HOME/.cargo/bin:$PATH"
#     git clone https://github.com/RXVEN-1907/nohindsight /opt/nohindsight
#     cd /opt/nohindsight && cargo build --release
#     install -m 0755 target/release/nh /usr/local/bin/nh
#     cat > /usr/local/bin/nightly-audit <<'AUDIT'
#       #!/bin/bash
#       set -euo pipefail
#       nh audit --out /var/reports
#       aws s3 cp /var/reports s3://nohindsight-reports/ --recursive
#     AUDIT
#     chmod +x /usr/local/bin/nightly-audit
#     echo "0 2 * * * root /usr/local/bin/nightly-audit" > /etc/cron.d/nh-audit
#   EOF
#   tags = { Name = "nohindsight-nightly-auditor" }
# }
# ---------------------------------------------------------------------------
