#!/usr/bin/env bash
# rollback.sh - Rollback to Previous Deployment (unchanged — no bugs found)
set -euo pipefail

ENVIRONMENT="${1:?Usage: rollback.sh <environment>}"
STATE_DIR="/var/lib/deploybot"

RED='\033[0;31m'; GREEN='\033[0;32m'; NC='\033[0m'
log_info()  { echo -e "${GREEN}[ROLLBACK]${NC} $(date -u +%H:%M:%S) $*"; }
log_error() { echo -e "${RED}[ERROR]${NC}    $(date -u +%H:%M:%S) $*" >&2; }

if [[ ! "${ENVIRONMENT}" =~ ^(staging|production)$ ]]; then
    log_error "Invalid environment: ${ENVIRONMENT}"
    exit 1
fi

PREV_IMAGE_FILE="${STATE_DIR}/${ENVIRONMENT}.image.prev"

if [[ ! -f "${PREV_IMAGE_FILE}" ]]; then
    log_error "No previous image found for ${ENVIRONMENT}. Cannot rollback."
    exit 1
fi

PREV_IMAGE=$(cat "${PREV_IMAGE_FILE}")
log_info "Rolling back ${ENVIRONMENT} to image: ${PREV_IMAGE}"

if [[ "${USE_KUBERNETES:-false}" == "true" ]]; then
    KUBE_DEPLOYMENT="${KUBE_DEPLOYMENT_STAGING}"
    [[ "${ENVIRONMENT}" == "production" ]] && KUBE_DEPLOYMENT="${KUBE_DEPLOYMENT_PRODUCTION}"

    kubectl set image deployment/"${KUBE_DEPLOYMENT}" \
        app="${PREV_IMAGE}" \
        --namespace "${KUBE_NAMESPACE}"

    kubectl rollout status deployment/"${KUBE_DEPLOYMENT}" \
        --namespace "${KUBE_NAMESPACE}" \
        --timeout=300s
else
    TARGET_HOST="${STAGING_HOST}"
    [[ "${ENVIRONMENT}" == "production" ]] && TARGET_HOST="${PRODUCTION_HOST}"

    ssh -i "${SSH_KEY_PATH}" \
        -o StrictHostKeyChecking=no \
        -o ConnectTimeout=15 \
        "${DEPLOY_USER}@${TARGET_HOST}" \
        "
        export IMAGE_TAG='${PREV_IMAGE}'
        cd /opt/myapp
        docker compose -f docker-compose.yml up -d --remove-orphans
        "
fi

cp "${STATE_DIR}/${ENVIRONMENT}.image" "${STATE_DIR}/${ENVIRONMENT}.image.next"
mv "${STATE_DIR}/${ENVIRONMENT}.image.prev" "${STATE_DIR}/${ENVIRONMENT}.image"
mv "${STATE_DIR}/${ENVIRONMENT}.image.next" "${STATE_DIR}/${ENVIRONMENT}.image.prev"

log_info "Rollback complete. Current image: ${PREV_IMAGE}"
exit 0
