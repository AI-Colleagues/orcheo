# Execution Worker Deployment Guide

This guide covers deploying the Orcheo execution worker with Celery and Redis.

## Environment Variables

### Required

| Variable | Description | Default | Example |
|----------|-------------|---------|---------|
| `REDIS_URL` | Redis connection URL for Celery broker | `redis://localhost:6379/0` | `redis://redis.example.com:6379/0` |

### Optional

| Variable | Description | Default |
|----------|-------------|---------|
| `CELERY_CONCURRENCY` | Number of worker processes | `4` |
| `CELERY_LOG_LEVEL` | Logging level (DEBUG, INFO, WARNING, ERROR) | `info` |

### Example Environment File

Create `/etc/orcheo/orcheo.env`:

```bash
# Redis broker configuration
REDIS_URL=redis://localhost:6379/0

# Worker configuration
CELERY_CONCURRENCY=4
CELERY_LOG_LEVEL=info

# Application settings (inherited from existing Orcheo config)
# ORCHEO_AUTH_MODE=jwt
# ORCHEO_DATABASE_URL=postgresql://orcheo:change-me@localhost:5432/orcheo


## Local Development

### Option 1: Docker Compose (Recommended for Quick Start)

Start the full stack with a single command:

```bash
# Build and start all services
make docker-up

# View logs
make docker-logs

# Stop all services
make docker-down
```

This starts:
- **Redis** on port 6379
- **Backend API** on port 2025 (with hot reload)
- **Canvas UI** on port 2026 (with hot reload)
- **Celery Worker** for background task execution
- **Celery Beat** for scheduled task dispatching

The source code is mounted as volumes, so changes are reflected immediately.

### Option 2: Native Commands (Faster Iteration)

For faster debugging and development feedback, run services natively:

#### Prerequisites

1. Install Redis:
   ```bash
   # macOS
   brew install redis
   brew services start redis

   # Or use Docker
   make redis
   ```

2. Install dependencies:
   ```bash
   uv sync
   ```

#### Running Services

Start all services in separate terminals:

```bash
# Terminal 1: Start Redis (if using Docker)
make redis

# Terminal 2: Start API server
make dev-server

# Terminal 3: Start Celery worker
make worker

# Terminal 4: Start Celery Beat scheduler (for cron triggers)
make celery-beat
```
