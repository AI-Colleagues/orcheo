# Orcheo

Front‑end workspace for the Orcheo workflow studio prototype. The app is built with Vite, React 19, TypeScript, Tailwind, and shadcn/ui components.

## Installation

### As an npm package

```bash
npm install -g orcheo-studio    # install globally
# or
npm install orcheo-studio       # install locally in your project
```

### For development

```bash
uv run npm install              # install orcheo-studio dependencies
```

## Getting Started

### Using the CLI (after npm install)

```bash
orcheo-studio                   # start preview server (production mode)
orcheo-studio start             # start preview server (alias)
orcheo-studio dev               # start development server on http://localhost:2026
orcheo-studio build             # create a production build
orcheo-studio preview           # preview production build
```

### Using npm scripts (for development)

```bash
uv run npm run dev              # start Vite on http://localhost:2026
uv run npm run build            # create a production build
uv run npm run lint             # lint with eslint
uv run npm run preview          # preview production build
```

## Project Layout

- `src/main.tsx` / `src/App.tsx` — App bootstrap and router.
- `src/features/` — Feature modules (auth, workflow, account, support, shared).
- `src/design-system/` — Wrapped shadcn/ui primitives that back the UI.
- `src/hooks/`, `src/lib/` — Reusable hooks and utilities.

## Testing

The project is configured with Vitest and Testing Library. Run the suite with:

```bash
uv run npm test
```

## Notes

- The repo excludes `node_modules`; install dependencies before running scripts.
- Tailwind configuration lives in `tailwind.config.js` with PostCSS settings in `postcss.config.js`.
