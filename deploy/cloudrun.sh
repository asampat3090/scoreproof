#!/usr/bin/env bash
# Deploy the hosted service to Cloud Run. Safe to re-run: every step either
# creates what is missing or updates it in place.
#
#   PROJECT=my-gcp-project deploy/cloudrun.sh
#
# Needs the gcloud CLI, logged in (gcloud auth login), and billing enabled on
# the project. Optional: REGION (default us-central1), SERVICE, BUCKET.
set -euo pipefail

PROJECT="${PROJECT:?Set PROJECT to your Google Cloud project id}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-scoreproof}"
BUCKET="${BUCKET:-${PROJECT}-scoreproof-files}"
cd "$(dirname "$0")/.."

gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com storage.googleapis.com --project "$PROJECT"

# Files people upload or make live here for one day, then are deleted.
if ! gcloud storage buckets describe "gs://$BUCKET" --project "$PROJECT" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://$BUCKET" --project "$PROJECT" \
    --location "$REGION" --uniform-bucket-level-access --public-access-prevention
fi
gcloud storage buckets update "gs://$BUCKET" --lifecycle-file deploy/lifecycle.json

NUMBER="$(gcloud projects describe "$PROJECT" --format 'value(projectNumber)')"
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member "serviceAccount:${NUMBER}-compute@developer.gserviceaccount.com" \
  --role roles/storage.objectAdmin >/dev/null

# Small, capped and scale-to-zero: at most 3 instances of 4 requests each.
gcloud run deploy "$SERVICE" --source . --project "$PROJECT" --region "$REGION" \
  --allow-unauthenticated --cpu 2 --memory 2Gi --concurrency 4 \
  --min-instances 0 --max-instances 3 --timeout 300 \
  --update-env-vars "SCOREPROOF_BUCKET=$BUCKET"

URL="${PUBLIC_URL:-$(gcloud run services describe "$SERVICE" --project "$PROJECT" \
  --region "$REGION" --format 'value(status.url)')}"
gcloud run services update "$SERVICE" --project "$PROJECT" --region "$REGION" \
  --update-env-vars "SCOREPROOF_PUBLIC_URL=$URL"

echo
echo "Drop page:     $URL"
echo "MCP connector: $URL/mcp"
