#!/usr/bin/env bash
# Provision GCP project resources and keyless GitHub Actions Workload Identity Federation for RollMetrics.
set -euo pipefail

export CLOUDSDK_CORE_DISABLE_PROMPTS=1

PROJECT_ID="${PROJECT_ID:-your-gcp-project}"
REGION="${REGION:-us-central1}"
GITHUB_REPO="${GITHUB_REPO:-your-org/rollmetrics}"
POOL_ID="${POOL_ID:-github-pool}"
PROVIDER_ID="${PROVIDER_ID:-github-provider}"
RUNTIME_SA_NAME="${RUNTIME_SA_NAME:-rollmetrics-runner}"
DEPLOYER_SA_NAME="${DEPLOYER_SA_NAME:-github-deployer}"
AR_REPO="${AR_REPO:-rollmetrics}"
PRIMARY_BUCKET="${PRIMARY_BUCKET:-${PROJECT_ID}-media-store}"
FALLBACK_BUCKET="${FALLBACK_BUCKET:-${PROJECT_ID}-media-fallback}"
PUBLIC_URL="${ROLLMETRICS_PUBLIC_URL:-http://127.0.0.1:8090}"

PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
RUNTIME_SA="${RUNTIME_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
DEPLOYER_SA="${DEPLOYER_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

echo "==> Enabling APIs on ${PROJECT_ID} (${PROJECT_NUMBER})"
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  storage.googleapis.com \
  secretmanager.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  generativelanguage.googleapis.com \
  apikeys.googleapis.com \
  --project="${PROJECT_ID}"

echo "==> Ensuring GCS media bucket in ${REGION}"
BUCKET_NAME="${PRIMARY_BUCKET}"
if ! gcloud storage buckets describe "gs://${BUCKET_NAME}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  if ! gcloud storage buckets create "gs://${BUCKET_NAME}" \
    --project="${PROJECT_ID}" \
    --location="${REGION}" \
    --uniform-bucket-level-access 2>/dev/null; then
    BUCKET_NAME="${FALLBACK_BUCKET}"
    gcloud storage buckets describe "gs://${BUCKET_NAME}" --project="${PROJECT_ID}" >/dev/null 2>&1 || \
      gcloud storage buckets create "gs://${BUCKET_NAME}" \
        --project="${PROJECT_ID}" \
        --location="${REGION}" \
        --uniform-bucket-level-access
  fi
fi
echo "Active bucket: gs://${BUCKET_NAME}"

echo "==> Ensuring Artifact Registry repository ${AR_REPO} in ${REGION}"
if ! gcloud artifacts repositories describe "${AR_REPO}" --project="${PROJECT_ID}" --location="${REGION}" >/dev/null 2>&1; then
  gcloud artifacts repositories create "${AR_REPO}" \
    --project="${PROJECT_ID}" \
    --location="${REGION}" \
    --repository-format=docker \
    --description="RollMetrics container images"
fi

echo "==> Ensuring service accounts"
if ! gcloud iam service-accounts describe "${RUNTIME_SA}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${RUNTIME_SA_NAME}" \
    --project="${PROJECT_ID}" \
    --display-name="RollMetrics Cloud Run Runtime"
fi

if ! gcloud iam service-accounts describe "${DEPLOYER_SA}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${DEPLOYER_SA_NAME}" \
    --project="${PROJECT_ID}" \
    --display-name="RollMetrics GitHub Actions Deployer"
fi

echo "==> Binding IAM roles for runtime SA (${RUNTIME_SA})"
for role in roles/secretmanager.secretAccessor roles/storage.objectAdmin; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${RUNTIME_SA}" \
    --role="${role}" \
    --condition=None \
    --quiet >/dev/null
done

echo "==> Binding IAM roles for deployer SA (${DEPLOYER_SA})"
for role in roles/run.admin roles/artifactregistry.writer roles/cloudbuild.builds.editor roles/storage.admin roles/secretmanager.viewer; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${DEPLOYER_SA}" \
    --role="${role}" \
    --condition=None \
    --quiet >/dev/null
done

gcloud iam service-accounts add-iam-policy-binding "${RUNTIME_SA}" \
  --project="${PROJECT_ID}" \
  --member="serviceAccount:${DEPLOYER_SA}" \
  --role="roles/iam.serviceAccountUser" \
  --quiet >/dev/null

gcloud iam service-accounts add-iam-policy-binding "${RUNTIME_SA}" \
  --project="${PROJECT_ID}" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/iam.serviceAccountTokenCreator" \
  --quiet >/dev/null

gcloud storage buckets add-iam-policy-binding "gs://${BUCKET_NAME}" \
  --project="${PROJECT_ID}" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/storage.objectUser" \
  --quiet >/dev/null

CORS_FILE="$(mktemp)"
cat <<EOF > "${CORS_FILE}"
[
  {
    "origin": [
      "${PUBLIC_URL}",
      "http://127.0.0.1:8090",
      "http://localhost:8090"
    ],
    "method": ["PUT", "POST", "GET", "HEAD", "OPTIONS"],
    "responseHeader": [
      "Content-Type",
      "Content-Length",
      "Content-Range",
      "ETag",
      "x-goog-resumable",
      "x-goog-meta-*"
    ],
    "maxAgeSeconds": 3600
  }
]
EOF
gcloud storage buckets update "gs://${BUCKET_NAME}" --project="${PROJECT_ID}" --cors-file="${CORS_FILE}" --quiet >/dev/null
rm -f "${CORS_FILE}"

echo "==> Ensuring Workload Identity Pool (${POOL_ID}) and GitHub OIDC Provider (${PROVIDER_ID})"
if ! gcloud iam workload-identity-pools describe "${POOL_ID}" --project="${PROJECT_ID}" --location="global" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools create "${POOL_ID}" \
    --project="${PROJECT_ID}" \
    --location="global" \
    --display-name="GitHub Actions Pool"
fi

if ! gcloud iam workload-identity-pools providers describe "${PROVIDER_ID}" \
  --project="${PROJECT_ID}" \
  --location="global" \
  --workload-identity-pool="${POOL_ID}" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers create-oidc "${PROVIDER_ID}" \
    --project="${PROJECT_ID}" \
    --location="global" \
    --workload-identity-pool="${POOL_ID}" \
    --display-name="GitHub OIDC Provider" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.actor=assertion.actor,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner" \
    --attribute-condition="attribute.repository == \"${GITHUB_REPO}\""
fi

WIF_MEMBER="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/attribute.repository/${GITHUB_REPO}"
for attempt in 1 2 3 4 5; do
  if gcloud iam service-accounts add-iam-policy-binding "${DEPLOYER_SA}" \
    --project="${PROJECT_ID}" \
    --member="${WIF_MEMBER}" \
    --role="roles/iam.workloadIdentityUser" \
    --quiet >/dev/null 2>&1; then
    break
  fi
  echo "Waiting for Workload Identity Pool IAM propagation (attempt ${attempt}/5)..."
  sleep 4
done
gcloud iam service-accounts add-iam-policy-binding "${DEPLOYER_SA}" \
  --project="${PROJECT_ID}" \
  --member="${WIF_MEMBER}" \
  --role="roles/iam.workloadIdentityUser" \
  --quiet >/dev/null

WIF_PROVIDER_RESOURCE="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/providers/${PROVIDER_ID}"
AR_IMAGE_REPO="${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}"

if command -v gh >/dev/null 2>&1 && gh repo view "${GITHUB_REPO}" >/dev/null 2>&1; then
  echo "==> Syncing GitHub Actions repository variables on ${GITHUB_REPO}"
  gh variable set GCP_PROJECT_ID --repo "${GITHUB_REPO}" --body "${PROJECT_ID}"
  gh variable set GCP_REGION --repo "${GITHUB_REPO}" --body "${REGION}"
  gh variable set GCP_WIF_PROVIDER --repo "${GITHUB_REPO}" --body "${WIF_PROVIDER_RESOURCE}"
  gh variable set GCP_DEPLOYER_SA --repo "${GITHUB_REPO}" --body "${DEPLOYER_SA}"
  gh variable set GCP_RUNTIME_SA --repo "${GITHUB_REPO}" --body "${RUNTIME_SA}"
  gh variable set GCP_ARTIFACT_REPO --repo "${GITHUB_REPO}" --body "${AR_IMAGE_REPO}"
  gh variable set ROLLMETRICS_GCS_BUCKET --repo "${GITHUB_REPO}" --body "${BUCKET_NAME}"
  if [ -n "${ROLLMETRICS_ALLOWLIST_EMAILS:-}" ]; then
    gh variable set ROLLMETRICS_ALLOWLIST_EMAILS --repo "${GITHUB_REPO}" --body "${ROLLMETRICS_ALLOWLIST_EMAILS}"
  fi
  if [ -n "${ROLLMETRICS_OWNER_EMAIL:-}" ]; then
    gh variable set ROLLMETRICS_OWNER_EMAIL --repo "${GITHUB_REPO}" --body "${ROLLMETRICS_OWNER_EMAIL}"
  fi
  if [ -n "${ROLLMETRICS_PUBLIC_URL:-}" ]; then
    gh variable set ROLLMETRICS_PUBLIC_URL --repo "${GITHUB_REPO}" --body "${ROLLMETRICS_PUBLIC_URL}"
  fi
  if [ -n "${ROLLMETRICS_DEFAULT_ACADEMY_ID:-}" ]; then
    gh variable set ROLLMETRICS_DEFAULT_ACADEMY_ID --repo "${GITHUB_REPO}" --body "${ROLLMETRICS_DEFAULT_ACADEMY_ID}"
  fi
  if [ -n "${ROLLMETRICS_GOOGLE_CLIENT_ID:-}" ]; then
    gh variable set ROLLMETRICS_GOOGLE_CLIENT_ID --repo "${GITHUB_REPO}" --body "${ROLLMETRICS_GOOGLE_CLIENT_ID}"
  fi
  if [ -n "${ROLLMETRICS_FIREBASE_PROJECT_ID:-}" ]; then
    gh variable set ROLLMETRICS_FIREBASE_PROJECT_ID --repo "${GITHUB_REPO}" --body "${ROLLMETRICS_FIREBASE_PROJECT_ID}"
  fi
fi

echo "==> WIF provisioning complete"
echo "GCP_WIF_PROVIDER=${WIF_PROVIDER_RESOURCE}"
echo "GCP_DEPLOYER_SA=${DEPLOYER_SA}"
echo "GCP_RUNTIME_SA=${RUNTIME_SA}"
echo "GCP_ARTIFACT_REPO=${AR_IMAGE_REPO}"
echo "ROLLMETRICS_GCS_BUCKET=${BUCKET_NAME}"
