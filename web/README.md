# Export UI

The device-export page the `cync-lan-mqtt` exporter serves. Vite + Tailwind,
no UI framework — it is one page with about a hundred lines of imperative
logic, and React or Astro would add weight without removing any.

```bash
npm install
npm run dev      # dev server with HMR
npm run build    # type-check, then bundle to dist/
```

`npm run dev` serves the page but not the API behind it, so the export buttons
will fail against a dev server alone. Point a browser at a running exporter for
the real flow.

## Nothing here is committed built

`dist/` is produced by the Docker build's node stage and copied into the image.
There is no checked-in bundle, so there is no generated file that can drift
from its source.

## The one configuration that is easy to get wrong

The exporter mounts `StaticFiles` at the URL `/static` pointing *at* the asset
directory, so `/static/app.js` resolves to `<dir>/app.js` — there is no nested
`static` folder on disk. That is why `vite.config.ts` pairs `base: "./"` with
`assetsDir: "static"`: assets land in `dist/static/`, are referenced
relatively as `./static/…`, and the Dockerfile flattens them into `www/`.

Getting the pair wrong 404s every asset while the page itself still loads,
which presents as a styling bug rather than a path bug.

`base` must stay relative. The page is reachable behind a path prefix, and an
absolute `/static/…` would break there — the original hand-written page was
careful about this too.

## What replaced what

The previous version vendored pre-minified files and inlined its script:

| Before | After |
|---|---|
| `animate.min.css`, 71 kB | two `@keyframes` — only `fadeIn` and `shakeX` were ever used |
| `prism.min.js` + `prism-yaml.min.js`, 21 kB | `prismjs` from npm, YAML component only |
| `main.min.css`, 72 kB | Tailwind v4, built against the actual markup |
| 104 lines of inline `<script>` and four `onclick=` attributes | `src/main.ts`, handlers bound by id |

162 kB of assets became 41 kB, and the bundle is hashed, so it can be served
with a strict CSP and cached properly.
