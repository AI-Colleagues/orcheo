# Hosted Apps hello example

Build the uploadable bundle from this directory so `index.html` remains at the
ZIP root:

```bash
zip -r ../hello-app.zip index.html styles.css app.js
```

The example is static-only and intentionally uses no external dependencies or
service worker.
