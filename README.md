# 🤖 Telegram Deployment Automation Bot

A production-ready, secure Telegram bot for triggering deployments to staging and production environments — with role-based access control, audit logging, real-time log streaming, health checks, and auto-rollback.

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
│                    ┌─────────▼────────────────────────┐                 │
│                    │           DEPLOY TARGET          │                 │
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
│             │ Notify user ✅│                  │  Notify user ❌ │     │
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
│   ├── main.tf                 # EC2 + ECR + IAM + VPC + OIDC
│   └── destroy.sh              # Safe teardown of all AWS resources
│
├── docs/                       # Extended documentation
│   └── install-guide.docx      # Step-by-step installation guide
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

## Getting Started

> 📖 **Full step-by-step installation instructions are in [`docs/install-guide.docx`](docs/install-guide.docx)**
>
> The guide covers everything from creating your Telegram bot and provisioning AWS infrastructure with Terraform, to configuring GitHub Secrets, setting up environments with approval gates, and deploying your first build. No prior AWS or Telegram experience required.

**Prerequisites at a glance:**

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
| `PRODUCTION_SSH_KEY` | Contents of `~/.ssh/deploy_key` |
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
| `/status` | Staging | Show health status and deployed commit for all environments |

---

## Security Architecture

### Role-Based Access Control (RBAC)

Two roles are defined, controlled entirely by Telegram user IDs set in environment variables:

```
ADMIN  → full access: production deploy, rollback, staging
         set via: ADMIN_TELEGRAM_IDS=123456789,987654321

STAGING → limited access: staging deploy + /status only
          set via: STAGING_TELEGRAM_IDS=111222333
          (admins are automatically included)
```

The `@require_role` decorator runs the ID check in Python before any deployment code is reached. Role is also re-verified on every inline button callback, since callbacks can be replayed by a determined attacker.

### Command Injection Prevention

All subprocess calls use a fixed argument list — `shell=True` with user input is never used:

```python
# ❌ DANGEROUS — shell injection possible
subprocess.run(f"deploy.sh {user_input}", shell=True)

# ✅ SAFE — argument list, no shell interpolation
subprocess.run(["/app/scripts/deploy.sh", environment, commit])
```

Environment names are additionally validated against a strict allowlist (`staging` / `production`) and commit hashes are validated as 4–40 hex characters before either is passed to any script.

### Secret Management

```
Development:  .env file (never committed — see .gitignore)
Production:   AWS Secrets Manager → injected as environment variables
CI/CD:        GitHub Actions Secrets + OIDC (no long-lived AWS keys stored)
SSH Keys:     Dedicated deploy keypair, minimal permissions, no sudo
```

### Principle of Least Privilege

- Bot container runs as a non-root user (`botuser`)
- EC2 IAM role is scoped to ECR pull + CloudWatch Logs only
- GitHub Actions authenticates via OIDC — no stored AWS access keys
- Docker socket mount grants root-equivalent Docker access — see the note in `docker-compose.yml` if this is a concern for your threat model

---

## Deployment Flow

```
User → /deploy production
         │
         ▼
1. RBAC check → not admin? 🚫 Denied
         │ admin ✅
         ▼
2. Fetch latest commit hash from git
         │
         ▼
3. Confirmation dialog: "Deploy abc1234 to production? [Confirm] [Cancel]"
         │ Confirm clicked
         ▼
4. Re-verify admin role (callbacks can be replayed)
         │
         ▼
5. Audit log: { user, action=deploy_started, env, commit, timestamp }
         │
         ▼
6. Run deploy.sh production abc1234
   ├── git pull origin main
   ├── docker build + tag
   ├── docker push → ECR
   ├── ssh deploy@host → docker compose up -d
   └── health check (10 retries × 10s)
         │
         ├── ✅ SUCCESS → write state files, audit log, notify user
         └── ❌ FAILURE → audit log, auto-rollback, notify user
```

---

## Deployment Strategies

### Blue/Green — Zero-Downtime Cutover

Traffic flips atomically from the old version (Blue) to the new version (Green) via a single load balancer rule change. Green is health-checked before any traffic reaches it, and Blue stays warm for an instant rollback.

```bash
# Swap traffic from Blue to Green (AWS CLI)
aws elbv2 modify-listener \
  --listener-arn $LISTENER_ARN \
  --default-actions Type=forward,TargetGroupArn=$GREEN_TG_ARN
```

**Best for:** most production deployments — simple, fast, and fully reversible.

### Canary — Gradual Traffic Shift

A small percentage of real traffic is routed to the new version first. If error rates and latency stay within bounds, traffic is gradually increased to 100%. Any degradation triggers a full rollback.

```
5%  → v2   monitor 10–30 min
25% → v2   monitor
50% → v2   monitor
100% → v2  ✅  or  rollback → v1
```

Kubernetes implementations typically use Argo Rollouts, Flagger, or Istio virtual services for weighted routing.

**Best for:** high-traffic services where even a brief outage is costly, or when you want real-user signal before full rollout.

---

## Monitoring Integration

The bot exposes Prometheus metrics via `prometheus-client`. Key metrics to instrument in `bot.py`:

```python
from prometheus_client import Counter, Histogram, Gauge

DEPLOY_TOTAL    = Counter("deployments_total", "Total deployments", ["env", "status"])
DEPLOY_DURATION = Histogram("deployment_duration_seconds", "Deploy time", ["env"])
HEALTH_UP       = Gauge("health_check_up", "Health check status", ["env"])
```

Recommended Grafana panels: deployment frequency (bar chart), deployment duration (histogram), success/failure ratio, time-since-last-deploy (single stat), and per-environment health status (red/green indicator).

Example Prometheus alert rules:

```yaml
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
          summary: "{{ $labels.env }} health check failing for 2+ minutes"
```

The `monitoring/prometheus.yml` in this repo is pre-configured to scrape the bot container on port `9090`.

---

## Scaling Considerations

The default setup is a single bot instance with file-based state — suitable for small teams. For larger-scale use:

- **State** — move `/var/lib/deploybot` state files to Redis or DynamoDB to support multiple bot instances
- **Concurrency** — add a distributed lock (`Redis SET NX EX`) per environment to prevent concurrent deploys stepping on each other
- **Queue** — use SQS or Celery to serialize deploy requests rather than running them in parallel
- **Webhooks** — switch from polling to Telegram webhooks for sub-second response times; polling is limited to a single running instance
- **Multi-team** — either expand RBAC within a single bot (simpler to operate) or run one bot per team (full isolation, more overhead)

---

## Common Failure Scenarios

| Scenario | Detection | Mitigation |
|----------|-----------|------------|
| Deploy script hangs | `asyncio` timeout on subprocess | Kill process, report timeout, trigger rollback |
| Registry push fails | Non-zero exit from `docker push` | Retry with exponential backoff |
| SSH connection timeout | Subprocess timeout | Alert user, retry once |
| Health check never passes | Max retries exceeded | Auto-rollback to previous image |
| Bot loses Telegram connectivity | PTB polling reconnect logic | Systemd `Restart=always` policy |
| Container OOM killed | Docker health check fails | Review memory limits in `docker-compose.yml` |
| Git merge conflict on pull | `git pull` non-zero exit | Alert user, require manual resolution |
| Bot token compromised | Unauthorized commands arriving | Revoke via @BotFather immediately, rotate |
| Replay attack on inline buttons | Malicious user re-sends old callback | Role re-verified on every callback handler |

---

## Teardown

To delete all AWS resources created by `terraform/main.tf`, run the safe teardown script:

```bash
cd terraform/

bash destroy.sh            # interactive — prompts you to type DESTROY
bash destroy.sh --dry-run  # print every action without executing anything
bash destroy.sh --force    # skip the confirmation prompt (CI use)
```

The script handles dependency ordering (ECR images → instances → IAM → VPC) and bypasses the `prevent_destroy` lifecycle guard on the ECR repository. See [`terraform/destroy.sh`](terraform/destroy.sh) for full details and the list of resources it intentionally leaves untouched (S3 state bucket, DynamoDB lock table, GitHub OIDC provider).

---

## Production Hardening Checklist

```
Infrastructure:
[ ] SSH: disable password auth and root login — key-only
[ ] Firewall: block all ports except 22 and 443
[ ] Rotate the SSH deploy key every 90 days
[ ] Enable AWS Systems Manager Patch Manager for EC2
[ ] ECR: scan images on push, fail CI builds on CRITICAL CVEs
[ ] VPC: move bot to a private subnet with a NAT gateway for outbound

Bot Security:
[ ] Whitelist Telegram user IDs — never run as a public bot
[ ] Re-verify permissions on every callback (don't trust cached state)
[ ] Validate all inputs with strict allow-lists before any subprocess call
[ ] Never log secrets — audit log user IDs and actions, not tokens
[ ] Restrict bot token update types via BotFather's token scope settings

Deployment:
[ ] Require PR approval before merging to main
[ ] GitHub Environment protection rules: required reviewers for production
[ ] Sign images with Cosign for supply-chain security
[ ] Restrict production deploys to business hours (deployment windows)
[ ] Add post-deploy smoke tests on top of the health check endpoint

Observability:
[ ] Ship logs to a centralized store (CloudWatch Logs, Datadog, ELK)
[ ] Alert on deployment failures with on-call runbook links
[ ] Review audit logs monthly
```

---

## Interview Talking Points

**"Walk me through how you'd secure this bot."**
RBAC via allowlisted Telegram user IDs enforced at the decorator level, never `shell=True` with user input, input validation on all arguments, a dedicated deploy SSH keypair with no sudo, OIDC instead of long-lived AWS credentials, non-root container user, and role re-verification on every inline button callback.

**"What happens when a deployment fails?"**
The health check polls the service endpoint with configurable retries. If all retries are exhausted, `deploy.sh` exits non-zero. The bot detects the error sentinel line in the streamed output, logs the failure, notifies the user, and automatically calls `rollback.sh`, which re-deploys the previous image tag from the state file. Rollback output is streamed back to the user so they can see what happened.

**"How would you scale this to 50 teams?"**
Move file-based state to Redis or DynamoDB, add a per-environment distributed lock to prevent concurrent deploys, queue deploy requests via SQS or Celery, switch from polling to webhooks for responsiveness, and consider one bot per team for full isolation over a single shared bot with RBAC.

**"How do you prevent command injection?"**
Arguments are always passed as a list to `asyncio.create_subprocess_exec` — never interpolated into a shell string. Environment names are validated against a `frozenset` allowlist before reaching any subprocess call. Commit hashes are matched against a `^[0-9a-f]{4,40}$` regex. Both checks run independently in `deployment.py` and in `deploy.sh`.

**"What's the difference between blue/green and canary?"**
Blue/green is an instant, atomic traffic switch between two full environments — near-zero downtime and trivially reversible, but you only catch problems after the full cutover. Canary is a gradual traffic shift that exposes the new version to a small percentage of real users first, giving you real-world signal before committing — at the cost of complexity and a longer rollout window.