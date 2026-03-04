# 🤖 Telegram Deployment Bot — Installation Guide

> **No installs on your laptop.** Everything runs inside AWS CloudShell — a free browser-based terminal built into the AWS Console.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Architecture Overview](#architecture-overview)
3. [Part 1 — Create Your Telegram Bot](#part-1--create-your-telegram-bot)
4. [Part 2 — Open CloudShell & Install Terraform](#part-2--open-cloudshell--install-terraform)
5. [Part 3 — AWS Identity Provider Setup (One-Time)](#part-3--aws-identity-provider-setup-one-time)
6. [Part 4 — Run Terraform to Provision AWS Resources](#part-4--run-terraform-to-provision-aws-resources)
7. [Part 5 — Generate & Install SSH Keys](#part-5--generate--install-ssh-keys)
8. [Part 6 — Add Secrets to GitHub](#part-6--add-secrets-to-github)
9. [Part 7 — Configure GitHub Environments](#part-7--configure-github-environments)
10. [Part 8 — Configure the Bot on EC2 Servers](#part-8--configure-the-bot-on-ec2-servers)
11. [Part 9 — Deploy & Verify](#part-9--deploy--verify)
12. [Starting & Stopping the Bot](#starting--stopping-the-bot)
13. [Troubleshooting](#troubleshooting)
14. [Final Pre-Deploy Checklist](#final-pre-deploy-checklist)

---

## Prerequisites

You need exactly three things before starting. Nothing needs to be installed on your local machine.

| Requirement | URL | Notes |
|-------------|-----|-------|
| AWS Account | [console.aws.amazon.com](https://console.aws.amazon.com) | Free tier is sufficient |
| GitHub Account | [github.com](https://github.com) | Free account is fine |
| Telegram App | Phone/Desktop | Used to create and manage your bot |

> **What's included in AWS CloudShell out of the box:** Python 3, Git, AWS CLI, pip, curl, wget, ssh, ssh-keygen. You only need to install one extra tool: **Terraform**.

---

## Architecture Overview

```
Your Laptop (Browser only)
        │
        ▼
  AWS CloudShell ──► GitHub Repository
        │                    │
        │              GitHub Actions
        │              (CI/CD Pipeline)
        │                    │
        ▼                    ▼
  AWS EC2 (Staging) ◄── Docker Image (ECR)
  AWS EC2 (Production) ◄── (after approval)
        │
        ▼
  Telegram Bot (Running in Docker)
```

The GitHub Actions pipeline:
1. Runs your test suite
2. Lints your code with `ruff`
3. Builds a Docker image and pushes it to AWS ECR
4. SSHs into your EC2 server(s) and deploys the container
5. Health-checks the deployment and notifies you via Telegram

---

## Part 1 — Create Your Telegram Bot

> Done entirely on your phone in ~2 minutes. No AWS or GitHub access needed yet.

### Step 1.1 — Create the bot via BotFather

1. Open Telegram on your phone.
2. Tap the **search icon** and search for `@BotFather`.
3. Select the result with a **blue checkmark** — this is the official one.
4. Tap **Start**.
5. Send the following command:

```
/newbot
```

6. BotFather will ask two questions:
   - **Name** — the display name shown in Telegram, e.g. `My Deploy Bot`
   - **Username** — must end in `bot`, e.g. `mydeploybot_bot`

7. BotFather replies with your **bot token**. It looks like this:

```
7123456789:AAHdqTcvCH1vGWJxfSeofSPs38eBlP2I9Igs
```

> ⚠️ **Save this token immediately.** Screenshot it or copy it to a notes app. You will need it in Part 6. Never share it — anyone with this token has full control over your bot.

---

### Step 1.2 — Get your personal Telegram User ID

1. In Telegram, search for `@userinfobot`.
2. Tap **Start**.
3. The bot immediately replies with your **User ID** — a plain number like `123456789`.

> 📝 Write this number down. It goes into your GitHub Secrets and your server `.env` file so the bot knows you are an admin.

---

## Part 2 — Open CloudShell & Install Terraform

### Step 2.1 — Open AWS CloudShell

1. Log in to [console.aws.amazon.com](https://console.aws.amazon.com).
2. Find the **`>_` terminal icon** in the top navigation bar.
3. Click it. A terminal panel opens at the bottom of your screen.
4. Wait ~10 seconds. You will see a prompt like:

```
[cloudshell-user@ip ~]$
```

> 💡 CloudShell saves files between sessions (up to 1 GB). If you close the browser and return, your files are still there.

> ⚠️ CloudShell times out after **20 minutes of inactivity**. If this happens, click the `>_` icon to reopen it. Your files are safe — just re-export the PATH (see [Troubleshooting](#troubleshooting)).

---

### Step 2.2 — Install Terraform

Copy and paste this entire block into CloudShell and press **Enter**:

```bash
# Download and install Terraform in CloudShell
cd ~

curl -fsSL \
  https://releases.hashicorp.com/terraform/1.7.5/terraform_1.7.5_linux_amd64.zip \
  -o terraform.zip

unzip -o terraform.zip

mkdir -p ~/.local/bin

mv terraform ~/.local/bin/terraform

export PATH=$PATH:~/.local/bin

echo 'export PATH=$PATH:~/.local/bin' >> ~/.bashrc

# Verify the installation:
terraform --version
```

**Expected output:**
```
Terraform v1.7.5
on linux_amd64
```

> 💡 You only need to do this once. CloudShell persists files between sessions, so Terraform will still be available the next time you open CloudShell.

---

### Step 2.3 — Clone your GitHub repository

Run the following in CloudShell, replacing `YOUR_USERNAME` and `YOUR_REPO` with your actual GitHub details:

```bash
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git
cd YOUR_REPO
```

> 💡 **Don't have a repo yet?** Go to [github.com](https://github.com) → **New repository** → upload your project files there first, then come back and clone it.

---

## Part 3 — AWS Identity Provider Setup (One-Time)

This step tells AWS to trust GitHub Actions to authenticate without storing AWS credentials. You do this **once per AWS account** and never again.

### Step 3.1 — Add GitHub as a trusted identity provider

1. In the AWS Console, search for **IAM** in the top search bar and click it.
2. In the left sidebar, click **Identity providers**.
3. Click **Add provider** (top right).
4. Fill in the form **exactly** as shown:

| Field | Value |
|-------|-------|
| Provider type | **OpenID Connect** |
| Provider URL | `https://token.actions.githubusercontent.com` |
| Audience | `sts.amazonaws.com` |

5. Click **Get thumbprint**.
6. Click **Add provider**.

> ✅ Done. Move on to Part 4. Skipping this step will cause the pipeline to fail at the "Configure AWS credentials" step.

---

## Part 4 — Run Terraform to Provision AWS Resources

Terraform reads the configuration in your project and automatically creates all AWS infrastructure: EC2 servers, an ECR image repository, IAM roles, and security groups.

> 💡 All commands run in CloudShell. Nothing runs on your laptop.

### Step 4.1 — Navigate to the Terraform folder

```bash
cd ~/YOUR_REPO/terraform
```

### Step 4.2 — Initialise Terraform

```bash
terraform init
```

**Expected output:**
```
Terraform has been successfully initialized!
```

### Step 4.3 — Preview changes (dry run)

```bash
terraform plan
```

This shows everything that will be created — no changes are made yet. Verify you can see EC2 instances, an ECR repository, and IAM roles in the output. Review before proceeding.

### Step 4.4 — Apply and create all resources

```bash
terraform apply
```

Terraform will prompt:
```
Do you want to perform these actions?
  Terraform will perform the actions described above.
  Only 'yes' will be accepted to approve.

  Enter a value:
```

Type `yes` and press **Enter**.

> ⏱️ This takes approximately **3–5 minutes** to complete.

### Step 4.5 — Copy the output values

When Terraform finishes, it prints output values. **Copy these now** — you'll need them in Parts 5, 6, and 8.

```
# Example output — your values will be different:

ecr_registry    = "123456789.dkr.ecr.us-east-1.amazonaws.com"
deploy_role_arn = "arn:aws:iam::123456789:role/myapp-github-actions-role"
staging_ip      = "54.123.45.67"
production_ip   = "54.123.45.89"
```

> 📝 If you forget to copy the output, run `terraform output` at any time to see it again.

---

## Part 5 — Generate & Install SSH Keys

GitHub Actions needs to SSH into your EC2 servers to deploy. You generate the key in CloudShell and install it on each server — also from CloudShell.

### Step 5.1 — Generate an SSH key pair

```bash
ssh-keygen -t ed25519 -f ~/.ssh/deploy_key -N ""
```

This creates two files:
- `~/.ssh/deploy_key` — the **private key** (goes into GitHub Secrets)
- `~/.ssh/deploy_key.pub` — the **public key** (installed on your EC2 servers)

---

### Step 5.2 — Install the public key on the staging server

SSH into your staging server using the IP from Terraform output:

```bash
ssh -i ~/.ssh/deploy_key ec2-user@YOUR_STAGING_IP
```

Once inside the EC2 server, run these commands:

```bash
sudo useradd -m deploy

sudo mkdir -p /home/deploy/.ssh

sudo bash -c 'echo "$(cat ~/.ssh/authorized_keys 2>/dev/null)" >> /home/deploy/.ssh/authorized_keys'

sudo chown -R deploy:deploy /home/deploy/.ssh

sudo chmod 700 /home/deploy/.ssh

sudo chmod 600 /home/deploy/.ssh/authorized_keys

sudo mkdir -p /opt/myapp

sudo chown deploy:deploy /opt/myapp

# Return to CloudShell:
exit
```

---

### Step 5.3 — Install the public key on the production server

Repeat the **exact same steps** as Step 5.2 but use `YOUR_PRODUCTION_IP`:

```bash
ssh -i ~/.ssh/deploy_key ec2-user@YOUR_PRODUCTION_IP
# ... run the same commands as above, then exit
```

---

### Step 5.4 — Copy the private key for GitHub

Run this in CloudShell to display the private key:

```bash
cat ~/.ssh/deploy_key
```

Select the **entire output** — including the `-----BEGIN OPENSSH PRIVATE KEY-----` and `-----END OPENSSH PRIVATE KEY-----` lines — and copy it. You'll paste it into GitHub Secrets in Part 6.

> ⚠️ Treat the private key like a password. Only paste it into GitHub Secrets. Never share it or commit it to a repository.

---

## Part 6 — Add Secrets to GitHub

GitHub Secrets store sensitive values securely. The Actions pipeline reads from them automatically at runtime — you never hardcode credentials in your code.

### How to navigate to GitHub Secrets

1. Go to your repository on [github.com](https://github.com).
2. Click the **Settings** tab (at the top of the repo page).
3. In the left sidebar, click **Secrets and variables**.
4. Click **Actions**.
5. Click **New repository secret** for each entry in the table below.

> 💡 Secret names are **case-sensitive**. Enter them exactly as shown.

### All required secrets

| Secret Name | Where to find the value | Purpose |
|-------------|------------------------|---------|
| `TELEGRAM_BOT_TOKEN` | BotFather reply (Part 1.1) | Sends Telegram deployment notifications |
| `TELEGRAM_CHAT_ID` | Your User ID from `@userinfobot` (Part 1.2) | Specifies who to notify |
| `ECR_REGISTRY` | Terraform output: `ecr_registry` | Docker image storage URL on AWS |
| `AWS_DEPLOY_ROLE_ARN` | Terraform output: `deploy_role_arn` | Allows GitHub to authenticate to AWS |
| `STAGING_SSH_KEY` | Output of `cat ~/.ssh/deploy_key` in CloudShell | SSH access to staging server |
| `PRODUCTION_SSH_KEY` | Output of `cat ~/.ssh/deploy_key` (same file) | SSH access to production server |
| `STAGING_HOST` | Terraform output: `staging_ip` | IP address of staging server |
| `PRODUCTION_HOST` | Terraform output: `production_ip` | IP address of production server |
| `STAGING_HEALTH_URL` | `http://YOUR_STAGING_IP/health` | URL the pipeline uses to health-check staging |
| `PRODUCTION_HEALTH_URL` | `http://YOUR_PRODUCTION_IP/health` | URL the pipeline uses to health-check production |

---

## Part 7 — Configure GitHub Environments

GitHub Environments add a **manual approval gate** before anything deploys to production. Without this, every push to `main` would deploy to production immediately.

### Step 7.1 — Create the production environment (approval required)

1. Go to your repo → **Settings** → **Environments**.
2. Click **New environment**.
3. Name it exactly: `production` (lowercase, no spaces).
4. Click **Configure environment**.
5. Under **Required reviewers**, search for your GitHub username and select it.
6. Click **Save protection rules**.

> ✅ When code is merged to `main`, GitHub will email you requesting approval before deploying to production. You click **Approve** in the GitHub UI and the deployment continues.

---

### Step 7.2 — Create the staging environment (no approval)

1. Click **New environment** again.
2. Name it: `staging`.
3. Leave all protection rules blank — no reviewers needed.
4. Click **Save**.

---

## Part 8 — Configure the Bot on EC2 Servers

The Telegram bot runs as a Docker container on EC2. It needs a `.env` file containing its runtime configuration. You create this from CloudShell.

### Step 8.1 — Configure the staging server

SSH into your staging server:

```bash
ssh -i ~/.ssh/deploy_key deploy@YOUR_STAGING_IP
```

Once inside, create the `.env` file (replace all placeholder values with your own):

```bash
cat > /opt/myapp/.env << 'EOF'
TELEGRAM_BOT_TOKEN=7123456789:AAHdqTcvCH1vGWJxfSeofSPs38eBlP2I9Igs
ADMIN_TELEGRAM_IDS=123456789
STAGING_TELEGRAM_IDS=123456789
REGISTRY_URL=123456789.dkr.ecr.us-east-1.amazonaws.com
REGISTRY_IMAGE=myapp
STAGING_HOST=54.123.45.67
PRODUCTION_HOST=54.123.45.89
EOF

chmod 600 /opt/myapp/.env

exit
```

---

### Step 8.2 — Configure the production server

Repeat the same steps on your production server:

```bash
ssh -i ~/.ssh/deploy_key deploy@YOUR_PRODUCTION_IP

# Then run the same cat > /opt/myapp/.env << 'EOF' ... EOF block above

exit
```

> 💡 **Multiple admins:** Add more Telegram User IDs to `ADMIN_TELEGRAM_IDS` separated by commas: `123456789,987654321`

---

## Part 9 — Deploy & Verify

Everything is configured. Push your code from CloudShell and the pipeline runs automatically on GitHub.

### Step 9.1 — Deploy to staging

In CloudShell:

```bash
cd ~/YOUR_REPO

git checkout -b develop

git add .

git commit -m "Initial setup"

git push origin develop
```

Then go to **github.com → your repo → Actions tab**. You'll see the pipeline running. It will:

1. Run your test suite (79 tests)
2. Check code quality with `ruff`
3. Build a Docker image and push it to ECR
4. SSH into the staging server and deploy the container
5. Health-check staging to confirm it's alive

> ⏱️ Expected time: ~3–5 minutes for the full pipeline to complete.

---

### Step 9.2 — Deploy to production

```bash
git checkout main

git merge develop

git push origin main
```

The pipeline will run tests, build the image, then **pause for approval**. GitHub sends you an email. To approve:

1. Go to **github.com → your repo → Actions tab**.
2. Click the workflow run showing **Waiting**.
3. Click **Review deployments**.
4. Check the box next to `production`.
5. Click **Approve and deploy**.

When the deployment succeeds, your Telegram bot sends you:

```
✅ Production deployment succeeded! Commit: a1b2c3d4
```

---

## Starting & Stopping the Bot

The bot runs as a Docker Compose service on your EC2 servers. SSH in from CloudShell to manage it.

### Connect to a server

```bash
# Staging
ssh -i ~/.ssh/deploy_key deploy@YOUR_STAGING_IP

# Production
ssh -i ~/.ssh/deploy_key deploy@YOUR_PRODUCTION_IP
```

### Check status

```bash
cd /opt/myapp
docker compose ps
```

### View live logs

```bash
docker compose logs -f
```

### Restart the bot

```bash
docker compose restart
```

### Stop the bot

```bash
docker compose down
```

### Start the bot manually

```bash
docker compose up -d
```

### Pull the latest image and redeploy manually

```bash
docker compose pull
docker compose up -d
```

---

## Troubleshooting

### `terraform: command not found` in CloudShell

CloudShell timed out and the `PATH` was reset. Re-run:

```bash
export PATH=$PATH:~/.local/bin
```

To make this permanent again:

```bash
echo 'export PATH=$PATH:~/.local/bin' >> ~/.bashrc
```

---

### Pipeline fails at "Configure AWS credentials"

**Cause:** The identity provider is missing or the role ARN is wrong.

**Fix:**
1. Verify you completed Part 3 (GitHub identity provider in AWS IAM).
2. Check that `AWS_DEPLOY_ROLE_ARN` in GitHub Secrets **exactly** matches the value from `terraform output`.
3. The role will never trust GitHub if the identity provider was skipped.

---

### Pipeline fails at "Deploy via SSH"

**Cause:** The SSH private key in GitHub Secrets is missing header/footer lines or is malformed.

**Fix:**
1. Re-display the key: `cat ~/.ssh/deploy_key`
2. Copy the **entire output**, including:
   ```
   -----BEGIN OPENSSH PRIVATE KEY-----
   ...
   -----END OPENSSH PRIVATE KEY-----
   ```
3. Re-paste it into the `STAGING_SSH_KEY` and `PRODUCTION_SSH_KEY` secrets.

To verify the SSH connection manually:

```bash
ssh -i ~/.ssh/deploy_key deploy@YOUR_SERVER_IP echo ok
# Expected output: ok
# If you see "Permission denied": the public key is not installed on the server
```

If permission is denied, re-run the public key installation steps from Part 5.2/5.3.

---

### Health check fails after deploy

**Cause:** The Docker containers may not have started correctly.

**Diagnosis:**

```bash
ssh -i ~/.ssh/deploy_key deploy@YOUR_STAGING_IP

cd /opt/myapp

docker compose ps      # Are containers running?
docker compose logs    # Any errors in the logs?
```

Common causes:
- The `.env` file is missing or has incorrect values.
- Docker failed to pull the image from ECR (check `ECR_REGISTRY` secret).
- Port conflict — another process is using the same port.

---

### Bot doesn't respond in Telegram

**Cause:** The `.env` file on the server has incorrect values.

**Fix:**

```bash
ssh -i ~/.ssh/deploy_key deploy@YOUR_STAGING_IP

cat /opt/myapp/.env          # Verify values look correct
docker compose logs           # Look for "Unauthorized" or token errors
```

Common issues:
- `TELEGRAM_BOT_TOKEN` is wrong or has extra whitespace.
- The bot was deleted or the token was regenerated in BotFather.
- `ADMIN_TELEGRAM_IDS` doesn't include your User ID, so the bot is running but ignoring your messages.

---

### CloudShell files are gone

**Cause:** CloudShell storage persists between sessions but has a **120-day inactivity limit**.

**Fix:** Re-clone your repository and re-run `terraform output` to retrieve your values. Your AWS resources (EC2 instances, ECR, IAM roles) are **not affected** — only your CloudShell working directory was cleared.

```bash
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git
cd YOUR_REPO/terraform
terraform output
```

---

### `terraform apply` fails mid-way

**Cause:** A resource already partially exists, or there's a permissions issue.

**Fix:**

```bash
terraform plan   # Check for errors or conflicts
terraform apply  # Re-run — Terraform is idempotent and will skip already-created resources
```

If you need to start completely fresh:

```bash
terraform destroy   # Destroys all created AWS resources
terraform apply     # Re-creates everything from scratch
```

> ⚠️ `terraform destroy` permanently deletes your EC2 servers and data. Only use this if you intend to start over.

---

## Final Pre-Deploy Checklist

Tick every item before your first push:

- [ ] Created Telegram bot via BotFather and saved the token (Part 1.1)
- [ ] Obtained your Telegram User ID from `@userinfobot` (Part 1.2)
- [ ] Opened AWS CloudShell and installed Terraform (Part 2.2)
- [ ] Cloned your GitHub repository into CloudShell (Part 2.3)
- [ ] Added GitHub as an identity provider in AWS IAM (Part 3)
- [ ] Ran `terraform apply` and copied the 4 output values (Part 4)
- [ ] Generated SSH key pair in CloudShell with `ssh-keygen` (Part 5.1)
- [ ] Installed public key and created `deploy` user on **both** servers (Parts 5.2 & 5.3)
- [ ] Added all 10 secrets to GitHub repository (Part 6)
- [ ] Created `production` environment in GitHub with yourself as reviewer (Part 7.1)
- [ ] Created `staging` environment in GitHub with no reviewer (Part 7.2)
- [ ] Created `.env` file on **both** servers with correct bot token and settings (Part 8)

---

> 🚀 **All boxes ticked?** Run `git push origin develop` in CloudShell and open the **Actions** tab in GitHub. Your bot will be live in approximately 5 minutes.