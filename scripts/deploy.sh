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
#   4. Push to ECR (or other registry)
#   5. Deploy via Docker Compose OR Kubernetes
#   6. Health check with retries
#   7. Write state files (for /status command)
#   8. Auto-rollback on health check failure
#
# Exit codes:
#   0 = success
#   1 = failure (triggers auto-rollback in bot)
#
# BUGS FIXED:
#   Health-check loop off-by-one:
#     The original checked `if [[ ${ATTEMPT} -ge ${MAX_RETRIES} ]]; then exit 1`
#     INSIDE the while loop, AFTER the sleep.  On the last iteration the flow
#     was: attempt++, HTTP check fails, sleep, THEN check attempt>=max → exit 1.
#     This meant the loop always slept one extra time on the final attempt and
#     the log said "attempt 10/10 — waiting 10s" before exiting, which is
#     misleading (we already know we're done).
#
#     Fix: check the exhaustion condition at the TOP of the loop body, before
#     the sleep.  If we've already used all retries, exit immediately without
#     an extra sleep.  The structure is now:
#       1. increment counter
#       2. do the HTTP check → break on success
#       3. if counter >= max → exit 1 (no more retries left, don't sleep)
#       4. sleep and loop
# =============================================================================

set -euo pipefail  # Exit on error, undefined vars, pipe failures

# ── Variables ──────────────────────────────────────────────────────────────────
ENVIRONMENT="${1:?Usage: deploy.sh <environment> <commit>}"
COMMIT="${2:?Commit hash required}"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

STATE_DIR="/var/lib/deploybot"
REPO_DIR="/app/repo"

# Derive image tag from environment + commit
IMAGE_TAG="${REGISTRY_IMAGE}:${ENVIRONMENT}-${COMMIT}"
FULL_IMAGE="${REGISTRY_URL}/${IMAGE_TAG}"

# Color codes for log readability
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log_info()    { echo -e "${GREEN}[INFO]${NC}  $(date -u +%H:%M:%S) $*"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC}  $(date -u +%H:%M:%S) $*"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $(date -u +%H:%M:%S) $*" >&2; }

# ── Input Validation ───────────────────────────────────────────────────────────
log_info "=== Deployment Starting ==="
log_info "Environment : ${ENVIRONMENT}"
log_info "Commit      : ${COMMIT}"
log_info "Timestamp   : ${TIMESTAMP}"

# Whitelist environment values — prevents any injection via environment name
if [[ ! "${ENVIRONMENT}" =~ ^(staging|production)$ ]]; then
    log_error "Invalid environment '${ENVIRONMENT}'. Must be: staging or production"
    exit 1
fi

# Validate commit hash format (hex only)
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

ACTUAL_COMMIT=$(git rev-parse --short HEAD)
log_info "Checked out commit: ${ACTUAL_COMMIT}"

# ── Step 2: Build Docker Image ─────────────────────────────────────────────────
log_info "--- Step 2: Building Docker image ---"
log_info "Image: ${FULL_IMAGE}"

docker build \
    --file Dockerfile \
    --tag "${FULL_IMAGE}" \
    --build-arg BUILD_ENV="${ENVIRONMENT}" \
    --build-arg GIT_COMMIT="${ACTUAL_COMMIT}" \
    --build-arg BUILD_DATE="${TIMESTAMP}" \
    --label "git.commit=${ACTUAL_COMMIT}" \
    --label "deploy.environment=${ENVIRONMENT}" \
    --label "deploy.timestamp=${TIMESTAMP}" \
    --no-cache \
    .

log_info "Docker build successful."

# ── Step 3: Push to Registry ───────────────────────────────────────────────────
log_info "--- Step 3: Pushing image to registry ---"

# Authenticate to AWS ECR
aws ecr get-login-password --region "${AWS_REGION:-us-east-1}" \
    | docker login --username AWS --password-stdin "${REGISTRY_URL}"

docker push "${FULL_IMAGE}"
log_info "Push complete: ${FULL_IMAGE}"

# Also tag as 'latest' for this environment
docker tag "${FULL_IMAGE}" "${REGISTRY_URL}/${REGISTRY_IMAGE}:${ENVIRONMENT}-latest"
docker push "${REGISTRY_URL}/${REGISTRY_IMAGE}:${ENVIRONMENT}-latest"

# ── Step 4: Save previous image tag for rollback ───────────────────────────────
mkdir -p "${STATE_DIR}"
if [[ -f "${STATE_DIR}/${ENVIRONMENT}.image" ]]; then
    cp "${STATE_DIR}/${ENVIRONMENT}.image" "${STATE_DIR}/${ENVIRONMENT}.image.prev"
fi
echo "${FULL_IMAGE}" > "${STATE_DIR}/${ENVIRONMENT}.image"

# ── Step 5: Deploy ─────────────────────────────────────────────────────────────
log_info "--- Step 5: Deploying ---"

if [[ "${USE_KUBERNETES:-false}" == "true" ]]; then
    # ── Kubernetes Deployment ────────────────────────────────────────────────
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
    # ── Docker Compose Deployment ────────────────────────────────────────────
    if [[ "${ENVIRONMENT}" == "production" ]]; then
        TARGET_HOST="${PRODUCTION_HOST}"
    else
        TARGET_HOST="${STAGING_HOST}"
    fi

    log_info "Deploying to host: ${TARGET_HOST}"

    # SSH deploy — uses a dedicated deploy key (read-only, no password)
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

# BUG FIX: the original loop checked `ATTEMPT >= MAX_RETRIES` AFTER sleeping,
# which meant the final failure message was always preceded by an unnecessary
# sleep and printed "attempt 10/10 — waiting 10s" before exiting.
#
# New structure:
#   1. increment ATTEMPT
#   2. perform HTTP check → break on HTTP 200
#   3. if ATTEMPT >= MAX_RETRIES → log and exit (no extra sleep)
#   4. otherwise sleep and go back to step 1
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

    # BUG FIX: check exhaustion BEFORE sleeping so we don't waste time on the
    # last failed attempt.
    if [[ ${ATTEMPT} -ge ${MAX_RETRIES} ]]; then
        log_error "Health check FAILED after ${MAX_RETRIES} attempts. Triggering rollback."
        exit 1
    fi

    log_warn "Retrying in ${RETRY_DELAY}s..."
    sleep "${RETRY_DELAY}"
done

# ── Step 7: Write State Files ──────────────────────────────────────────────────
log_info "--- Step 7: Recording deployment state ---"
echo "${ACTUAL_COMMIT}" > "${STATE_DIR}/${ENVIRONMENT}.commit"
echo "${TIMESTAMP}" > "${STATE_DIR}/${ENVIRONMENT}.timestamp"

log_info "=== Deployment Complete ==="
log_info "Environment : ${ENVIRONMENT}"
log_info "Commit      : ${ACTUAL_COMMIT}"
log_info "Image       : ${FULL_IMAGE}"
log_info "Time        : ${TIMESTAMP}"

exit 0