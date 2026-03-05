#!/usr/bin/env bash
# =============================================================================
# deploy.sh - Full Deployment Pipeline
# =============================================================================
# Usage: ./deploy.sh <environment> <commit_hash>
#
# Pipeline:
#   1. Validate inputs
#   2. Pull latest code from GitHub
#   3. Build Docker image
#   4. Push to ECR
#   5. Deploy via Docker Compose OR Kubernetes
#   6. Health check with retries
#   7. Write state files ONLY after health check passes (fix #4)
#
# Exit codes:
#   0 = success
#   1 = failure (triggers auto-rollback in bot)
# =============================================================================

set -euo pipefail

# ── Variables ──────────────────────────────────────────────────────────────────
ENVIRONMENT="${1:?Usage: deploy.sh <environment> <commit>}"
COMMIT="${2:?Commit hash required}"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

STATE_DIR="/var/lib/deploybot"
REPO_DIR="/app/repo"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log_info()  { echo -e "${GREEN}[INFO]${NC}  $(date -u +%H:%M:%S) $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $(date -u +%H:%M:%S) $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $(date -u +%H:%M:%S) $*" >&2; }

# ── Input Validation ───────────────────────────────────────────────────────────
log_info "=== Deployment Starting ==="
log_info "Environment : ${ENVIRONMENT}"
log_info "Commit      : ${COMMIT}"
log_info "Timestamp   : ${TIMESTAMP}"

if [[ ! "${ENVIRONMENT}" =~ ^(staging|production)$ ]]; then
    log_error "Invalid environment '${ENVIRONMENT}'. Must be: staging or production"
    exit 1
fi

if [[ ! "${COMMIT}" =~ ^[0-9a-f]{4,40}$ ]]; then
    log_error "Invalid commit hash format: '${COMMIT}'"
    exit 1
fi

# ── Step 1: Pull Latest Code ───────────────────────────────────────────────────
log_info "--- Step 1: Pulling latest code ---"

if [[ "${ENVIRONMENT}" == "production" ]]; then
    BRANCH="main"
else
    BRANCH="develop"
fi

cd "${REPO_DIR}"
git fetch origin
git checkout "${BRANCH}"
git pull origin "${BRANCH}"

# Fix #12: use COMMIT (the bot-supplied value) as the single source of truth
# for both the image tag and the state files. Previously ACTUAL_COMMIT was
# used for state files while COMMIT was used for the image tag, causing a
# mismatch that made /status show a commit that didn't match the running image.
#
# We still log what HEAD resolved to for traceability, but do not use it as
# a second source of truth.
HEAD_COMMIT=$(git rev-parse --short HEAD)
log_info "Requested commit : ${COMMIT}"
log_info "HEAD after pull  : ${HEAD_COMMIT}"

# ── Step 2: Build Docker Image ─────────────────────────────────────────────────
log_info "--- Step 2: Building Docker image ---"

IMAGE_TAG="${REGISTRY_IMAGE}:${ENVIRONMENT}-${COMMIT}"
FULL_IMAGE="${REGISTRY_URL}/${IMAGE_TAG}"
log_info "Image: ${FULL_IMAGE}"

docker build \
    --file Dockerfile \
    --tag "${FULL_IMAGE}" \
    --build-arg BUILD_ENV="${ENVIRONMENT}" \
    --build-arg GIT_COMMIT="${COMMIT}" \
    --build-arg BUILD_DATE="${TIMESTAMP}" \
    --label "git.commit=${COMMIT}" \
    --label "deploy.environment=${ENVIRONMENT}" \
    --label "deploy.timestamp=${TIMESTAMP}" \
    --no-cache \
    .

log_info "Docker build successful."

# ── Step 3: Push to Registry ───────────────────────────────────────────────────
log_info "--- Step 3: Pushing image to registry ---"

aws ecr get-login-password --region "${AWS_REGION:-us-east-1}" \
    | docker login --username AWS --password-stdin "${REGISTRY_URL}"

docker push "${FULL_IMAGE}"
log_info "Push complete: ${FULL_IMAGE}"

docker tag "${FULL_IMAGE}" "${REGISTRY_URL}/${REGISTRY_IMAGE}:${ENVIRONMENT}-latest"
docker push "${REGISTRY_URL}/${REGISTRY_IMAGE}:${ENVIRONMENT}-latest"

# ── Step 4: Save previous image tag for rollback ───────────────────────────────
# NOTE: we save the rollback pointer here (before deploy) so rollback.sh always
# has something to revert to. However, we do NOT write the commit/timestamp
# state files until AFTER the health check passes (fix #4) — that way /status
# always reflects the last KNOWN-GOOD deployment, not an in-flight one.
mkdir -p "${STATE_DIR}"
if [[ -f "${STATE_DIR}/${ENVIRONMENT}.image" ]]; then
    cp "${STATE_DIR}/${ENVIRONMENT}.image" "${STATE_DIR}/${ENVIRONMENT}.image.prev"
fi
echo "${FULL_IMAGE}" > "${STATE_DIR}/${ENVIRONMENT}.image"

# ── Step 5: Deploy ─────────────────────────────────────────────────────────────
log_info "--- Step 5: Deploying ---"

if [[ "${USE_KUBERNETES:-false}" == "true" ]]; then
    log_info "Deploying to Kubernetes namespace: ${KUBE_NAMESPACE}"

    if [[ "${ENVIRONMENT}" == "production" ]]; then
        KUBE_DEPLOYMENT="${KUBE_DEPLOYMENT_PRODUCTION}"
    else
        KUBE_DEPLOYMENT="${KUBE_DEPLOYMENT_STAGING}"
    fi

    kubectl set image deployment/"${KUBE_DEPLOYMENT}" \
        app="${FULL_IMAGE}" \
        --namespace "${KUBE_NAMESPACE}"

    log_info "Waiting for rollout to complete..."
    kubectl rollout status deployment/"${KUBE_DEPLOYMENT}" \
        --namespace "${KUBE_NAMESPACE}" \
        --timeout=300s

else
    if [[ "${ENVIRONMENT}" == "production" ]]; then
        TARGET_HOST="${PRODUCTION_HOST}"
    else
        TARGET_HOST="${STAGING_HOST}"
    fi

    log_info "Deploying to host: ${TARGET_HOST}"

    ssh -i "${SSH_KEY_PATH}" \
        -o StrictHostKeyChecking=no \
        -o ConnectTimeout=15 \
        "${DEPLOY_USER}@${TARGET_HOST}" \
        "
        export IMAGE_TAG='${FULL_IMAGE}'
        cd /opt/myapp
        docker compose -f docker-compose.yml pull
        docker compose -f docker-compose.yml up -d --remove-orphans
        docker system prune -f --filter 'until=24h'
        "
fi

log_info "Deployment commands completed."

# ── Step 6: Health Check ───────────────────────────────────────────────────────
log_info "--- Step 6: Health check ---"

if [[ "${ENVIRONMENT}" == "production" ]]; then
    HEALTH_URL="${PRODUCTION_HEALTH_URL}"
else
    HEALTH_URL="${STAGING_HEALTH_URL}"
fi

MAX_RETRIES=10
RETRY_DELAY=10
ATTEMPT=0

log_info "Polling health endpoint: ${HEALTH_URL}"

while true; do
    ATTEMPT=$((ATTEMPT + 1))
    log_info "Health check attempt ${ATTEMPT}/${MAX_RETRIES}..."

    HTTP_STATUS=$(curl --silent --output /dev/null --write-out "%{http_code}" \
        --max-time 5 \
        --location \
        "${HEALTH_URL}" || echo "000")

    if [[ "${HTTP_STATUS}" == "200" ]]; then
        log_info "Health check PASSED (HTTP ${HTTP_STATUS})"
        break
    fi

    log_warn "Health check FAILED (HTTP ${HTTP_STATUS})."

    if [[ ${ATTEMPT} -ge ${MAX_RETRIES} ]]; then
        log_error "Health check FAILED after ${MAX_RETRIES} attempts. Triggering rollback."
        exit 1
    fi

    log_warn "Retrying in ${RETRY_DELAY}s..."
    sleep "${RETRY_DELAY}"
done

# ── Step 7: Write State Files (ONLY after health check passes) ─────────────────
# Fix #4: state files are written here — AFTER a successful health check —
# not before deployment. This means rollback.sh always reads the last
# known-good commit, not one that just failed a health check.
log_info "--- Step 7: Recording deployment state ---"
echo "${COMMIT}" > "${STATE_DIR}/${ENVIRONMENT}.commit"
echo "${TIMESTAMP}" > "${STATE_DIR}/${ENVIRONMENT}.timestamp"

log_info "=== Deployment Complete ==="
log_info "Environment : ${ENVIRONMENT}"
log_info "Commit      : ${COMMIT}"
log_info "Image       : ${FULL_IMAGE}"
log_info "Time        : ${TIMESTAMP}"

exit 0
