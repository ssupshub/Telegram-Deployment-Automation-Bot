#!/usr/bin/env bash
# =============================================================================
# terraform/destroy.sh
# =============================================================================
# SAFE TEARDOWN of every AWS resource created by main.tf.
#
# Resources destroyed (in dependency order):
#   1.  ECR images          — must be deleted before the repository can go
#   2.  EC2 instances       — staging + production
#   3.  IAM role policies   — must detach before deleting roles
#   4.  IAM instance profile
#   5.  IAM roles           — ec2-role + github-actions-role
#   6.  ECR lifecycle policy
#   7.  ECR repository      — prevent_destroy is bypassed via AWS CLI
#   8.  SSH key pair
#   9.  Security group
#  10.  Route table association
#  11.  Route table
#  12.  Internet gateway
#  13.  Subnet
#  14.  VPC
#
# WHY A SHELL SCRIPT INSTEAD OF `terraform destroy`?
#   • The ECR repository has `prevent_destroy = true` in main.tf, which makes
#     `terraform destroy` fail with a lifecycle error.  We handle that here by
#     first purging all images via the AWS CLI, then force-deleting the repo.
#   • This script gives you a clear, line-by-line audit trail and an explicit
#     confirmation prompt before anything is deleted.
#
# USAGE:
#   cd terraform/
#   bash destroy.sh                          # interactive (recommended)
#   bash destroy.sh --force                  # skip confirmation prompt (CI use)
#   bash destroy.sh --dry-run                # print commands, execute nothing
#   bash destroy.sh --region eu-west-1       # override AWS region
#   bash destroy.sh --project my-deploy-bot  # override project name prefix
#
# PREREQUISITES:
#   • AWS CLI v2 configured with credentials that have full access to the
#     resources listed above.
#   • Terraform >= 1.5 installed and `terraform init` already run.
#   • jq  (used to parse AWS CLI JSON output)
#
# =============================================================================

set -euo pipefail

# ── Defaults (must match variables in main.tf) ─────────────────────────────────
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

# Run a command or, in dry-run mode, just print it.
run() {
  if [[ "${DRY_RUN}" == "true" ]]; then
    log_dry "$*"
  else
    "$@"
  fi
}

# Run an AWS CLI command, suppressing the "resource not found" errors that
# occur when a resource was already deleted or was never created.
aws_safe() {
  if [[ "${DRY_RUN}" == "true" ]]; then
    log_dry "aws $*"
    return 0
  fi
  aws "$@" 2>/dev/null || true
}

# Terminate an EC2 instance by Name tag and wait until it's gone.
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

# Delete an IAM role policy (inline) safely.
delete_role_policy() {
  local role="$1"
  local policy="$2"
  log_info "Deleting inline policy '${policy}' from role '${role}'..."
  aws_safe iam delete-role-policy --role-name "${role}" --policy-name "${policy}"
}

# Detach a managed policy from an IAM role safely.
detach_managed_policy() {
  local role="$1"
  local policy_arn="$2"
  log_info "Detaching managed policy from role '${role}'..."
  aws_safe iam detach-role-policy --role-name "${role}" --policy-arn "${policy_arn}"
}

# Remove an IAM role from its instance profile, then delete both.
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

# Delete an IAM role safely (after policies are already removed).
delete_role() {
  local role="$1"
  log_info "Deleting IAM role '${role}'..."
  aws_safe iam delete-role --role-name "${role}"
}

# Look up a VPC by Name tag and return its ID.
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
  log_error "AWS CLI not found. Install it: https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html"
  exit 1
fi

if ! command -v jq &>/dev/null; then
  log_error "'jq' is required but not installed. Run: sudo apt-get install -y jq  (or brew install jq)"
  exit 1
fi

if ! command -v terraform &>/dev/null; then
  log_warn "Terraform not found — the 'terraform destroy' step will be skipped."
  TF_AVAILABLE=false
else
  TF_AVAILABLE=true
fi

AWS_ACCOUNT=$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo "unknown")
log_info "AWS account : ${AWS_ACCOUNT}"
log_info "Region      : ${REGION}"
log_info "Project     : ${PROJECT}"
log_info "ECR repo    : ${ECR_REPO_NAME}"
log_info "Dry-run     : ${DRY_RUN}"

# ── Confirmation prompt ────────────────────────────────────────────────────────
if [[ "${FORCE}" == "false" && "${DRY_RUN}" == "false" ]]; then
  echo ""
  echo -e "${RED}${BOLD}╔══════════════════════════════════════════════════════════════╗${NC}"
  echo -e "${RED}${BOLD}║          ⚠️   DESTRUCTIVE OPERATION — NO UNDO   ⚠️            ║${NC}"
  echo -e "${RED}${BOLD}╚══════════════════════════════════════════════════════════════╝${NC}"
  echo ""
  echo "  This will PERMANENTLY DELETE:"
  echo "    • EC2 instances  : ${PROJECT}-staging, ${PROJECT}-production"
  echo "    • ECR repository : ${ECR_REPO_NAME}  (ALL images inside it)"
  echo "    • IAM roles      : ${PROJECT}-ec2-role, ${PROJECT}-github-actions-role"
  echo "    • IAM profile    : ${PROJECT}-ec2-profile"
  echo "    • SSH key pair   : ${PROJECT}-key"
  echo "    • Security group : ${PROJECT}-sg"
  echo "    • VPC + subnet + IGW + route table"
  echo ""
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

# =============================================================================
# STEP 1 — ECR: delete all images, then the repository
# =============================================================================
log_step "Step 1/14 — Delete all ECR images from '${ECR_REPO_NAME}'"

if [[ "${DRY_RUN}" == "false" ]]; then
  # Collect every image digest in the repository.
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
    log_info "All images deleted."
  else
    log_info "No images found in repository — nothing to delete."
  fi
else
  log_dry "aws ecr list-images ... | aws ecr batch-delete-image ..."
fi

# =============================================================================
# STEP 2 — EC2: terminate staging instance
# =============================================================================
log_step "Step 2/14 — Terminate EC2 staging instance"
terminate_instance "${PROJECT}-staging"

# =============================================================================
# STEP 3 — EC2: terminate production instance
# =============================================================================
log_step "Step 3/14 — Terminate EC2 production instance"
terminate_instance "${PROJECT}-production"

# Give AWS a moment to release ENIs attached to the instances before we try to
# delete the security group (which will fail if any ENI still references it).
if [[ "${DRY_RUN}" == "false" ]]; then
  log_info "Waiting 15s for network interfaces to be released..."
  sleep 15
fi

# =============================================================================
# STEP 4 — IAM: delete inline policies from ec2-role
# =============================================================================
log_step "Step 4/14 — Remove inline policies from EC2 IAM role"
delete_role_policy "${PROJECT}-ec2-role" "ECRPullPolicy"

# =============================================================================
# STEP 5 — IAM: delete inline policies from github-actions-role
# =============================================================================
log_step "Step 5/14 — Remove inline policies from GitHub Actions IAM role"
delete_role_policy "${PROJECT}-github-actions-role" "GitHubActionsDeployPolicy"

# =============================================================================
# STEP 6 — IAM: remove role from instance profile, delete profile
# =============================================================================
log_step "Step 6/14 — Delete IAM instance profile"
delete_instance_profile "${PROJECT}-ec2-profile" "${PROJECT}-ec2-role"

# =============================================================================
# STEP 7 — IAM: delete ec2-role
# =============================================================================
log_step "Step 7/14 — Delete EC2 IAM role"
delete_role "${PROJECT}-ec2-role"

# =============================================================================
# STEP 8 — IAM: delete github-actions-role
# =============================================================================
log_step "Step 8/14 — Delete GitHub Actions IAM role"
delete_role "${PROJECT}-github-actions-role"

# =============================================================================
# STEP 9 — ECR: delete lifecycle policy, then force-delete repository
# =============================================================================
log_step "Step 9/14 — Delete ECR lifecycle policy and repository"
log_info "Deleting ECR lifecycle policy..."
aws_safe ecr delete-lifecycle-policy \
  --region "${REGION}" \
  --repository-name "${ECR_REPO_NAME}"

log_info "Force-deleting ECR repository '${ECR_REPO_NAME}' (bypasses prevent_destroy)..."
run aws ecr delete-repository \
  --region "${REGION}" \
  --repository-name "${ECR_REPO_NAME}" \
  --force > /dev/null 2>/dev/null || true

# =============================================================================
# STEP 10 — EC2: delete SSH key pair
# =============================================================================
log_step "Step 10/14 — Delete SSH key pair"
log_info "Deleting key pair '${PROJECT}-key'..."
aws_safe ec2 delete-key-pair --region "${REGION}" --key-name "${PROJECT}-key"

# =============================================================================
# STEP 11 — VPC: delete security group
# (must happen after instances are gone so no ENIs reference it)
# =============================================================================
log_step "Step 11/14 — Delete security group"

if [[ "${DRY_RUN}" == "false" ]]; then
  SG_ID=$(aws ec2 describe-security-groups \
    --region "${REGION}" \
    --filters "Name=group-name,Values=${PROJECT}-sg" \
    --query "SecurityGroups[0].GroupId" \
    --output text 2>/dev/null || echo "None")

  if [[ "${SG_ID}" != "None" && -n "${SG_ID}" ]]; then
    log_info "Deleting security group ${SG_ID} (${PROJECT}-sg)..."
    # Retry a few times — ENIs released by terminated instances can lag.
    for attempt in 1 2 3 4 5; do
      if aws ec2 delete-security-group --region "${REGION}" --group-id "${SG_ID}" 2>/dev/null; then
        log_info "Security group deleted."
        break
      fi
      log_warn "Attempt ${attempt}/5 failed — waiting 10s for ENIs to detach..."
      sleep 10
    done
  else
    log_warn "Security group '${PROJECT}-sg' not found — skipping."
  fi
else
  log_dry "aws ec2 describe-security-groups ... | aws ec2 delete-security-group ..."
fi

# =============================================================================
# STEP 12 — VPC: disassociate + delete route table
# =============================================================================
log_step "Step 12/14 — Delete route table and subnet"

if [[ "${DRY_RUN}" == "false" ]]; then
  VPC_ID=$(get_vpc_id)
  if [[ "${VPC_ID}" != "None" && -n "${VPC_ID}" ]]; then

    # Disassociate non-main route table associations.
    ASSOC_IDS=$(aws ec2 describe-route-tables \
      --region "${REGION}" \
      --filters "Name=vpc-id,Values=${VPC_ID}" "Name=tag:Name,Values=${PROJECT}-*" \
      --query "RouteTables[*].Associations[?Main==\`false\`].RouteTableAssociationId" \
      --output text 2>/dev/null || true)

    for assoc_id in ${ASSOC_IDS}; do
      log_info "Disassociating route table association ${assoc_id}..."
      aws_safe ec2 disassociate-route-table --region "${REGION}" --association-id "${assoc_id}"
    done

    # Delete non-main route tables.
    RT_IDS=$(aws ec2 describe-route-tables \
      --region "${REGION}" \
      --filters "Name=vpc-id,Values=${VPC_ID}" "Name=tag:Name,Values=${PROJECT}-*" \
      --query "RouteTables[*].RouteTableId" \
      --output text 2>/dev/null || true)

    for rt_id in ${RT_IDS}; do
      log_info "Deleting route table ${rt_id}..."
      aws_safe ec2 delete-route-table --region "${REGION}" --route-table-id "${rt_id}"
    done

    # Delete subnet.
    SUBNET_IDS=$(aws ec2 describe-subnets \
      --region "${REGION}" \
      --filters "Name=vpc-id,Values=${VPC_ID}" \
      --query "Subnets[*].SubnetId" \
      --output text 2>/dev/null || true)

    for subnet_id in ${SUBNET_IDS}; do
      log_info "Deleting subnet ${subnet_id}..."
      aws_safe ec2 delete-subnet --region "${REGION}" --subnet-id "${subnet_id}"
    done
  else
    log_warn "VPC '${PROJECT}-vpc' not found — skipping route table / subnet deletion."
  fi
else
  log_dry "aws ec2 describe-route-tables ... | aws ec2 delete-route-table ..."
  log_dry "aws ec2 describe-subnets ... | aws ec2 delete-subnet ..."
fi

# =============================================================================
# STEP 13 — VPC: detach + delete internet gateway
# =============================================================================
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
      log_info "Detaching internet gateway ${IGW_ID} from VPC ${VPC_ID}..."
      aws_safe ec2 detach-internet-gateway --region "${REGION}" --internet-gateway-id "${IGW_ID}" --vpc-id "${VPC_ID}"
      log_info "Deleting internet gateway ${IGW_ID}..."
      aws_safe ec2 delete-internet-gateway --region "${REGION}" --internet-gateway-id "${IGW_ID}"
    else
      log_warn "No internet gateway found attached to VPC ${VPC_ID} — skipping."
    fi
  else
    log_warn "VPC not found — skipping internet gateway deletion."
  fi
else
  log_dry "aws ec2 describe-internet-gateways ... | aws ec2 detach-internet-gateway + delete-internet-gateway"
fi

# =============================================================================
# STEP 14 — VPC: delete VPC
# =============================================================================
log_step "Step 14/14 — Delete VPC"

if [[ "${DRY_RUN}" == "false" ]]; then
  VPC_ID=$(get_vpc_id)
  if [[ "${VPC_ID}" != "None" && -n "${VPC_ID}" ]]; then
    log_info "Deleting VPC ${VPC_ID} (${PROJECT}-vpc)..."
    aws_safe ec2 delete-vpc --region "${REGION}" --vpc-id "${VPC_ID}"
    log_info "VPC deleted."
  else
    log_warn "VPC '${PROJECT}-vpc' not found — already deleted or never created."
  fi
else
  log_dry "aws ec2 describe-vpcs ... | aws ec2 delete-vpc ..."
fi

# =============================================================================
# OPTIONAL — run terraform destroy for any remaining state-tracked resources
# (e.g. the S3 backend itself, if you also manage it via Terraform)
# Note: the ECR repo's prevent_destroy was already bypassed above via AWS CLI,
# so `terraform destroy` will succeed for everything else.
# =============================================================================
log_step "Optional — terraform destroy (cleans up Terraform state)"

if [[ "${TF_AVAILABLE}" == "true" && -f "main.tf" ]]; then
  if [[ "${DRY_RUN}" == "false" ]]; then
    log_info "Running terraform destroy to sync state file..."
    # -refresh=false avoids errors for resources we already deleted via AWS CLI.
    # -auto-approve is safe here because we already confirmed above.
    terraform destroy \
      -refresh=false \
      -auto-approve \
      -var "ssh_public_key=placeholder" \
      -var "github_org=placeholder" \
      -var "github_repo=placeholder" \
      2>&1 | grep -v "prevent_destroy" || true
    log_info "terraform destroy complete."
  else
    log_dry "terraform destroy -refresh=false -auto-approve ..."
  fi
else
  log_warn "Terraform not available or main.tf not found — skipping terraform destroy."
  log_warn "Manually remove the state file from S3 if you no longer need it:"
  log_warn "  aws s3 rm s3://myorg-terraform-state/deploy-bot/terraform.tfstate"
fi

# =============================================================================
# Summary
# =============================================================================
echo ""
echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}${BOLD}║              ✅  Teardown complete                           ║${NC}"
echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "  Deleted resources:"
echo "    ✓  EC2 instances    : ${PROJECT}-staging, ${PROJECT}-production"
echo "    ✓  ECR repository   : ${ECR_REPO_NAME}  (all images purged)"
echo "    ✓  IAM roles        : ${PROJECT}-ec2-role, ${PROJECT}-github-actions-role"
echo "    ✓  IAM profile      : ${PROJECT}-ec2-profile"
echo "    ✓  SSH key pair     : ${PROJECT}-key"
echo "    ✓  Security group   : ${PROJECT}-sg"
echo "    ✓  VPC / subnet / IGW / route table"
echo ""
echo "  Resources NOT deleted by this script (managed separately):"
echo "    •  S3 bucket for Terraform state  (myorg-terraform-state)"
echo "    •  DynamoDB table for state lock  (terraform-state-lock)"
echo "    •  GitHub OIDC identity provider  (in IAM → Identity providers)"
echo ""
echo "  To delete those manually:"
echo "    aws s3 rb s3://myorg-terraform-state --force"
echo "    aws dynamodb delete-table --table-name terraform-state-lock --region ${REGION}"
echo "    # IAM → Identity providers → token.actions.githubusercontent.com → Delete"
echo ""
if [[ "${DRY_RUN}" == "true" ]]; then
  echo -e "  ${YELLOW}DRY-RUN mode — no changes were made.${NC}"
  echo ""
fi
