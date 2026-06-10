/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_ORCHEO_BACKEND_URL?: string;
  readonly VITE_ORCHEO_CHATKIT_DOMAIN_KEY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

declare const __ORCHEO_STUDIO_VERSION__: string;

declare module "*.py?raw" {
  const content: string;
  export default content;
}
