# ── Builder Stage ──────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY bot/requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt


# ── Runtime Stage ──────────────────────────────────────────────────────────────
FROM python:3.12-slim

# Security: run as non-root user
RUN groupadd -r botuser && useradd -r -g botuser botuser

WORKDIR /app

# Install runtime dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    # For deploy scripts:
    docker.io \
    curl \
    openssh-client \
    git \
    awscli \
    # kubectl (optional — comment out if not using k8s)
    # kubectl \
    && rm -rf /var/lib/apt/lists/*

# Copy Python packages from builder
COPY --from=builder /root/.local /home/botuser/.local

# Copy application code
COPY bot/ ./bot/
COPY scripts/ ./scripts/

# Make scripts executable
RUN chmod +x /app/scripts/*.sh

# Create state and log directories
RUN mkdir -p /var/lib/deploybot /var/log/deploybot \
    && chown -R botuser:botuser /var/lib/deploybot /var/log/deploybot /app

# Security: don't run as root
USER botuser

ENV PYTHONPATH=/app
ENV PATH="/home/botuser/.local/bin:${PATH}"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Health check for the bot container itself
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import telegram; print('ok')" || exit 1

CMD ["python", "/app/bot/bot.py"]
