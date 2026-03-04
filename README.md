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
│                    ┌──────────────────┼──────────────────┐              │
│                    ▼                  ▼                  ▼              │
│             ┌───────────┐  ┌──────────────────┐  ┌────────────┐        │
│             │ Git Pull  │  │  Docker Build    │  │ Push to ECR│        │
│             └───────────┘  └──────────────────┘  └─────┬──────┘        │
│                                                         │               │
│                    ┌────────────────────────────────────┘               │
│                    ▼                                                     │
│             ┌──────────────────┐                                         │
│             │  Health Check   │                                         │
│             │  (retry loop)   │                                         │
│             └────────┬────────┘                                         │
│              ✅ Pass │  ❌ Fail                                         │
│        ┌─────────────┴──────────────┐                                   │
│        ▼                            ▼                                   │
│  ┌───────────────┐         ┌─────────────────┐                          │
│  │ Notify user ✅│         │  Auto-Rollback  │                          │
│  └───────────────┘         │  Notify user ❌ │                          │
│                             └─────────────────┘                          │
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
├── .env.example                # Environment variable template
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
| `/status` | Staging | Show health status and deployed commit for all environments |

---

## Security Architecture

### Role-Based Access Control (RBAC)

```
ADMIN  → full access: production deploy, rollback, staging
         set via: ADMIN_TELEGRAM_IDS=123456789,987654321

STAGING → limited access: staging deploy + /status only
          set via: STAGING_TELEGRAM_IDS=111222333
```

### Command Injection Prevention

```python
# ❌ DANGEROUS — shell injection possible
subprocess.run(f"deploy.sh {user_input}", shell=True)

# ✅ SAFE — argument list, no shell interpolation
subprocess.run(["/app/scripts/deploy.sh", environment, commit])
```

### F811 Fix — Config.get_telegram_bot_token()

The original code had a Ruff F811 error (redefinition of unused name) because
`TELEGRAM_BOT_TOKEN` was defined twice in the same class body — once as a
`@property` and once as a class-level `str` attribute. The class attribute
silently overwrote the property, making the property unreachable.

**Fix:** both definitions removed and replaced with a single `@classmethod`:

```python
@classmethod
def get_telegram_bot_token(cls) -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "")
```

This gives one name, one definition, lazy evaluation, and full testability.

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
3. Confirmation dialog
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

## Teardown

```bash
cd terraform/
bash destroy.sh            # interactive
bash destroy.sh --dry-run  # preview only
bash destroy.sh --force    # skip confirmation (CI)
```

---

## Production Hardening Checklist

```
Infrastructure:
[ ] SSH: disable password auth and root login — key-only
[ ] Firewall: block all ports except 22 and 443
[ ] Rotate the SSH deploy key every 90 days
[ ] ECR: scan images on push, fail CI on CRITICAL CVEs

Bot Security:
[ ] Whitelist Telegram user IDs — never run as a public bot
[ ] Re-verify permissions on every callback
[ ] Validate all inputs with strict allow-lists
[ ] Never log secrets

Deployment:
[ ] Require PR approval before merging to main
[ ] GitHub Environment protection rules for production
[ ] Add post-deploy smoke tests on top of health check
```

---

*Built with Python · Runs on AWS EC2 · Deployed via Docker · Controlled via Telegram*
