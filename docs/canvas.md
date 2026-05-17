# Canvas

Orcheo Canvas is the visual interface for monitoring, configuring, and managing workflows. It provides a read-only graph view of workflow structure, a configuration panel for adjusting default node parameters, and a credentials manager for securely storing API keys and secrets via the Credential Vault.

## Installation

```bash
# Install globally
npm install -g orcheo-canvas

# Or install locally in your project
npm install orcheo-canvas
```

## Usage

After installation, start the Canvas interface:

```bash
# Start preview server (production mode)
orcheo-canvas

# Start development server
orcheo-canvas dev

# Build for production
orcheo-canvas build

# Preview production build
orcheo-canvas preview
```

The Canvas application will be available at:

- **Development mode**: `http://localhost:2026`
- **Production mode**: Configured preview port

## Configuration

Canvas connects to the Orcheo backend API. Configure the connection via environment variables:

```bash
# Backend API URL
VITE_ORCHEO_BACKEND_URL=http://localhost:2025

# Authentication (optional)
VITE_ORCHEO_AUTH_ISSUER=https://your-idp.com/
VITE_ORCHEO_AUTH_CLIENT_ID=your-client-id
```

## Docker Compose

When running the full stack with Docker Compose, Canvas is included automatically:

```bash
docker compose up -d
```

Canvas will be available at `http://localhost:2026`.

See [Manual Setup Guide](manual_setup.md#docker-compose-full-stack) for the complete Docker Compose setup.

## Features

- **Workflow graph view**: Visualise the structure and node connections of any registered workflow (read-only; authoring is done in code or via your AI coding agent)
- **Default config editor**: Adjust default parameters for each node without touching code
- **Credential Vault manager**: Store, rotate, and delete encrypted credentials (API keys, tokens, passwords) that nodes can reference by name; secrets are visible only to human operators and never exposed to AI agents
- **Real-time execution monitoring**: Watch workflow runs with live status updates per node
- **ChatKit integration**: Test conversational workflows directly in Canvas
- **Version awareness**: Top navigation shows Canvas + backend versions
- **Update reminders**: Non-blocking reminder appears when updates are available (checked at most once every 24 hours per browser profile)
