# 🤖 Telegram Deployment Bot — Installation Guide

> **No installs on your laptop.** Everything runs inside AWS CloudShell — a free browser-based terminal built into the AWS Console.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Part 1 — Create Your Telegram Bot](#part-1--create-your-telegram-bot)
3. [Part 2 — Open CloudShell & Install Terraform](#part-2--open-cloudshell--install-terraform)
4. [Part 3 — AWS Identity Provider Setup (One-Time)](#part-3--aws-identity-provider-setup-one-time)
5. [Part 4 — Run Terraform to Provision AWS Resources](#part-4--run-terraform-to-provision-aws-resources)
6. [Part 5 — Generate & Install SSH Keys](#part-5--generate--install-ssh-keys)
7. [Part 6 — Add Secrets to GitHub](#part-6--add-secrets-to-github)
8. [Part 7 — Configure GitHub Environments](#part-7--configure-github-environments)
9. [Part 8 — Configure the Bot on EC2 Servers](#part-8--configure-the-bot-on-ec2-servers)
10. [Part 9 — Deploy & Verify](#part-9--deploy--verify)
11. [Starting & Stopping the Bot](#starting--stopping-the-bot)
12. [Troubleshooting](#troubleshooting)
13. [Final Pre-Deploy Checklist](#final-pre-deploy-checklist)

---

## Prerequisites

| Requirement | URL | Notes |
|-------------|-----|-------|
| AWS Account | [console.aws.amazon.com](https://console.aws.amazon.com) | Free tier is sufficient |
| GitHub Account | [github.com](https://github.com) | Free account is fine |
| Telegram App | Phone/Desktop | Used to create and manage your bot |

---

## Part 1 — Create Your Telegram Bot

### Step 1.1 — Create the bot via BotFather

1. Open Telegram on your phone.
2. Search for `@BotFather` (blue checkmark — official).
3. Tap **Start**, then send `/newbot`.
4. Provide a display name and a username ending in `bot`.
5. Save the bot token BotFather sends back — it looks like `7123456789:AAH...`.

> ⚠️ Never share this token. Anyone with it controls your bot.

### Step 1.2 — Get your Telegram User ID

1. Search for `@userinfobot` in Telegram.
2. Tap **Start** — it immediately replies with your numeric User ID.

---

## Part 2 — Open CloudShell & Install Terraform

### Step 2.1 — Open AWS CloudShell

1. Log in to [console.aws.amazon.com](https://console.aws.amazon.com).
2. Click the **`>_`** terminal icon in the top navigation bar.
3. Wait ~10 seconds for the prompt to appear.

### Step 2.2 — Install Terraform

```bash
cd ~
curl -fsSL \
  https://releases.hashicorp.com/terraform/1.7.5/terraform_1.7.5_linux_amd64.zip \
  -o terraform.zip
unzip -o terraform.zip
mkdir -p ~/.local/bin
mv terraform ~/.local/bin/terraform
export PATH=$PATH:~/.local/bin
echo 'export PATH=$PATH:~/.local/bin' >> ~/.bashrc
terraform --version
```

### Step 2.3 — Clone your repository

```bash
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git
cd YOUR_REPO/
```

---

## Part 3 — AWS Identity Provider Setup (One-Time)

1. In AWS Console, go to **IAM → Identity providers → Add provider**.
2. Fill in:
   - Provider type: **OpenID Connect**
   - Provider URL: `https://token.actions.githubusercontent.com`
   - Audience: `sts.amazonaws.com`
3. Click **Get thumbprint**, then **Add provider**.

---

## Part 4 — Run Terraform to Provision AWS Resources

```bash
cd YOUR_REPO/terraform
terraform init
terraform plan
terraform apply   # type 'yes' when prompted
```

**Copy these output values** — you'll need them later:

```
ecr_registry    = "123456789.dkr.ecr.us-east-1.amazonaws.com"
deploy_role_arn = "arn:aws:iam::123456789:role/deploy-bot-github-actions-role"
staging_ip      = "54.123.45.67"
production_ip   = "54.123.45.89"
```

Run `terraform output` at any time to see them again.

---

## Part 5 — Generate & Install SSH Keys

### Step 5.1 — Generate key pair

```bash
ssh-keygen -t ed25519 -f ~/.ssh/deploy_key -N ""
```

### Step 5.2 — Install on staging server

```bash
ssh -i ~/.ssh/deploy_key ec2-user@YOUR_STAGING_IP
```

Then on the server:

```bash
sudo useradd -m deploy
sudo mkdir -p /home/deploy/.ssh
sudo bash -c 'cat ~/.ssh/authorized_keys >> /home/deploy/.ssh/authorized_keys'
sudo chown -R deploy:deploy /home/deploy/.ssh
sudo chmod 700 /home/deploy/.ssh && sudo chmod 600 /home/deploy/.ssh/authorized_keys
sudo mkdir -p /opt/myapp && sudo chown deploy:deploy /opt/myapp
exit
```

### Step 5.3 — Repeat for production server

Same steps as 5.2, using `YOUR_PRODUCTION_IP`.

### Step 5.4 — Copy private key for GitHub

```bash
cat ~/.ssh/deploy_key
```

Copy the entire output including the `-----BEGIN/END OPENSSH PRIVATE KEY-----` lines.

---

## Part 6 — Add Secrets to GitHub

Go to your repo → **Settings → Secrets and variables → Actions → New repository secret**.

| Secret Name | Value Source |
|-------------|-------------|
| `TELEGRAM_BOT_TOKEN` | BotFather reply |
| `TELEGRAM_CHAT_ID` | Your User ID from @userinfobot |
| `ECR_REGISTRY` | `terraform output ecr_registry` |
| `AWS_DEPLOY_ROLE_ARN` | `terraform output deploy_role_arn` |
| `STAGING_SSH_KEY` | Contents of `~/.ssh/deploy_key` |
| `PRODUCTION_SSH_KEY` | Contents of `~/.ssh/deploy_key` |
| `STAGING_HOST` | `terraform output staging_ip` |
| `PRODUCTION_HOST` | `terraform output production_ip` |
| `STAGING_HEALTH_URL` | `http://YOUR_STAGING_IP/health` |
| `PRODUCTION_HEALTH_URL` | `http://YOUR_PRODUCTION_IP/health` |

---

## Part 7 — Configure GitHub Environments

### Production (approval required)

1. Go to repo → **Settings → Environments → New environment**.
2. Name it `production`.
3. Under **Required reviewers**, add your GitHub username.
4. Click **Save protection rules**.

### Staging (no approval)

1. Create another environment named `staging`.
2. Leave all protection rules blank.

---

## Part 8 — Configure the Bot on EC2 Servers

SSH into each server and create the `.env` file:

```bash
ssh -i ~/.ssh/deploy_key deploy@YOUR_STAGING_IP

cat > /opt/myapp/.env << 'EOF'
TELEGRAM_BOT_TOKEN=your-bot-token
ADMIN_TELEGRAM_IDS=your-user-id
STAGING_TELEGRAM_IDS=your-user-id
REGISTRY_URL=your-ecr-registry-url
REGISTRY_IMAGE=myapp
STAGING_HOST=your-staging-ip
PRODUCTION_HOST=your-production-ip
EOF

chmod 600 /opt/myapp/.env
exit
```

Repeat for the production server.

---

## Part 9 — Deploy & Verify

### Deploy to staging

```bash
cd ~/YOUR_REPO
git checkout -b develop
git add .
git commit -m "Initial setup"
git push origin develop
```

Watch the pipeline in **GitHub → Actions tab**. It runs tests, builds, and deploys in ~3–5 minutes.

### Deploy to production

```bash
git checkout main
git merge develop
git push origin main
```

The pipeline pauses for your approval. Go to **Actions → the waiting run → Review deployments → Approve**.

On success, your Telegram bot sends: `✅ Production deployment succeeded!`

---

## Starting & Stopping the Bot

```bash
# Connect to server
ssh -i ~/.ssh/deploy_key deploy@YOUR_SERVER_IP

cd /opt/myapp

docker compose ps          # check status
docker compose logs -f     # view live logs
docker compose restart     # restart
docker compose down        # stop
docker compose up -d       # start
docker compose pull && docker compose up -d  # pull latest and redeploy
```

---

## Troubleshooting

### `terraform: command not found`
```bash
export PATH=$PATH:~/.local/bin
```

### Pipeline fails at "Configure AWS credentials"
Verify you completed Part 3 and that `AWS_DEPLOY_ROLE_ARN` exactly matches `terraform output`.

### Pipeline fails at "Deploy via SSH"
Re-copy the private key — ensure it includes the full `-----BEGIN/END OPENSSH PRIVATE KEY-----` lines.

### Health check fails after deploy
```bash
ssh -i ~/.ssh/deploy_key deploy@YOUR_SERVER_IP
cd /opt/myapp
docker compose ps    # are containers running?
docker compose logs  # any errors?
```

### Bot doesn't respond in Telegram
Check `.env` on the server — wrong token or your User ID not in `ADMIN_TELEGRAM_IDS`.

---

## Final Pre-Deploy Checklist

- [ ] Created Telegram bot via BotFather and saved the token
- [ ] Obtained Telegram User ID from @userinfobot
- [ ] Opened AWS CloudShell and installed Terraform
- [ ] Cloned repository into CloudShell
- [ ] Added GitHub as identity provider in AWS IAM
- [ ] Ran `terraform apply` and copied the 4 output values
- [ ] Generated SSH key pair in CloudShell
- [ ] Installed public key and created `deploy` user on **both** servers
- [ ] Added all 10 secrets to GitHub repository
- [ ] Created `production` environment in GitHub with yourself as reviewer
- [ ] Created `staging` environment in GitHub with no reviewer
- [ ] Created `.env` file on **both** servers with correct values

> 🚀 **All boxes ticked?** Run `git push origin develop` and open the **Actions** tab. Your bot will be live in ~5 minutes.
