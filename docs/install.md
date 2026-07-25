# Installation

>[!TIP]
> Existing `cync_mesh.yaml`? simply use the config as it is: bind mount into the docker container.

## Docker

### Pull the published image (easiest)

Multi-arch images (`linux/amd64`, `linux/arm64`) are published to GitHub
Packages on every `cync-lan-mqtt-vX.Y.Z` release:

```bash
docker pull ghcr.io/proxy-alt/cync-lan-mqtt:latest
```

Pin a version instead of `latest` if you would rather upgrade deliberately:
`ghcr.io/proxy-alt/cync-lan-mqtt:0.2.1`. `latest` only ever moves to a full
release, never to a `bN` beta.

>[!NOTE]
> `linux/arm/v7` (32-bit ARM) is **not** published. Neither `uvloop` nor
> `cffi` ships an armv7l wheel, so the image cannot be built for it without
> bundling a full C/Rust toolchain. `arm64` covers Raspberry Pi 3/4/5 on any
> 64-bit OS.

### Or build it locally
- Clone the repo 
- `cd` into the repo directory
- `docker compose -f ./docker/Dockerfile build` will output a `ghcr.io/proxy-alt/cync-lan-mqtt:latest` tagged image
- Copy the example `docker-compose.yaml` file and edit it for your setup.
- Set up env vars using the docker-compose `environment` section or uncomment the `env_file` option and create an .env file (See [example](../docker/example.env))

#### Upgrading
- Rebuild the image (use no-cache for a clean build): `docker compose -f ./docker/Dockerfile build --no-cache`