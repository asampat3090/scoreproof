#!/usr/bin/env bash
# One-time: let GitHub Actions deploy to Cloud Run from main, with no stored
# keys (Workload Identity Federation). Safe to re-run.
#
#   PROJECT=scoreproof-hosted deploy/github-setup.sh
#
# Only pushes to main of REPO can use it. Prints the two repository variables
# the CI deploy job reads.
set -euo pipefail

PROJECT="${PROJECT:?Set PROJECT to your Google Cloud project id}"
REPO="${REPO:-asampat3090/scoreproof}"
NUMBER="$(gcloud projects describe "$PROJECT" --format 'value(projectNumber)')"
SA="github-deployer@${PROJECT}.iam.gserviceaccount.com"
POOL="projects/${NUMBER}/locations/global/workloadIdentityPools/github"

gcloud services enable iamcredentials.googleapis.com sts.googleapis.com --project "$PROJECT"

gcloud iam service-accounts describe "$SA" --project "$PROJECT" >/dev/null 2>&1 ||
  gcloud iam service-accounts create github-deployer --project "$PROJECT" \
    --display-name "GitHub deploys"

# What `gcloud run deploy --source .` needs: build, push the image, update the service.
for role in roles/run.admin roles/cloudbuild.builds.editor roles/artifactregistry.writer \
  roles/storage.admin roles/serviceusage.serviceUsageConsumer roles/logging.viewer; do
  gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$SA" \
    --role "$role" --condition None >/dev/null
done
# The service runs as the default compute account; deploying needs to act as it.
gcloud iam service-accounts add-iam-policy-binding \
  "${NUMBER}-compute@developer.gserviceaccount.com" --project "$PROJECT" \
  --member "serviceAccount:$SA" --role roles/iam.serviceAccountUser >/dev/null

gcloud iam workload-identity-pools describe github --project "$PROJECT" \
  --location global >/dev/null 2>&1 ||
  gcloud iam workload-identity-pools create github --project "$PROJECT" \
    --location global --display-name GitHub
gcloud iam workload-identity-pools providers describe github --project "$PROJECT" \
  --location global --workload-identity-pool github >/dev/null 2>&1 ||
  gcloud iam workload-identity-pools providers create-oidc github --project "$PROJECT" \
    --location global --workload-identity-pool github \
    --issuer-uri https://token.actions.githubusercontent.com \
    --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
    --attribute-condition "assertion.repository=='${REPO}' && assertion.ref=='refs/heads/main'"
gcloud iam service-accounts add-iam-policy-binding "$SA" --project "$PROJECT" \
  --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/${POOL}/attribute.repository/${REPO}" >/dev/null

echo
echo "Now set these repository variables (Settings > Secrets and variables > Actions > Variables):"
echo "  GCP_WORKLOAD_IDENTITY_PROVIDER=${POOL}/providers/github"
echo "  GCP_SERVICE_ACCOUNT=${SA}"
echo "or: gh variable set GCP_WORKLOAD_IDENTITY_PROVIDER -b ${POOL}/providers/github -R ${REPO}"
echo "    gh variable set GCP_SERVICE_ACCOUNT -b ${SA} -R ${REPO}"
