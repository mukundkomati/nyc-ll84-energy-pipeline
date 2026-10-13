#!/usr/bin/env bash
# Build the ll84 wheel, upload it with the job script, and create/update the Glue job.
# Re-run after every code change.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
[ -f .env ] && set -a && . ./.env && set +a

: "${S3_BUCKET:?Set S3_BUCKET in .env}"
AWS_REGION="${AWS_REGION:-us-east-1}"
GLUE_ROLE_NAME="${GLUE_ROLE_NAME:-ll84-glue-role}"
GLUE_JOB_NAME="${GLUE_JOB_NAME:-ll84-transform}"
RAW_PREFIX="${S3_RAW_PREFIX:-raw/ll84}"
CURATED_PREFIX="${S3_CURATED_PREFIX:-curated/ll84}"
export AWS_REGION AWS_DEFAULT_REGION="$AWS_REGION"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${GLUE_ROLE_NAME}"

echo "Building wheel..."
rm -rf dist build
python -m pip wheel . --no-deps -w dist -q
WHEEL=$(ls dist/ll84_pipeline-*.whl | head -n1)
WHEEL_NAME=$(basename "$WHEEL")

aws s3 cp "$WHEEL" "s3://$S3_BUCKET/glue/libs/$WHEEL_NAME"
aws s3 cp glue/ll84_transform_job.py "s3://$S3_BUCKET/glue/scripts/ll84_transform_job.py"

DEFAULT_ARGS=$(cat <<JSON
{
  "--job-language": "python",
  "--additional-python-modules": "s3://$S3_BUCKET/glue/libs/$WHEEL_NAME",
  "--RAW_URI": "s3://$S3_BUCKET/$RAW_PREFIX",
  "--CURATED_URI": "s3://$S3_BUCKET/$CURATED_PREFIX",
  "--RUN_FLAGS": "true",
  "--YOY_PCT_THRESHOLD": "${YOY_PCT_THRESHOLD:-0.5}",
  "--PEER_Z_THRESHOLD": "${PEER_Z_THRESHOLD:-3.5}",
  "--IQR_MULTIPLIER": "${IQR_MULTIPLIER:-3.0}",
  "--MIN_PEER_GROUP": "${MIN_PEER_GROUP:-20}",
  "--enable-continuous-cloudwatch-log": "true",
  "--enable-metrics": "true"
}
JSON
)
COMMAND="Name=glueetl,ScriptLocation=s3://$S3_BUCKET/glue/scripts/ll84_transform_job.py,PythonVersion=3"

# Glue 5.0, the smallest Spark footprint (2 x G.1X). The work itself is single-process pandas.
if aws glue get-job --job-name "$GLUE_JOB_NAME" >/dev/null 2>&1; then
  aws glue update-job --job-name "$GLUE_JOB_NAME" --job-update "$(cat <<JSON
{
  "Role": "$ROLE_ARN",
  "Command": {"Name": "glueetl", "ScriptLocation": "s3://$S3_BUCKET/glue/scripts/ll84_transform_job.py", "PythonVersion": "3"},
  "DefaultArguments": $DEFAULT_ARGS,
  "GlueVersion": "5.0",
  "WorkerType": "G.1X",
  "NumberOfWorkers": 2,
  "Timeout": 30,
  "MaxRetries": 0
}
JSON
)" >/dev/null
  echo "Updated Glue job $GLUE_JOB_NAME"
else
  aws glue create-job --name "$GLUE_JOB_NAME" --role "$ROLE_ARN" \
    --command "$COMMAND" --default-arguments "$DEFAULT_ARGS" \
    --glue-version 5.0 --worker-type G.1X --number-of-workers 2 \
    --timeout 30 --max-retries 0 \
    --description "NYC LL84: raw CSV -> curated parquet + YoY EUI flags (pandas)" >/dev/null
  echo "Created Glue job $GLUE_JOB_NAME"
fi
echo "Next: python -m ll84 glue-run"
