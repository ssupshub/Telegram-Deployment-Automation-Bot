#!/usr/bin/env bash
# =============================================================================
# terraform/destroy.sh
# =============================================================================
# SAFE TEARDOWN of every AWS resource created by main.tf.
#
# USAGE:
#   cd terraform/
#   bash destroy.sh                          # interactive (recommended)
#   bash destroy.sh --force                  # skip confirmation prompt (CI use)
#   bash destroy.sh --dry-run                # print commands, execute nothing
#   bash destroy.sh --region eu-west-1       # override AWS region
#   bash destroy.sh --project my-deploy-bot  # override project name prefix
# =============================================================================

set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────────
REGION="us-east-1"
PROJECT="deploy-bot"
ECR_REPO_NAME="myapp"
FORCE=false
DRY_RUN=false

# ── Colours ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log_info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
log_step()    { echo -e "\n${CYAN}${BOLD}══ $* ${NC}"; }
log_dry()     { echo -e "${YELLOW}[DRY-RUN]${NC} would run: $*"; }

# ── Argument parsing ───────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force)       FORCE=true;          shift ;;
    --dry-run)     DRY_RUN=true;        shift ;;
    --region)      REGION="$2";         shift 2 ;;
    --project)     PROJECT="$2";        shift 2 ;;
    --ecr-repo)    ECR_REPO_NAME="$2";  shift 2 ;;
    *)
      log_error "Unknown argument: $1"
      echo "Usage: $0 [--force] [--dry-run] [--region REGION] [--project PROJECT] [--ecr-repo REPO]"
      exit 1
      ;;
  esac
done

# ── Helpers ────────────────────────────────────────────────────────────────────
run() {
  if [[ "${DRY_RUN}" == "true" ]]; then
    log_dry "$*"
  else
    "$@"
  fi
}

aws_safe() {
  if [[ "${DRY_RUN}" == "true" ]]; then
    log_dry "aws $*"
    return 0
  fi
  aws "$@" 2>/dev/null || true
}

terminate_instance() {
  local name_tag="$1"
  log_info "Looking up instance: ${name_tag}"
  local instance_id
  instance_id=$(aws ec2 describe-instances \
    --region "${REGION}" \
    --filters "Name=tag:Name,Values=${name_tag}" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
    --query "Reservations[0].Instances[0].InstanceId" \
    --output text 2>/dev/null || echo "None")

  if [[ "${instance_id}" == "None" || -z "${instance_id}" ]]; then
    log_warn "Instance '${name_tag}' not found — skipping."
    return
  fi

  log_info "Terminating instance ${instance_id} (${name_tag})..."
  run aws ec2 terminate-instances --region "${REGION}" --instance-ids "${instance_id}" > /dev/null
  if [[ "${DRY_RUN}" == "false" ]]; then
    log_info "Waiting for ${instance_id} to terminate (this can take ~60s)..."
    aws ec2 wait instance-terminated --region "${REGION}" --instance-ids "${instance_id}"
    log_info "Instance ${instance_id} terminated."
  fi
}

delete_role_policy() {
  local role="$1"
  local policy="$2"
  log_info "Deleting inline policy '${policy}' from role '${role}'..."
  aws_safe iam delete-role-policy --role-name "${role}" --policy-name "${policy}"
}

delete_instance_profile() {
  local profile_name="$1"
  local role_name="$2"
  log_info "Removing role '${role_name}' from instance profile '${profile_name}'..."
  aws_safe iam remove-role-from-instance-profile \
    --instance-profile-name "${profile_name}" \
    --role-name "${role_name}"
  log_info "Deleting instance profile '${profile_name}'..."
  aws_safe iam delete-instance-profile --instance-profile-name "${profile_name}"
}

delete_role() {
  local role="$1"
  log_info "Deleting IAM role '${role}'..."
  aws_safe iam delete-role --role-name "${role}"
}

get_vpc_id() {
  aws ec2 describe-vpcs \
    --region "${REGION}" \
    --filters "Name=tag:Name,Values=${PROJECT}-vpc" \
    --query "Vpcs[0].VpcId" \
    --output text 2>/dev/null || echo "None"
}

# ── Pre-flight checks ──────────────────────────────────────────────────────────
log_step "Pre-flight checks"

if ! command -v aws &>/dev/null; then
  log_error "AWS CLI not found."
  exit 1
fi

if ! command -v jq &>/dev/null; then
  log_error "'jq' is required but not installed."
  exit 1
fi

TF_AVAILABLE=false
command -v terraform &>/dev/null && TF_AVAILABLE=true

AWS_ACCOUNT=$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo "unknown")
log_info "AWS account : ${AWS_ACCOUNT}"
log_info "Region      : ${REGION}"
log_info "Project     : ${PROJECT}"
log_info "Dry-run     : ${DRY_RUN}"

# ── Confirmation prompt ────────────────────────────────────────────────────────
if [[ "${FORCE}" == "false" && "${DRY_RUN}" == "false" ]]; then
  echo ""
  echo -e "${RED}${BOLD}╔══════════════════════════════════════════════════════════════╗${NC}"
  echo -e "${RED}${BOLD}║          ⚠️   DESTRUCTIVE OPERATION — NO UNDO   ⚠️            ║${NC}"
  echo -e "${RED}${BOLD}╚══════════════════════════════════════════════════════════════╝${NC}"
  echo ""
  echo "  This will PERMANENTLY DELETE all AWS resources for project: ${PROJECT}"
  echo "  AWS account : ${AWS_ACCOUNT}"
  echo "  Region      : ${REGION}"
  echo ""
  read -rp "  Type DESTROY to confirm: " confirmation
  if [[ "${confirmation}" != "DESTROY" ]]; then
    log_info "Aborted — nothing was deleted."
    exit 0
  fi
  echo ""
fi

# ── Step 1: ECR images ─────────────────────────────────────────────────────────
log_step "Step 1/14 — Delete all ECR images from '${ECR_REPO_NAME}'"
if [[ "${DRY_RUN}" == "false" ]]; then
  IMAGE_IDS=$(aws ecr list-images \
    --region "${REGION}" \
    --repository-name "${ECR_REPO_NAME}" \
    --query "imageIds[*]" \
    --output json 2>/dev/null || echo "[]")
  IMAGE_COUNT=$(echo "${IMAGE_IDS}" | jq 'length')
  if [[ "${IMAGE_COUNT}" -gt 0 ]]; then
    log_info "Deleting ${IMAGE_COUNT} image(s) from ECR..."
    aws ecr batch-delete-image \
      --region "${REGION}" \
      --repository-name "${ECR_REPO_NAME}" \
      --image-ids "${IMAGE_IDS}" > /dev/null
  fi
else
  log_dry "aws ecr list-images ... | aws ecr batch-delete-image ..."
fi

log_step "Step 2/14 — Terminate EC2 staging instance"
terminate_instance "${PROJECT}-staging"

log_step "Step 3/14 — Terminate EC2 production instance"
terminate_instance "${PROJECT}-production"

if [[ "${DRY_RUN}" == "false" ]]; then
  log_info "Waiting 15s for network interfaces to be released..."
  sleep 15
fi

log_step "Step 4/14 — Remove inline policies from EC2 IAM role"
delete_role_policy "${PROJECT}-ec2-role" "ECRPullPolicy"

log_step "Step 5/14 — Remove inline policies from GitHub Actions IAM role"
delete_role_policy "${PROJECT}-github-actions-role" "GitHubActionsDeployPolicy"

log_step "Step 6/14 — Delete IAM instance profile"
delete_instance_profile "${PROJECT}-ec2-profile" "${PROJECT}-ec2-role"

log_step "Step 7/14 — Delete EC2 IAM role"
delete_role "${PROJECT}-ec2-role"

log_step "Step 8/14 — Delete GitHub Actions IAM role"
delete_role "${PROJECT}-github-actions-role"

log_step "Step 9/14 — Delete ECR lifecycle policy and repository"
aws_safe ecr delete-lifecycle-policy --region "${REGION}" --repository-name "${ECR_REPO_NAME}"
run aws ecr delete-repository --region "${REGION}" --repository-name "${ECR_REPO_NAME}" --force > /dev/null 2>/dev/null || true

log_step "Step 10/14 — Delete SSH key pair"
aws_safe ec2 delete-key-pair --region "${REGION}" --key-name "${PROJECT}-key"

log_step "Step 11/14 — Delete security group"
if [[ "${DRY_RUN}" == "false" ]]; then
  SG_ID=$(aws ec2 describe-security-groups \
    --region "${REGION}" \
    --filters "Name=group-name,Values=${PROJECT}-sg" \
    --query "SecurityGroups[0].GroupId" \
    --output text 2>/dev/null || echo "None")
  if [[ "${SG_ID}" != "None" && -n "${SG_ID}" ]]; then
    for attempt in 1 2 3 4 5; do
      if aws ec2 delete-security-group --region "${REGION}" --group-id "${SG_ID}" 2>/dev/null; then
        log_info "Security group deleted."
        break
      fi
      log_warn "Attempt ${attempt}/5 failed — waiting 10s..."
      sleep 10
    done
  fi
fi

log_step "Step 12/14 — Delete route table and subnet"
if [[ "${DRY_RUN}" == "false" ]]; then
  VPC_ID=$(get_vpc_id)
  if [[ "${VPC_ID}" != "None" && -n "${VPC_ID}" ]]; then
    ASSOC_IDS=$(aws ec2 describe-route-tables \
      --region "${REGION}" \
      --filters "Name=vpc-id,Values=${VPC_ID}" "Name=tag:Name,Values=${PROJECT}-*" \
      --query "RouteTables[*].Associations[?Main==\`false\`].RouteTableAssociationId" \
      --output text 2>/dev/null || true)
    for assoc_id in ${ASSOC_IDS}; do
      aws_safe ec2 disassociate-route-table --region "${REGION}" --association-id "${assoc_id}"
    done
    RT_IDS=$(aws ec2 describe-route-tables \
      --region "${REGION}" \
      --filters "Name=vpc-id,Values=${VPC_ID}" "Name=tag:Name,Values=${PROJECT}-*" \
      --query "RouteTables[*].RouteTableId" \
      --output text 2>/dev/null || true)
    for rt_id in ${RT_IDS}; do
      aws_safe ec2 delete-route-table --region "${REGION}" --route-table-id "${rt_id}"
    done
    SUBNET_IDS=$(aws ec2 describe-subnets \
      --region "${REGION}" \
      --filters "Name=vpc-id,Values=${VPC_ID}" \
      --query "Subnets[*].SubnetId" \
      --output text 2>/dev/null || true)
    for subnet_id in ${SUBNET_IDS}; do
      aws_safe ec2 delete-subnet --region "${REGION}" --subnet-id "${subnet_id}"
    done
  fi
fi

log_step "Step 13/14 — Delete internet gateway"
if [[ "${DRY_RUN}" == "false" ]]; then
  VPC_ID=$(get_vpc_id)
  if [[ "${VPC_ID}" != "None" && -n "${VPC_ID}" ]]; then
    IGW_ID=$(aws ec2 describe-internet-gateways \
      --region "${REGION}" \
      --filters "Name=attachment.vpc-id,Values=${VPC_ID}" \
      --query "InternetGateways[0].InternetGatewayId" \
      --output text 2>/dev/null || echo "None")
    if [[ "${IGW_ID}" != "None" && -n "${IGW_ID}" ]]; then
      aws_safe ec2 detach-internet-gateway --region "${REGION}" --internet-gateway-id "${IGW_ID}" --vpc-id "${VPC_ID}"
      aws_safe ec2 delete-internet-gateway --region "${REGION}" --internet-gateway-id "${IGW_ID}"
    fi
  fi
fi

log_step "Step 14/14 — Delete VPC"
if [[ "${DRY_RUN}" == "false" ]]; then
  VPC_ID=$(get_vpc_id)
  if [[ "${VPC_ID}" != "None" && -n "${VPC_ID}" ]]; then
    aws_safe ec2 delete-vpc --region "${REGION}" --vpc-id "${VPC_ID}"
    log_info "VPC deleted."
  fi
fi

log_step "Optional — terraform destroy"
if [[ "${TF_AVAILABLE}" == "true" && -f "main.tf" && "${DRY_RUN}" == "false" ]]; then
  terraform destroy -refresh=false -auto-approve \
    -var "ssh_public_key=placeholder" \
    -var "github_org=placeholder" \
    -var "github_repo=placeholder" \
    2>&1 | grep -v "prevent_destroy" || true
fi

echo ""
echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}${BOLD}║              ✅  Teardown complete                           ║${NC}"
echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
if [[ "${DRY_RUN}" == "true" ]]; then
  echo -e "  ${YELLOW}DRY-RUN mode — no changes were made.${NC}"
fi
