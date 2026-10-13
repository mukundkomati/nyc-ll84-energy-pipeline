#!/usr/bin/env bash
# One-time AWS setup: S3 bucket, Glue IAM role, Athena workgroup with a cost cap.
# Usage: ./infra/setup_aws.sh   (reads settings from .env)
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
[ -f .env ] && set -a && . ./.env && set +a

: "${S3_BUCKET:?Set S3_BUCKET in .env}"
AWS_REGION="${AWS_REGION:-us-east-1}"
GLUE_ROLE_NAME="${GLUE_ROLE_NAME:-ll84-glue-role}"
ATHENA_WORKGROUP="${ATHENA_WORKGROUP:-ll84-wg}"
ATHENA_SCAN_LIMIT_BYTES="${ATHENA_SCAN_LIMIT_BYTES:-1073741824}"  # 1 GB per query
export AWS_REGION AWS_DEFAULT_REGION="$AWS_REGION"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "Account $ACCOUNT_ID, region $AWS_REGION, bucket $S3_BUCKET"

# --- S3 bucket ----------------------------------------------------------------
if aws s3api head-bucket --bucket "$S3_BUCKET" 2>/dev/null; then
  echo "Bucket exists"
else
  if [ "$AWS_REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$S3_BUCKET"
  else
    aws s3api create-bucket --bucket "$S3_BUCKET" \
      --create-bucket-configuration LocationConstraint="$AWS_REGION"
  fi
  echo "Created bucket"
fi
aws s3api put-public-access-block --bucket "$S3_BUCKET" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-encryption --bucket "$S3_BUCKET" --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
# Athena result files are disposable: expire them after 7 days.
aws s3api put-bucket-lifecycle-configuration --bucket "$S3_BUCKET" --lifecycle-configuration \
  '{"Rules":[{"ID":"expire-athena-results","Status":"Enabled","Filter":{"Prefix":"athena-results/"},"Expiration":{"Days":7}}]}'

# --- IAM role for Glue ---------------------------------------------------------
if aws iam get-role --role-name "$GLUE_ROLE_NAME" >/dev/null 2>&1; then
  echo "Role $GLUE_ROLE_NAME exists"
else
  aws iam create-role --role-name "$GLUE_ROLE_NAME" \
    --assume-role-policy-document file://infra/iam/glue-trust-policy.json >/dev/null
  aws iam attach-role-policy --role-name "$GLUE_ROLE_NAME" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole
  echo "Created role $GLUE_ROLE_NAME"
fi
sed "s/__BUCKET__/$S3_BUCKET/g" infra/iam/glue-s3-policy.json > /tmp/ll84-glue-s3-policy.json
aws iam put-role-policy --role-name "$GLUE_ROLE_NAME" --policy-name ll84-s3-access \
  --policy-document file:///tmp/ll84-glue-s3-policy.json

# --- Athena workgroup (results location + per-query scan cap) ------------------
if aws athena get-work-group --work-group "$ATHENA_WORKGROUP" >/dev/null 2>&1; then
  echo "Workgroup $ATHENA_WORKGROUP exists"
else
  aws athena create-work-group --name "$ATHENA_WORKGROUP" --configuration \
    "ResultConfiguration={OutputLocation=s3://$S3_BUCKET/athena-results/},EnforceWorkGroupConfiguration=true,PublishCloudWatchMetricsEnabled=true,BytesScannedCutoffPerQuery=$ATHENA_SCAN_LIMIT_BYTES,EngineVersion={SelectedEngineVersion=AUTO}"
  echo "Created Athena workgroup $ATHENA_WORKGROUP"
fi

echo
echo "Done. Next: ./infra/deploy_glue.sh"
