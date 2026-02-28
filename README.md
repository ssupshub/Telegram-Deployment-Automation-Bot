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
│  ┌─────────────┐    RBAC     ┌──────────────────┐   Audit Log           │
│  │  Bot Handler│ ──────────► │  Role Check      │ ──────────► File/S3   │
│  │  (PTB)      │             │  (admin_ids list)│                       │
│  └─────────────┘             └────────┬─────────┘                       │
│                                       │ ✅ Authorized                   │
│                              ┌────────▼─────────┐                       │
│                              │ Inline Confirm   │                       │
│                              │ (commit hash)    │                       │
│                              └────────┬─────────┘                       │
│                                       │ ✅ Confirmed                    │
│                              ┌────────▼─────────┐                       │
│                              │ DeploymentManager│                       │
│                              │  subprocess exec │                       │
│                              └────────┬─────────┘                       │
│                                       │                                 │
│          ┌────────────────────────────┼────────────────────┐            │
│          │                            │                    │            │
│          ▼                            ▼                    ▼            │
│   ┌─────────────┐           ┌──────────────────┐  ┌─────────────┐       │
│   │ Git Pull    │           │  Docker Build    │  │ Push to ECR │       │
│   │ (GitHub)    │           │  + Tag + Label   │  │ (AWS)       │       │
│   └─────────────┘           └──────────────────┘  └──────┬──────┘       │
│                                                          │              │
│                              ┌───────────────────────────┘              │
│                              │                                          │
│                    ┌─────────▼────────────────────────┐                 │
│                    │           DEPLOY TARGET          │                 │
│                    │                                  │                 │
│                    │  ┌─────────────┐  ┌───────────┐  │                 │
│                    │  │Docker Compose│  │Kubernetes│  │                 │
│                    │  │(SSH deploy) │  │(kubectl)  │  │                 │
│                    │  └─────────────┘  └───────────┘  │                 │
│                    └──────────────────────────────────┘                 │
│                                       │                                 │
│                              ┌────────▼─────────┐                       │
│                              │  Health Check    │                       │
│                              │  (retry loop)    │                       │
│                              └────────┬─────────┘                       │
│                              ✅ Pass  │  ❌ Fail                       │
│                     ┌─────────────────┴─────────────────┐               │
│                     ▼                                   ▼               │
│             ┌──────────────┐                   ┌─────────────────┐      │
│             │ Write state  │                   │  Auto-Rollback  │      │
│             │ Notify user ✅│                  │  Notify user ❌ │      │
│             └──────────────┘                   └─────────────────┘      │
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

## Installation Guide

> **No installs on your laptop.** Everything runs inside **AWS CloudShell** — a free terminal built into the AWS website. All you need is a web browser.

---

### What You Need Before Starting

- An AWS account → [console.aws.amazon.com](https://console.aws.amazon.com) (free tier is fine)
- A GitHub account → [github.com](https://github.com) (free)
- The Telegram app on your phone

---

### What is AWS CloudShell?

CloudShell is a free terminal that lives inside the AWS Console. Click the **`>_` icon** in the top navigation bar of any AWS page and a terminal opens in your browser — already logged into your AWS account. No installs, no configuration.

**CloudShell comes with:** Python 3, Git, AWS CLI, pip, curl, ssh, ssh-keygen.
**You only need to install:** Terraform (one command, shown in Part 2).

> **Tip:** CloudShell saves your files between sessions. If it times out after 20 minutes of inactivity, just click `>_` again — your files are still there.

---

### Part 1 — Create Your Telegram Bot

Done on your phone. Takes about 2 minutes.

**Step 1: Find BotFather**

Open Telegram, search for `@BotFather` (blue checkmark — official one), tap Start.

**Step 2: Create your bot**

Send `/newbot` to BotFather. It asks two questions:
- **Name** — anything you like, e.g. `My Deploy Bot`
- **Username** — must end in `bot`, e.g. `mydeploybot_bot`

BotFather replies with your token:
```
7123456789:AAHdqTcvCH1vGWJxfSeofSPs38eBlP2I9Igs
```

> ⚠️ **Save this token.** You'll need it in Part 4. Never share it publicly.

**Step 3: Get your Telegram User ID**

Search for `@userinfobot` on Telegram, tap Start. It instantly replies with your User ID — a number like `123456789`. Write it down.

---

### Part 2 — Open CloudShell and Install Terraform

**Step 1: Open CloudShell**

Go to [console.aws.amazon.com](https://console.aws.amazon.com) → click the **`>_` icon** in the top navigation bar → wait ~10 seconds for the terminal to load.

**Step 2: Install Terraform**

Paste this entire block into CloudShell and press Enter:

```bash
cd ~
curl -fsSL https://releases.hashicorp.com/terraform/1.7.5/terraform_1.7.5_linux_amd64.zip -o terraform.zip
unzip -o terraform.zip
mkdir -p ~/.local/bin
mv terraform ~/.local/bin/terraform
export PATH=$PATH:~/.local/bin
echo 'export PATH=$PATH:~/.local/bin' >> ~/.bashrc

# Verify:
terraform --version
# Should print: Terraform v1.7.5
```

**Step 3: Clone your GitHub repo into CloudShell**

```bash
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git
cd YOUR_REPO
```

---

### Part 3 — One-Time AWS Setup (Identity Provider)

This lets GitHub Actions log into AWS without a password. You do this once, never again.

1. In AWS Console, search for **IAM** → click it
2. In the left sidebar click **Identity providers**
3. Click **Add provider**
4. Fill in the form:

| Field | Value |
|-------|-------|
| Provider type | `OpenID Connect` |
| Provider URL | `https://token.actions.githubusercontent.com` |
| Audience | `sts.amazonaws.com` |

5. Click **Get thumbprint** → click **Add provider**

---

### Part 4 — Run Terraform

Terraform automatically creates all your AWS resources: EC2 servers, ECR image repository, IAM roles, security groups.

```bash
# In CloudShell:
cd ~/YOUR_REPO/terraform

terraform init
terraform plan   # Preview what will be created — no changes yet
terraform apply  # Type 'yes' when prompted
```

Takes 3–5 minutes. When done, copy the output values — you need them in Part 5:

```
ecr_registry    = "123456789.dkr.ecr.us-east-1.amazonaws.com"
deploy_role_arn = "arn:aws:iam::123456789:role/myapp-github-actions-role"
staging_ip      = "54.123.45.67"
production_ip   = "54.123.45.89"
```

> **Tip:** Forgot to copy? Run `terraform output` anytime to see it again.

---

### Part 5 — Create SSH Keys in CloudShell

**Step 1: Generate the key pair**

```bash
ssh-keygen -t ed25519 -f ~/.ssh/deploy_key -N ""
# Creates:
#   ~/.ssh/deploy_key      ← PRIVATE key (goes into GitHub Secrets)
#   ~/.ssh/deploy_key.pub  ← PUBLIC key (goes onto your EC2 servers)
```

**Step 2: Install the public key on your staging server**

```bash
# SSH into staging from CloudShell:
ssh -i ~/.ssh/deploy_key ec2-user@YOUR_STAGING_IP

# Now inside EC2 — run these:
sudo useradd -m deploy
sudo mkdir -p /home/deploy/.ssh
sudo bash -c 'cat >> /home/deploy/.ssh/authorized_keys' << 'EOF'
PASTE_CONTENTS_OF_deploy_key.pub_HERE
EOF
sudo chown -R deploy:deploy /home/deploy/.ssh
sudo chmod 700 /home/deploy/.ssh
sudo chmod 600 /home/deploy/.ssh/authorized_keys
sudo mkdir -p /opt/myapp
sudo chown deploy:deploy /opt/myapp

exit  # back to CloudShell
```

Repeat for your production server using `YOUR_PRODUCTION_IP`.

**Step 3: Print your private key (to copy into GitHub)**

```bash
cat ~/.ssh/deploy_key
```

Select all output — including the `-----BEGIN` and `-----END` lines — and copy it. You'll paste it into GitHub Secrets next.

---

### Part 6 — Add Secrets to GitHub

Go to: your repo on github.com → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

Add all 10 secrets:

| Secret Name | Where to get the value | What it does |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | From BotFather (Part 1) | Lets GitHub send Telegram notifications |
| `TELEGRAM_CHAT_ID` | Your User ID from @userinfobot (Part 1) | Who to notify |
| `ECR_REGISTRY` | Terraform output: `ecr_registry` | Docker image storage URL |
| `AWS_DEPLOY_ROLE_ARN` | Terraform output: `deploy_role_arn` | Lets GitHub log into AWS |
| `STAGING_SSH_KEY` | Output of `cat ~/.ssh/deploy_key` in CloudShell | SSH access to staging |
| `PRODUCTION_SSH_KEY` | Output of `cat ~/.ssh/deploy_key` (same file) | SSH access to production |
| `STAGING_HOST` | Terraform output: `staging_ip` | Staging server IP |
| `PRODUCTION_HOST` | Terraform output: `production_ip` | Production server IP |
| `STAGING_HEALTH_URL` | `http://YOUR_STAGING_IP/health` | Health check URL for staging |
| `PRODUCTION_HEALTH_URL` | `http://YOUR_PRODUCTION_IP/health` | Health check URL for production |

---

### Part 7 — Set Up GitHub Environments

This adds a manual approval gate before anything deploys to production.

**Create the production environment (approval required):**

1. Your repo → **Settings** → **Environments** → **New environment**
2. Name it exactly: `production`
3. Click **Configure environment**
4. Under **Required reviewers** — add your GitHub username
5. Click **Save protection rules**

**Create the staging environment (no approval needed):**

1. **New environment** → name it `staging`
2. Leave everything blank → **Save**

---

### Part 8 — Configure the Bot on Your EC2 Servers

SSH into each server from CloudShell and create the `.env` file:

```bash
ssh -i ~/.ssh/deploy_key deploy@YOUR_STAGING_IP

cat > /opt/myapp/.env << 'EOF'
TELEGRAM_BOT_TOKEN=7123456789:AAHdqTcvCH1vGWJxfSeofSPs38eBlP2I9Igs
ADMIN_TELEGRAM_IDS=123456789
STAGING_TELEGRAM_IDS=123456789
REGISTRY_URL=123456789.dkr.ecr.us-east-1.amazonaws.com
REGISTRY_IMAGE=myapp
AWS_REGION=us-east-1
STAGING_HOST=54.123.45.67
PRODUCTION_HOST=54.123.45.89
EOF

chmod 600 /opt/myapp/.env
exit
```

Repeat for your production server. Replace all values with your own.

> **Multiple admins?** Separate User IDs with commas: `ADMIN_TELEGRAM_IDS=123456789,987654321`

---

### Part 9 — Push Your Code

**Deploy to staging** (push to the `develop` branch):

```bash
# In CloudShell:
cd ~/YOUR_REPO
git checkout -b develop
git add .
git commit -m "Initial setup"
git push origin develop
```

Go to **github.com → your repo → Actions tab** to watch it run. The pipeline will:
- Run tests
- Run the linter
- Build and push a Docker image to ECR
- SSH into staging and deploy the container
- Health check staging

Takes about 3–5 minutes.

**Deploy to production** (push to the `main` branch):

```bash
git checkout main
git merge develop
git push origin main
```

GitHub pauses and emails you for approval. Go to **Actions → the waiting run → Review deployments → check `production` → Approve and deploy**.

When it succeeds, your Telegram bot sends you:
```
✅ Production deployment succeeded! Commit: a1b2c3d4
```

---

### Troubleshooting

**`terraform: command not found` in CloudShell**
CloudShell timed out and reset the PATH. Re-run:
```bash
export PATH=$PATH:~/.local/bin
```

**Pipeline fails at "Configure AWS credentials"**
Check `AWS_DEPLOY_ROLE_ARN` is exactly what Terraform output. Also make sure you completed Part 3 (adding GitHub as identity provider) — without it the role will never trust GitHub.

**Pipeline fails at "Deploy via SSH"**
The SSH key is usually wrong. Make sure you copied the full private key including `-----BEGIN OPENSSH PRIVATE KEY-----` and `-----END OPENSSH PRIVATE KEY-----`. Test the key directly:
```bash
ssh -i ~/.ssh/deploy_key deploy@YOUR_SERVER_IP echo ok
# Should print: ok
```

**Health check fails after deploy**
```bash
ssh -i ~/.ssh/deploy_key deploy@YOUR_STAGING_IP
cd /opt/myapp
docker compose ps      # Are containers running?
docker compose logs    # Any errors?
```

**Bot doesn't respond in Telegram**
```bash
cat /opt/myapp/.env    # Verify values look correct
docker compose logs    # Look for token or auth errors
```

---

### Checklist — Before Your First Push

```
☐ Created Telegram bot and saved the token (Part 1)
☐ Got your Telegram User ID from @userinfobot (Part 1)
☐ Opened CloudShell and installed Terraform (Part 2)
☐ Cloned your GitHub repo into CloudShell (Part 2)
☐ Added GitHub as identity provider in AWS IAM (Part 3)
☐ Ran terraform apply and copied the 4 output values (Part 4)
☐ Generated SSH key in CloudShell (Part 5)
☐ Installed public key and created deploy user on BOTH servers (Part 5)
☐ Added all 10 secrets to GitHub (Part 6)
☐ Created 'production' environment in GitHub with yourself as reviewer (Part 7)
☐ Created 'staging' environment in GitHub with no reviewer (Part 7)
☐ Created .env file on BOTH servers (Part 8)
```

Every box ticked? Run `git push origin develop` in CloudShell and watch the Actions tab. 🚀

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

See [Part 6 of the Installation Guide](#part-6--add-secrets-to-github) for where to get each value.

| Secret Name | Value |
|-------------|-------|
| `TELEGRAM_BOT_TOKEN` | From BotFather |
| `TELEGRAM_CHAT_ID` | Your Telegram User ID (from @userinfobot) |
| `ECR_REGISTRY` | From `terraform output` → `ecr_registry` |
| `AWS_DEPLOY_ROLE_ARN` | From `terraform output` → `deploy_role_arn` |
| `STAGING_SSH_KEY` | Output of `cat ~/.ssh/deploy_key` in CloudShell |
| `PRODUCTION_SSH_KEY` | Output of `cat ~/.ssh/deploy_key` in CloudShell |
| `STAGING_HOST` | From `terraform output` → `staging_ip` |
| `PRODUCTION_HOST` | From `terraform output` → `production_ip` |
| `STAGING_HEALTH_URL` | `http://YOUR_STAGING_IP/health` |
| `PRODUCTION_HEALTH_URL` | `http://YOUR_PRODUCTION_IP/health` |

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
