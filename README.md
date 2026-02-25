# 🤖 Telegram Deployment Automation Bot

A production-ready, secure Telegram bot for triggering deployments to staging and production
environments — with RBAC, audit logging, real-time log streaming, health checks, and auto-rollback.

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
│  ┌─────────────┐    RBAC     ┌──────────────────┐   Audit Log          │
│  │  Bot Handler│ ──────────► │  Role Check       │ ──────────► File/S3  │
│  │  (PTB)      │             │  (admin_ids list) │                      │
│  └─────────────┘             └────────┬─────────┘                      │
│                                       │ ✅ Authorized                   │
│                              ┌────────▼─────────┐                      │
│                              │ Inline Confirm   │                      │
│                              │ (commit hash)    │                      │
│                              └────────┬─────────┘                      │
│                                       │ ✅ Confirmed                    │
│                              ┌────────▼─────────┐                      │
│                              │ DeploymentManager│                      │
│                              │  subprocess exec  │                      │
│                              └────────┬─────────┘                      │
│                                       │                                 │
│          ┌────────────────────────────┼────────────────────┐           │
│          │                            │                    │           │
│          ▼                            ▼                    ▼           │
│   ┌─────────────┐           ┌──────────────────┐  ┌─────────────┐     │
│   │ Git Pull    │           │  Docker Build    │  │ Push to ECR │     │
│   │ (GitHub)    │           │  + Tag + Label   │  │ (AWS)       │     │
│   └─────────────┘           └──────────────────┘  └──────┬──────┘     │
│                                                           │            │
│                              ┌────────────────────────────┘           │
│                              │                                         │
│                    ┌─────────▼────────────────────────┐               │
│                    │           DEPLOY TARGET            │               │
│                    │                                   │               │
│                    │  ┌─────────────┐  ┌───────────┐  │               │
│                    │  │Docker Compose│  │Kubernetes │  │               │
│                    │  │(SSH deploy) │  │(kubectl)  │  │               │
│                    │  └─────────────┘  └───────────┘  │               │
│                    └────────────────────────────────────┘               │
│                                       │                                 │
│                              ┌────────▼─────────┐                      │
│                              │  Health Check     │                      │
│                              │  (retry loop)     │                      │
│                              └────────┬─────────┘                      │
│                              ✅ Pass  │  ❌ Fail                        │
│                     ┌─────────────────┴─────────────────┐              │
│                     ▼                                    ▼              │
│             ┌──────────────┐                   ┌─────────────────┐     │
│             │ Write state  │                   │  Auto-Rollback  │     │
│             │ Notify user ✅│                  │  Notify user ❌ │     │
│             └──────────────┘                   └─────────────────┘     │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Project Structure

```
telegram-deploy-bot/
│
├── bot/                        # Python bot source
│   ├── bot.py                  # Main entry point, command handlers
│   ├── config.py               # All config from environment variables
│   ├── rbac.py                 # Role-based access control decorator
│   ├── audit_logger.py         # Structured audit log (JSON Lines)
│   ├── deployment.py           # Deployment orchestration
│   └── requirements.txt        # Python dependencies
│
├── scripts/                    # Shell scripts (the actual deploy work)
│   ├── deploy.sh               # Full deployment pipeline
│   └── rollback.sh             # Rollback to previous image
│
├── terraform/                  # AWS infrastructure as code
│   └── main.tf                 # EC2 + ECR + IAM + VPC + OIDC
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
├── .env.example                # Environment variable template
└── README.md
```

---

## Quick Start

### 1. Clone and Configure

```bash
git clone https://github.com/yourorg/telegram-deploy-bot.git
cd telegram-deploy-bot

# Copy and fill in your environment variables
cp .env.example .env
nano .env
```

### 2. Get Your Telegram User ID

Message [@userinfobot](https://t.me/userinfobot) on Telegram.
Add your numeric ID to `ADMIN_TELEGRAM_IDS` in `.env`.

### 3. Create Your Bot

1. Message [@BotFather](https://t.me/BotFather)
2. Send `/newbot`, follow prompts
3. Copy the token → `TELEGRAM_BOT_TOKEN` in `.env`

### 4. Set Up SSH Deploy Key

```bash
# Generate a dedicated deploy key (no passphrase — used by scripts)
ssh-keygen -t ed25519 -C "deploy-bot" -f ./secrets/deploy_key -N ""

# Copy public key to your target servers
ssh-copy-id -i ./secrets/deploy_key.pub deploy@your-staging-server
ssh-copy-id -i ./secrets/deploy_key.pub deploy@your-production-server
```

### 5. Provision AWS Infrastructure

```bash
cd terraform

# Create a terraform.tfvars file:
cat > terraform.tfvars <<EOF
aws_region        = "us-east-1"
ssh_public_key    = "$(cat ../secrets/deploy_key.pub)"
github_org        = "yourorg"
github_repo       = "yourrepo"
EOF

terraform init
terraform plan
terraform apply
```

### 6. Launch the Bot

```bash
# Development (with hot reload)
docker compose up --build

# Production (detached)
docker compose up -d
```

---

## Bot Commands

| Command | Role Required | Description |
|---------|---------------|-------------|
| `/start` or `/help` | Any authorized | Show available commands |
| `/deploy staging` | Staging | Deploy develop branch to staging |
| `/deploy production` | Admin | Deploy main branch to production (confirmation required) |
| `/rollback staging` | Admin | Rollback staging to previous image |
| `/rollback production` | Admin | Rollback production to previous image |
| `/status` | Staging | Show health + commit for all environments |

---

## Security Architecture

### Role-Based Access Control (RBAC)

```
ADMIN role:
  • Can run all commands
  • Can deploy to production
  • Can rollback any environment
  • Defined by: ADMIN_TELEGRAM_IDS env var

STAGING role:
  • Can deploy to staging
  • Can check /status
  • Cannot touch production
  • Defined by: STAGING_TELEGRAM_IDS env var

(Admins are automatically included in staging permissions)
```

The `@require_role(Role.ADMIN)` decorator is applied at the function level,
so even if someone finds a way to send a message to the bot, the ID check
runs in Python before any deployment code is touched.

### Command Injection Prevention

The single most important security principle in this codebase:

**NEVER use `shell=True` with any user-provided input.**

```python
# ❌ DANGEROUS — shell injection possible
subprocess.run(f"deploy.sh {user_input}", shell=True)

# ✅ SAFE — argument list, no shell interpolation
subprocess.run(["/app/scripts/deploy.sh", environment, commit])
```

Additional validation: environment names are whitelisted against a regex
before being passed to any script. Commit hashes are validated as hex-only.

### Secret Management

```
Development:  .env file (never committed)
Production:   AWS Secrets Manager → injected as environment variables
CI/CD:        GitHub Actions Secrets + OIDC (no long-lived AWS keys)
SSH Keys:     Dedicated deploy keypair (minimal permissions, no sudo)
```

### Principle of Least Privilege

- Bot runs as non-root user inside Docker
- EC2 IAM role has only ECR pull permissions
- GitHub Actions uses OIDC (no stored AWS credentials)
- Deploy SSH key can only run specific commands on target servers
- Docker socket mount is the one exception — see note in docker-compose.yml

---

## Deployment Flow (Step by Step)

```
User → /deploy production
         │
         ▼
1. RBAC check: Is user in ADMIN_TELEGRAM_IDS? → No → 🚫 Denied
         │ Yes
         ▼
2. Fetch latest commit hash from git
         │
         ▼
3. Show confirmation dialog:
   "Deploy abc1234 to production? [Confirm] [Cancel]"
         │ User clicks Confirm
         ▼
4. Re-verify admin role (callback can be replayed!)
         │
         ▼
5. Audit log: {user, action=deploy_started, env, commit, timestamp}
         │
         ▼
6. Run deploy.sh staging abc1234
   ├── git pull origin main
   ├── docker build -t registry/image:production-abc1234
   ├── docker push registry/image:production-abc1234
   ├── ssh deploy@production-host docker compose up -d
   └── health check (10 retries × 10s = 100s max wait)
         │ Stream each line → Telegram message
         ▼
7a. SUCCESS:
   ├── Write state files (commit, timestamp)
   ├── Audit log: deploy_success
   └── "✅ Deployment succeeded!"

7b. FAILURE:
   ├── Audit log: deploy_failed
   ├── Auto-rollback: rollback.sh production
   └── "❌ Deployment FAILED. Auto-rollback initiated."
```

---

## Blue/Green Deployment Strategy

Blue/Green is the gold standard for zero-downtime production deployments.

```
BEFORE DEPLOY:
  Load Balancer ──► [Blue] (v1, serving 100% traffic)
                    [Green] (idle)

DURING DEPLOY:
  1. Deploy v2 to Green environment (no traffic yet)
  2. Run health checks on Green
  3. Update Load Balancer: route 100% traffic to Green
  4. Green becomes the new "live" environment
  5. Blue is kept warm for instant rollback

AFTER DEPLOY:
  Load Balancer ──► [Green] (v2, serving 100% traffic)
                    [Blue] (v1, on standby)

ROLLBACK:
  Load Balancer ──► [Blue] (v1, restored instantly)
```

**AWS Implementation:** Use an Application Load Balancer with two target groups.
Swap the listener rule with a single API call — zero downtime, instant rollback.

```bash
# Swap traffic from Blue to Green (AWS CLI)
aws elbv2 modify-listener \
  --listener-arn $LISTENER_ARN \
  --default-actions Type=forward,TargetGroupArn=$GREEN_TG_ARN
```

---

## Canary Deployment Strategy

Canary is for when you want to test in production with a subset of real traffic
before committing fully. Named after the "canary in a coal mine" concept.

```
Step 1: 5% of traffic → v2 (canary), 95% → v1
         └── Monitor: error rates, latency, custom metrics
         └── Wait 10–30 minutes

Step 2: 25% → v2, 75% → v1
         └── Monitor again

Step 3: 50% → v2, 50% → v1
         └── Monitor again

Step 4: 100% → v2 (full rollout)
         OR
         ROLLBACK: 100% → v1 if metrics degrade

Kubernetes implementation uses traffic splitting via:
  • Argo Rollouts (recommended)
  • Flagger
  • AWS App Mesh weighted routing
  • Istio virtual services
```

---

## Monitoring Integration

### Prometheus Metrics to Track

```python
from prometheus_client import Counter, Histogram, Gauge

# In bot.py, instrument these:
DEPLOY_TOTAL = Counter("deployments_total", "Total deployments", ["env", "status"])
DEPLOY_DURATION = Histogram("deployment_duration_seconds", "Deploy time", ["env"])
HEALTH_CHECK_STATUS = Gauge("health_check_up", "Health check status", ["env"])

# Usage:
with DEPLOY_DURATION.labels(env=environment).time():
    await run_deployment(...)
DEPLOY_TOTAL.labels(env=environment, status="success").inc()
```

### Grafana Dashboard Panels

1. **Deployment frequency** — bar chart, count per day/env
2. **Deployment duration** — histogram by environment
3. **Success/failure rate** — pie chart or ratio gauge
4. **Time since last deployment** — single stat
5. **Health check status** — red/green indicator per env

### Alerting Rules

```yaml
# prometheus/alerts.yml
groups:
  - name: deployments
    rules:
      - alert: DeploymentFailed
        expr: increase(deployments_total{status="failed"}[5m]) > 0
        annotations:
          summary: "Deployment failed — check Telegram bot logs"

      - alert: HealthCheckDown
        expr: health_check_up == 0
        for: 2m
        annotations:
          summary: "{{ $labels.env }} health check failing for 2 minutes"
```

---

## Scaling This System

### Horizontal Scaling Considerations

1. **Bot state**: The bot uses file-based state (`/var/lib/deploybot`).
   For multiple bot instances, move state to Redis or DynamoDB.

2. **Lock deployments**: Prevent concurrent deployments to the same environment.
   Use Redis `SET NX EX` (set if not exists, with expiry) as a distributed lock.

3. **Queue deployments**: Use SQS or Celery to queue deploy requests,
   so they run sequentially rather than stepping on each other.

4. **Webhook vs Polling**: Switch from polling to webhooks when you need
   sub-second response times or are running multiple bot instances.
   (Polling only works with a single instance.)

### Multi-Team / Multi-Bot

```
Option A: One bot, multiple environments
  └── Single bot handles all teams' environments
  └── RBAC differentiates who can do what
  └── Simplest to operate

Option B: One bot per team
  └── Each team manages their own bot
  └── Full isolation between teams
  └── More operational overhead
```

---

## Common Failure Scenarios + Mitigations

| Scenario | Detection | Mitigation |
|----------|-----------|------------|
| Deploy script hangs | asyncio timeout on subprocess | Kill process, report timeout, rollback |
| Registry push fails | Non-zero exit code from docker push | Retry with exponential backoff |
| SSH connection timeout | subprocess timeout | Alert user, retry once |
| Health check never passes | Max retries exceeded | Auto-rollback to previous image |
| Bot loses network to Telegram | Polling reconnect built into PTB | Systemd restart policy |
| Container OOM killed | Docker health check fails | Review memory limits in compose |
| Git merge conflict on pull | git pull returns error | Alert user, require manual fix |
| Telegram bot token compromised | Unauthorized commands | Revoke token via BotFather, rotate |
| Replay attack on inline buttons | Malicious user resends old callback | Re-verify role on every callback handler |

---

## GitHub Actions Secrets to Configure

Go to: `Settings > Secrets and variables > Actions`

| Secret Name | Value |
|-------------|-------|
| `ECR_REGISTRY` | `123456789.dkr.ecr.us-east-1.amazonaws.com` |
| `AWS_DEPLOY_ROLE_ARN` | ARN from `terraform output github_actions_role` |
| `STAGING_SSH_KEY` | Contents of `./secrets/deploy_key` |
| `PRODUCTION_SSH_KEY` | Contents of `./secrets/deploy_key` |
| `STAGING_HOST` | Your staging server IP |
| `PRODUCTION_HOST` | Your production server IP |
| `STAGING_HEALTH_URL` | `https://staging.example.com/health` |
| `PRODUCTION_HEALTH_URL` | `https://production.example.com/health` |
| `TELEGRAM_BOT_TOKEN` | From BotFather |
| `TELEGRAM_CHAT_ID` | Your admin chat/channel ID |

---

## Production Hardening Checklist

```
Infrastructure:
[ ] SSH: disable password auth, root login; key-only auth
[ ] Firewall: block all ports except 22, 443
[ ] Rotate SSH deploy key every 90 days
[ ] EC2 patching: enable AWS Systems Manager Patch Manager
[ ] ECR: scan images on push, fail builds on CRITICAL CVEs
[ ] VPC: bot server in private subnet, NAT gateway for outbound

Bot Security:
[ ] Whitelist Telegram user IDs — never use a public bot
[ ] Re-verify permissions on every callback (don't trust cached state)
[ ] Validate all inputs with strict allow-lists
[ ] Never log secrets — audit log user IDs, not tokens
[ ] Set bot token scope with BotFather (restrict to needed updates only)

Deployment:
[ ] Require PR approval before merging to main
[ ] GitHub Environment protection rules (required reviewers for production)
[ ] Image signing with Cosign (supply chain security)
[ ] Deployment windows: restrict production deploys to business hours
[ ] Post-deploy smoke tests in addition to health checks

Observability:
[ ] Centralized logging (CloudWatch Logs, Datadog, or ELK)
[ ] Alerts on deployment failures
[ ] On-call runbook linked from every alert
[ ] Monthly review of audit logs
```

---

## Interview Talking Points

**"Walk me through how you'd secure this bot."**
→ RBAC via allowlisted user IDs, no shell=True, input validation, separate deploy keypair,
  OIDC instead of long-lived AWS credentials, non-root container user, re-verify on callbacks.

**"What happens when a deployment fails?"**
→ Health check polls with retries. On max retries exceeded, the deploy script exits non-zero.
  The bot detects this, notifies the user, and calls rollback.sh which re-deploys the
  previous image tag stored in the state file.

**"How would you scale this to 50 teams?"**
→ Move state to Redis, add deployment queue (SQS/Celery), switch to webhooks,
  add distributed lock per environment to prevent concurrent deploys,
  consider one bot per team with a management API layer.

**"How do you prevent command injection?"**
→ Never use shell=True with user input. Pass arguments as a list to subprocess.
  Validate environment names against a regex allowlist. Validate commit hashes
  as hex-only before passing to any script.

**"What's the difference between blue/green and canary?"**
→ Blue/green: instant full traffic switch between two identical environments,
  near-zero downtime, easy rollback. Canary: gradual traffic shift to new version
  while monitoring metrics, catches regressions with limited blast radius.

---
