# 🤖 Telegram Deployment Automation Bot

A production-ready, secure Telegram bot for triggering deployments to staging and production environments — with role-based access control, audit logging, real-time log streaming, concurrent health checks, deploy locking, subprocess timeouts, and auto-rollback.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        TELEGRAM DEPLOYMENT BOT                          │
│                                                                         │
│  Developer (Telegram)                                                   │
│       │                                                                 │
│       │  /deploy production                                             │
│       ▼                                                                 │
│  ┌─────────────┐    RBAC     ┌──────────────────┐   Audit Log           │
│  │  Bot Handler│ ──────────► │  Role Check      │ ──────────► File/S3   │
│  │  (PTB)      │             │  (admin_ids list)│                       │
│  └─────────────┘             └────────┬─────────┘                       │
│                                       │ ✅ Authorized                   │
│                              ┌────────▼─────────┐                       │
│                              │ Deploy Lock      │ ← prevents double-    │
│                              │ (_deploying set) │   deploy race cond.   │
│                              └────────┬─────────┘                       │
│                                       │ ✅ Lock acquired                │
│                              ┌────────▼─────────┐                       │
│                              │ Inline Confirm   │                       │
│                              │ (commit hash)    │                       │
│                              └────────┬─────────┘                       │
│                                       │ ✅ Confirmed                    │
│                              ┌────────▼─────────┐                       │
│                              │ DeploymentManager│                       │
│                              │ subprocess exec  │                       │
│                              │ + timeout guard  │                       │
│                              └────────┬─────────┘                       │
│                    ┌──────────────────┼──────────────────┐              │
│                    ▼                  ▼                  ▼              │
│             ┌───────────┐  ┌──────────────────┐  ┌────────────┐         │
│             │ Git Pull  │  │  Docker Build    │  │ Push to ECR│         │
│             └───────────┘  └──────────────────┘  └─────┬──────┘         │
│                                                         │               │
│                    ┌────────────────────────────────────┘               │
│                    ▼                                                    │
│             ┌──────────────────┐                                        │
│             │  Health Check   │ ← state files written only              │
│             │  (retry loop)   │   AFTER this passes                     │
│             └────────┬────────┘                                         │
│              ✅ Pass │  ❌ Fail                                        │
│        ┌─────────────┴──────────────┐                                   │
│        ▼                            ▼                                   │
│  ┌───────────────┐         ┌─────────────────┐                          │
│  │ Notify user ✅│         │  Auto-Rollback  │                          │
│  │ Release lock  │         │  Notify user ❌ │                          │
│  └───────────────┘         │  Release lock   │                          │
│                             └─────────────────┘                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Project Structure

```
telegram-deploy-bot/
│
├── bot/                        # Python bot source
│   ├── bot.py                  # Entry point, command handlers, deploy lock
│   ├── config.py               # Lazy classmethod config (all values read at call time)
│   ├── rbac.py                 # Role-based access control decorator
│   ├── audit_logger.py         # Structured audit log (JSON Lines)
│   ├── deployment.py           # Deployment orchestration + subprocess timeout
│   └── requirements.txt        # Runtime Python dependencies
│
├── scripts/                    # Shell scripts (the actual deploy work)
│   ├── deploy.sh               # Full deployment pipeline
│   └── rollback.sh             # Rollback to previous image
│
├── terraform/                  # AWS infrastructure as code
│   ├── main.tf                 # EC2 + ECR + IAM + VPC + OIDC
│   └── destroy.sh              # Safe teardown of all AWS resources
│
├── docs/                       # Documentation
│   ├── INSTALLATION.md         # Step-by-step installation guide
│   └── BENEFITS.md             # Why use this bot
│
├── nginx/                      # Reverse proxy (webhook mode)
│   └── nginx.conf
│
├── monitoring/                 # Prometheus config
│   └── prometheus.yml
│
├── .github/
│   └── workflows/
│       └── ci-cd.yml           # GitHub Actions CI/CD pipeline
│
├── Dockerfile                  # Multi-stage Docker build for the bot
├── docker-compose.yml          # Run the bot + supporting services
├── requirements-dev.txt        # Pinned dev + test dependencies
├── .env.example                # Environment variable template
├── .secrets.baseline           # detect-secrets baseline (committed)
├── pytest.ini                  # Pytest configuration
└── README.md
```

---

## Getting Started

> 📖 **Full step-by-step installation instructions are in [`docs/INSTALLATION.md`](docs/INSTALLATION.md)**

**Prerequisites:**

- An AWS account — [console.aws.amazon.com](https://console.aws.amazon.com) (free tier works)
- A GitHub account — [github.com](https://github.com)
- Terraform ≥ 1.5 — [install guide](https://developer.hashicorp.com/terraform/install)
- The Telegram app and a bot token from [@BotFather](https://t.me/BotFather)

**GitHub Actions secrets required** — set these under `Settings → Secrets and variables → Actions`:

| Secret | Source |
|--------|--------|
| `TELEGRAM_BOT_TOKEN` | From @BotFather |
| `TELEGRAM_CHAT_ID` | Your Telegram user ID (from @userinfobot) |
| `ECR_REGISTRY` | `terraform output ecr_registry` |
| `AWS_DEPLOY_ROLE_ARN` | `terraform output deploy_role_arn` |
| `STAGING_SSH_KEY` | Contents of `~/.ssh/deploy_key` |
| `PRODUCTION_SSH_KEY` | Contents of `~/.ssh/deploy_key` (same file) |
| `STAGING_HOST` | `terraform output staging_ip` |
| `PRODUCTION_HOST` | `terraform output production_ip` |
| `STAGING_HEALTH_URL` | `http://<staging-ip>/health` |
| `PRODUCTION_HEALTH_URL` | `http://<production-ip>/health` |

---

## Bot Commands

| Command | Role Required | Description |
|---------|---------------|-------------|
| `/start` or `/help` | Any authorized | Show available commands |
| `/deploy staging` | Staging | Deploy `develop` branch to staging |
| `/deploy production` | Admin | Deploy `main` branch to production (requires confirmation) |
| `/rollback staging` | Admin | Rollback staging to the previous image |
| `/rollback production` | Admin | Rollback production to the previous image |
| `/status` | Staging | Show health and deployed commit for all environments |

---

## Environment Variables

All configuration is read from environment variables at call time — never frozen at import time. Copy `.env.example` to `.env` to get started.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | ✅ | — | Bot token from @BotFather |
| `ADMIN_TELEGRAM_IDS` | ✅ | — | Comma-separated admin user IDs |
| `STAGING_TELEGRAM_IDS` | — | — | Comma-separated staging user IDs |
| `REGISTRY_URL` | ✅ | — | ECR registry URL |
| `REGISTRY_IMAGE` | — | `myapp` | Docker image name |
| `AWS_REGION` | — | `us-east-1` | AWS region for ECR auth |
| `STAGING_HOST` | — | — | Staging server IP/hostname |
| `PRODUCTION_HOST` | — | — | Production server IP/hostname |
| `DEPLOY_USER` | — | `deploy` | SSH user on target servers |
| `SSH_KEY_PATH` | — | `/app/secrets/deploy_key` | Path to SSH deploy key |
| `STAGING_HEALTH_URL` | — | — | Health check endpoint for staging |
| `PRODUCTION_HEALTH_URL` | — | — | Health check endpoint for production |
| `HEALTH_CHECK_TIMEOUT` | — | `30` | Seconds per health check request |
| `HEALTH_CHECK_RETRIES` | — | `5` | Number of health check retries |
| `DEPLOY_TIMEOUT_SECONDS` | — | `600` | Max seconds before deploy is killed |
| `USE_KUBERNETES` | — | `false` | Use kubectl instead of Docker Compose |
| `KUBE_NAMESPACE` | — | `default` | Kubernetes namespace |
| `AUDIT_LOG_PATH` | — | `/var/log/deploybot/audit.log` | Audit log file path |
| `GITHUB_BRANCH_STAGING` | — | `develop` | Branch deployed to staging |
| `GITHUB_BRANCH_PRODUCTION` | — | `main` | Branch deployed to production |

---

## Security Architecture

### Role-Based Access Control (RBAC)

```
ADMIN   → full access: production deploy, rollback, staging, /status
          set via: ADMIN_TELEGRAM_IDS=123456789,987654321

STAGING → limited access: staging deploy + /status only
          set via: STAGING_TELEGRAM_IDS=111222333
```

Roles are enforced by the `@require_role` decorator on every handler. Admin role is re-verified on every callback button press — buttons cannot be replayed by unauthorized users.

### Deploy Lock

A module-level `_deploying: set[str]` prevents two concurrent deploys to the same environment. If an admin double-taps "Confirm" or a callback is replayed while a deploy is running, the second request is rejected immediately. The lock is released in a `try/finally` block so it is always freed, even if an unexpected exception occurs.

### Command Injection Prevention

```python
# ❌ DANGEROUS — shell injection possible
subprocess.run(f"deploy.sh {user_input}", shell=True)

# ✅ SAFE — fixed argument list, no shell interpolation
asyncio.create_subprocess_exec("/app/scripts/deploy.sh", environment, commit)
```

Environment and commit hash are additionally validated against strict allow-lists before reaching the subprocess call.

### Subprocess Timeout

Every deploy and rollback subprocess is wrapped in `asyncio.timeout(DEPLOY_TIMEOUT_SECONDS)`. If `deploy.sh` hangs — SSH timeout, docker build stall, network issue — the process is killed and an error is streamed back to the user. The bot never hangs indefinitely.

### Audit Log Integrity

The audit log writes core fields (`timestamp`, `user_id`, `action`) **after** spreading arbitrary metadata, so no metadata key can silently overwrite the forensic trail. Every action — deploy started, deploy success, deploy failed, rollback, denial — is recorded with user identity, environment, commit, and UTC timestamp.

### SSH Key Cleanup

CI/CD deploy steps use `trap 'rm -f /tmp/deploy_key' EXIT` to guarantee the private key is deleted from the runner filesystem even if the SSH command fails.

---

## Deployment Flow

```
User → /deploy production
         │
         ▼
1. RBAC check → not admin? 🚫 Denied + audited
         │ admin ✅
         ▼
2. Check deploy lock → env already deploying? ⏳ Rejected
         │ lock free ✅
         ▼
3. Fetch latest commit from Config.github_branch_production()
         │
         ▼
4. Confirmation dialog (commit hash shown)
         │ Confirm clicked
         ▼
5. Re-verify admin role on callback
         │
         ▼
6. Acquire deploy lock for environment
         │
         ▼
7. Audit log: { user, action=deploy_started, env, commit, timestamp }
         │
         ▼
8. Run deploy.sh production <commit> (timeout: DEPLOY_TIMEOUT_SECONDS)
   ├── Validate inputs (whitelist env, validate commit SHA format)
   ├── git fetch + checkout + pull origin main
   ├── docker build --no-cache (image tagged with exact commit)
   ├── aws ecr get-login-password | docker login
   ├── docker push → ECR
   ├── Save previous image ref for rollback
   ├── ssh deploy@host → docker compose up -d
   └── Health check (10 retries × 10s)
             │
             ├── ✅ PASS → write state files (commit + timestamp)
             │            audit log deploy_success
             │            notify user ✅
             │            release deploy lock
             │
             └── ❌ FAIL → audit log deploy_failed
                           notify user ❌
                           run rollback.sh (with timeout + streaming)
                           audit log auto_rollback_completed/failed
                           notify user with rollback result
                           release deploy lock
```

> **Why state files are written after health check:** If `deploy.sh` exits with code 1 (health check failed) and the bot triggers rollback, `rollback.sh` reads the previous image ref to revert to. Writing state files before health check would record a broken deployment as the last known-good state — the rollback would restore the broken image. State files are written only after a successful health check confirms the deployment is live and healthy.

---

## Running Tests

```bash
# Install runtime + dev dependencies
pip install -r bot/requirements.txt
pip install -r requirements-dev.txt

# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ -v --cov=bot --cov-report=term-missing
```

**95 tests across 5 test files**, covering:

- Config lazy evaluation and env-change reflection
- RBAC allow/deny logic and HTML parse mode on denial messages
- Deploy lock acquisition, rejection, and guaranteed release
- Deployment streaming, error detection, and subprocess timeout
- Concurrent health checks via `asyncio.gather()`
- Audit log integrity (metadata cannot overwrite core fields)
- Auto-rollback triggering on deploy failure
- Callback security (re-verification, double-confirm rejection)

---

## CI/CD Pipeline

```
Push to develop → test → build → push to ECR → deploy to staging → health check
Push to main    → test → build → push to ECR → [approval gate] → deploy to production → health check → notify Telegram
Pull request    → test only
```

All GitHub Actions are pinned to specific versions (no `@master` tags). The security scan (`detect-secrets`) runs against a committed `.secrets.baseline` so it produces stable, reproducible results.

---

## Teardown

```bash
cd terraform/
bash destroy.sh            # interactive — prompts "type DESTROY to confirm"
bash destroy.sh --dry-run  # preview all commands without executing
bash destroy.sh --force    # skip confirmation (CI use)
bash destroy.sh --region eu-west-1  # override region
```

Tears down EC2 instances, ECR repository and all images, IAM roles, VPC, subnets, internet gateway, security group, and SSH key pair.

---

## Production Hardening Checklist

```
Infrastructure:
[ ] SSH: disable password auth and root login (key-only)
[ ] Security group: restrict port 22 to your IP, not 0.0.0.0/0
[ ] Rotate the SSH deploy key every 90 days
[ ] ECR: scan images on push, fail CI on CRITICAL CVEs (Trivy configured)
[ ] Add SSH server fingerprints to known_hosts instead of StrictHostKeyChecking=no

Bot Security:
[ ] Whitelist only known Telegram user IDs — never run as a public bot
[ ] Permissions re-verified on every callback (already implemented)
[ ] Deploy lock prevents concurrent deploys (already implemented)
[ ] Subprocess timeout prevents hangs (already implemented)
[ ] Never log secrets (TELEGRAM_BOT_TOKEN excluded from safe_env)

Deployment:
[ ] Require PR review before merging to main
[ ] GitHub Environment protection rules with required reviewers for production
[ ] Add post-deploy smoke tests on top of the health check
[ ] Ship audit logs to immutable storage (S3 with Object Lock, CloudWatch Logs)
[ ] Set DEPLOY_TIMEOUT_SECONDS to match your slowest expected build time
```

---

*Built with Python 3.12 · python-telegram-bot 21 · Runs on AWS EC2 · Deployed via Docker · Controlled via Telegram*