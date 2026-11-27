#!/usr/bin/env bash
# Build a leak-scanned, clean-history orphan v0.1.0 export of RollMetrics for a public GitHub Organization.
# Usage:
#   ./scripts/export_public_oss_repo.sh [--dry-run] [--org <github-org>] [--repo <repo-name>] [--output <dir>]
set -euo pipefail

DRY_RUN=true
ORG=""
REPO_NAME="rollmetrics"
OUTPUT_DIR=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --push)
      DRY_RUN=false
      shift
      ;;
    --org)
      ORG="${2:-}"
      shift 2
      ;;
    --repo)
      REPO_NAME="${2:-rollmetrics}"
      shift 2
      ;;
    --output)
      OUTPUT_DIR="${2:-}"
      shift 2
      ;;
    -h|--help)
      echo "Usage: $0 [--dry-run | --push] [--org <github-org>] [--repo <repo-name>] [--output <dir>]"
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_REPO="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [ -z "${OUTPUT_DIR}" ]; then
  OUTPUT_DIR="$(mktemp -d "/tmp/${REPO_NAME}-oss-export.XXXXXX")"
else
  rm -rf "${OUTPUT_DIR}"
  mkdir -p "${OUTPUT_DIR}"
fi

echo "==> Exporting tracked files from ${SRC_REPO} to ${OUTPUT_DIR}"
git -C "${SRC_REPO}" archive --format=tar HEAD | tar -x -C "${OUTPUT_DIR}"

echo "==> Removing any runtime or media artifacts if present"
rm -rf "${OUTPUT_DIR}/data" "${OUTPUT_DIR}/.env" "${OUTPUT_DIR}/.venv" "${OUTPUT_DIR}/frontend/node_modules" "${OUTPUT_DIR}/frontend/dist"

echo "==> Running PII, secret, and large-media hygiene scan on export"
LEAK_FOUND=0

check_pattern() {
  local label="$1"
  local pattern="$2"
  if grep -rnE --exclude-dir=.git --exclude="ci.yml" --exclude="export_public_oss_repo.sh" --exclude="*.sqlite" "${pattern}" "${OUTPUT_DIR}" >/tmp/oss_leak_hits.txt 2>/dev/null; then
    echo "FAIL [${label}]:"
    head -n 20 /tmp/oss_leak_hits.txt
    LEAK_FOUND=1
  fi
}

check_pattern "personal-email" "korymath[@]gmail\.com"
check_pattern "gcp-project-number" "83719080477[4]"
check_pattern "gcp-billing-id" "018CC6-28519[7]|01459F-000A4[8]"
check_pattern "oauth-client-id" "4910799780[0]-1ku6csls"
check_pattern "cloud-run-hash" "cyey7gzfs[q]"
check_pattern "google-api-key" "AIza[0-9A-Za-z_-]{30,}"
check_pattern "google-oauth-secret" "GOCSPX-[0-9A-Za-z_-]{20,}"
EM_DASH="$(printf '\xe2\x80\x94')"
check_pattern "unicode-em-dash" "${EM_DASH}"

rm -f /tmp/oss_leak_hits.txt

if find "${OUTPUT_DIR}" -type f \( -name "*.mp4" -o -name "*.mov" -o -name "*.mkv" -o -name ".env" \) ! -path "${OUTPUT_DIR}/frontend/public/landing-preview.mp4" | grep -q .; then
  echo "FAIL [media-or-env]: Found raw media or .env file in export"
  LEAK_FOUND=1
fi

if [ "${LEAK_FOUND}" -ne 0 ]; then
  echo "Export hygiene scan FAILED. Aborting." >&2
  exit 1
fi
echo "PASS: Export hygiene scan clean."

echo "==> Initializing clean orphan git history (v0.1.0)"
git -C "${OUTPUT_DIR}" init -b main >/dev/null
git -C "${OUTPUT_DIR}" add -A
git -C "${OUTPUT_DIR}" commit -m "Initial public open-source release of RollMetrics v0.1.0" >/dev/null
git -C "${OUTPUT_DIR}" tag -a v0.1.0 -m "RollMetrics v0.1.0"

if [ "${DRY_RUN}" = true ] || [ -z "${ORG}" ]; then
  echo "==> Dry run complete. Clean orphan repo ready at: ${OUTPUT_DIR}"
  if [ -n "${ORG}" ]; then
    echo "To push to https://github.com/${ORG}/${REPO_NAME}, re-run with --push --org ${ORG} --repo ${REPO_NAME}"
  fi
  exit 0
fi

REMOTE_URL="git@github.com:${ORG}/${REPO_NAME}.git"
echo "==> Pushing orphan main and v0.1.0 tag to ${REMOTE_URL}"
git -C "${OUTPUT_DIR}" remote add origin "${REMOTE_URL}"
git -C "${OUTPUT_DIR}" push -u origin main --tags
echo "==> Public OSS export pushed to ${REMOTE_URL}"
