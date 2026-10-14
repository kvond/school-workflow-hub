#!/usr/bin/env bash
# One-time Google Cloud setup for the weekly preflight.
#
#   PROJECT_ID=my-project ./deploy/setup.sh
#
# Before running: config.yaml and secrets/token.json exist locally (see deploy/README.md),
# and you are signed in with `gcloud auth login` as someone who can create resources in the project.
# Safe to re-run: existing resources are left alone, secrets get a new version.
set -euo pipefail

: "${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-us-east1}"
REPO="${REPO:-preflight}"
JOB="${JOB:-weekly-preflight}"
BUCKET="${BUCKET:-${PROJECT_ID}-preflight}"
SA_NAME="${SA_NAME:-preflight-runner}"
SA="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
TZ_NAME="${TZ_NAME:-America/New_York}"
# Thursday afternoon for the coming week, and a Sunday re-check that catches weekend edits.
THURSDAY_CRON="${THURSDAY_CRON:-47 15 * * 4}"
SUNDAY_CRON="${SUNDAY_CRON:-47 17 * * 0}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/preflight:latest"

gcloud config set project "$PROJECT_ID"

echo "== APIs"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com cloudscheduler.googleapis.com storage.googleapis.com \
  drive.googleapis.com sheets.googleapis.com aiplatform.googleapis.com

echo "== Artifact Registry, bucket, service account"
gcloud artifacts repositories describe "$REPO" --location "$REGION" >/dev/null 2>&1 ||
  gcloud artifacts repositories create "$REPO" --repository-format docker --location "$REGION"
gcloud storage buckets describe "gs://${BUCKET}" >/dev/null 2>&1 ||
  gcloud storage buckets create "gs://${BUCKET}" --location "$REGION" --uniform-bucket-level-access
gcloud iam service-accounts describe "$SA" >/dev/null 2>&1 ||
  gcloud iam service-accounts create "$SA_NAME" --display-name "Weekly preflight"
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" --member "serviceAccount:${SA}" \
  --role roles/storage.objectAdmin >/dev/null
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:${SA}" \
  --role roles/aiplatform.user --condition None >/dev/null

echo "== Secrets"
put_secret() {  # name, file
  gcloud secrets describe "$1" >/dev/null 2>&1 || gcloud secrets create "$1" --replication-policy automatic
  gcloud secrets versions add "$1" --data-file "$2"
  gcloud secrets add-iam-policy-binding "$1" --member "serviceAccount:${SA}" \
    --role roles/secretmanager.secretAccessor >/dev/null
}
put_secret preflight-config config.yaml
put_secret preflight-google-token secrets/token.json
SECRETS="GOOGLE_OAUTH_TOKEN_JSON=preflight-google-token:latest"
if [[ -n "${SCHOOLOGY_KEY:-}" && -n "${SCHOOLOGY_SECRET:-}" ]]; then
  printf '%s' "$SCHOOLOGY_KEY" > /tmp/sk && put_secret preflight-schoology-key /tmp/sk
  printf '%s' "$SCHOOLOGY_SECRET" > /tmp/ss && put_secret preflight-schoology-secret /tmp/ss
  rm -f /tmp/sk /tmp/ss
  SECRETS="${SECRETS},SCHOOLOGY_KEY=preflight-schoology-key:latest,SCHOOLOGY_SECRET=preflight-schoology-secret:latest"
fi

echo "== First image"
gcloud builds submit --region "$REGION" --tag "$IMAGE" .

echo "== Let Cloud Build redeploy the job on later pushes"
NUM="$(gcloud projects describe "$PROJECT_ID" --format 'value(projectNumber)')"
for builder in "${NUM}@cloudbuild.gserviceaccount.com" "${NUM}-compute@developer.gserviceaccount.com"; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:${builder}" \
    --role roles/run.developer --condition None >/dev/null 2>&1 || true
  gcloud iam service-accounts add-iam-policy-binding "$SA" --member "serviceAccount:${builder}" \
    --role roles/iam.serviceAccountUser >/dev/null 2>&1 || true
done

echo "== Cloud Run Job"
if gcloud run jobs describe "$JOB" --region "$REGION" >/dev/null 2>&1; then
  ACTION=update
else
  ACTION=create
fi
gcloud run jobs "$ACTION" "$JOB" --region "$REGION" --image "$IMAGE" \
  --service-account "$SA" --task-timeout 15m --max-retries 1 --memory 1Gi \
  --set-secrets "${SECRETS},/config/config.yaml=preflight-config:latest" \
  --set-env-vars "LOG_LEVEL=INFO"

echo "== Schedules (${TZ_NAME})"
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:${SA}" \
  --role roles/run.invoker --condition None >/dev/null
RUN_URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB}:run"
for pair in "preflight-thursday|${THURSDAY_CRON}" "preflight-sunday|${SUNDAY_CRON}"; do
  name="${pair%%|*}"; cron="${pair#*|}"
  if gcloud scheduler jobs describe "$name" --location "$REGION" >/dev/null 2>&1; then
    verb=update
  else
    verb=create
  fi
  gcloud scheduler jobs "$verb" http "$name" --location "$REGION" --schedule "$cron" --time-zone "$TZ_NAME" \
    --uri "$RUN_URI" --http-method POST --oauth-service-account-email "$SA"
done

echo
echo "Done. Run it now with:  gcloud run jobs execute ${JOB} --region ${REGION} --wait"
echo "Then connect the GitHub repo to Cloud Build (deploy/README.md, step 5) so pushes to main redeploy."
