# Studio

Orcheo Studio is the visual interface for monitoring, configuring, and managing workflows. It provides a read-only graph view of workflow structure, a configuration panel for adjusting default node parameters, and a credentials manager for securely storing API keys and secrets via the Credential Vault.

## Installation

```bash
# Install globally
npm install -g orcheo-studio

# Or install locally in your project
npm install orcheo-studio
```

## Usage

After installation, start the Studio interface:

```bash
# Start preview server (production mode)
orcheo-studio

# Start development server
orcheo-studio dev

# Build for production
orcheo-studio build

# Preview production build
orcheo-studio preview
```

The Studio application will be available at:

- **Development mode**: `http://localhost:2026`
- **Production mode**: Configured preview port

## Configuration

Studio connects to the Orcheo backend API. Configure the connection via environment variables:

```bash
# Backend API URL
VITE_ORCHEO_BACKEND_URL=http://localhost:2025

# Authentication (optional)
VITE_ORCHEO_AUTH_ISSUER=https://your-idp.com/
VITE_ORCHEO_AUTH_CLIENT_ID=your-client-id
```

## Docker Compose

When running the full stack with Docker Compose, Studio is included automatically:

```bash
docker compose up -d
```

Studio will be available at `http://localhost:2026`.

See [Manual Setup Guide](manual_setup.md#docker-compose-full-stack) for the complete Docker Compose setup.

## Features

- **Workflow graph view**: Visualise the structure and node connections of any registered workflow (read-only; authoring is done in code or via your AI coding agent)
- **Default config editor**: Adjust default parameters for each node without touching code
- **Credential Vault manager**: Store, rotate, and delete encrypted credentials (API keys, tokens, passwords) that nodes can reference by name; secrets are visible only to human operators and never exposed to AI agents
- **Real-time execution monitoring**: Watch workflow runs with live status updates per node
- **ChatKit integration**: Test conversational workflows directly in Studio
- **Version awareness**: Top navigation shows Studio + backend versions
- **Update reminders**: Non-blocking reminder appears when updates are available (checked at most once every 24 hours per browser profile)
