#!/usr/bin/env bash
# Delete everything setup_aws.sh / deploy_glue.sh / athena-setup created. Irreversible.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
[ -f .env ] && set -a && . ./.env && set +a

: "${S3_BUCKET:?Set S3_BUCKET in .env}"
AWS_REGION="${AWS_REGION:-us-east-1}"
GLUE_ROLE_NAME="${GLUE_ROLE_NAME:-ll84-glue-role}"
GLUE_JOB_NAME="${GLUE_JOB_NAME:-ll84-transform}"
GLUE_DATABASE="${GLUE_DATABASE:-ll84}"
ATHENA_WORKGROUP="${ATHENA_WORKGROUP:-ll84-wg}"
export AWS_REGION AWS_DEFAULT_REGION="$AWS_REGION"

read -r -p "Delete bucket s3://$S3_BUCKET (all data), Glue job, catalog DB, role and workgroup? [y/N] " ok
[ "$ok" = "y" ] || { echo "Aborted"; exit 1; }

aws glue delete-job --job-name "$GLUE_JOB_NAME" >/dev/null 2>&1 || true
for t in $(aws glue get-tables --database-name "$GLUE_DATABASE" --query 'TableList[].Name' --output text 2>/dev/null); do
  aws glue delete-table --database-name "$GLUE_DATABASE" --name "$t"
done
aws glue delete-database --name "$GLUE_DATABASE" >/dev/null 2>&1 || true
aws athena delete-work-group --work-group "$ATHENA_WORKGROUP" --recursive-delete-option >/dev/null 2>&1 || true
aws iam delete-role-policy --role-name "$GLUE_ROLE_NAME" --policy-name ll84-s3-access >/dev/null 2>&1 || true
aws iam detach-role-policy --role-name "$GLUE_ROLE_NAME" \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole >/dev/null 2>&1 || true
aws iam delete-role --role-name "$GLUE_ROLE_NAME" >/dev/null 2>&1 || true
aws s3 rb "s3://$S3_BUCKET" --force
echo "Teardown complete."
