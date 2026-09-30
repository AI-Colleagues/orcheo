"""Launch a credential-free Playwright server behind the public HTTPS proxy.

The lean stack runs this in a read-only container on internal-only networks,
without the worker's env file, vault, home directory, or persistent volumes.
The proxy is the browser's only internet route. The worker uses Playwright's
remote protocol; it never forwards its own network to the browser.
"""

from __future__ import annotations
import json
import os
from pathlib import Path
from playwright._impl._driver import compute_driver_executable


def main() -> None:
    """Launch Chromium with fixed public-only network settings."""
    node, cli = compute_driver_executable()
    package = str(Path(cli).parent)
    script = f"""
const {{ chromium }} = require({json.dumps(package)});
(async () => {{
  const server = await chromium.launchServer({{
    host: '0.0.0.0', port: 3000, wsPath: '/public-browser', headless: true,
    proxy: {{server: 'http://public-egress:8080', bypass: ''}},
    args: ['--proxy-bypass-list=<-loopback>', '--disable-quic',
           '--force-webrtc-ip-handling-policy=disable_non_proxied_udp']
  }});
  console.log('Public browser ready');
  for (const signal of ['SIGTERM', 'SIGINT']) {{
    process.on(signal, async () => {{ await server.close(); process.exit(0); }});
  }}
}})().catch(error => {{ console.error(error.message); process.exit(1); }});
"""
    os.execv(node, [node, "-e", script])


if __name__ == "__main__":
    main()
