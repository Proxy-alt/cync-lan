# The export UI is a Vite app. Building it here keeps node out of the final
# image and keeps built assets out of the repository - nothing under web/dist
# is committed, so there is no generated output to drift from its source.
FROM node:26-slim AS web

WORKDIR /web
# package files first: this layer only rebuilds when dependencies change.
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build


# Trixie (Debian 13), not bookworm (Debian 12). A Docker Scout scan of the
# published image found 48 vulnerabilities, every one of them in a base-image
# package and every one reported as "not fixed" - bookworm is oldstable, so
# those fixes are not coming. No amount of `apt-get upgrade` in this file
# changes that; the base itself is the only lever.
#
# `python:3.14-slim` currently resolves to exactly this image, but the distro
# is named explicitly so a future Debian release cannot silently change the
# runtime underneath a rebuild, and so Dependabot has a concrete tag to bump.
FROM python:3.14-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    DEBIAN_FRONTEND=noninteractive

# NO build-time certificate generation here, deliberately.
#
# This used to run `openssl req -x509 ...`, which baked one key pair into the
# published image - so every install that pulled it shared a single private
# key, publicly extractable from the image layers. A Trivy scan flags it as
# an exposed asymmetric private key, correctly.
#
# It was also redundant. cync_lan.server._ensure_self_signed_cert already
# generates the pair at startup when either file is missing, using identical
# parameters (RSA 4096, CN=*.xlink.cn, self-signed, 3650 days) - it exists
# because a HACS install has no build step at all. Pre-creating the files here
# is precisely what stopped it running: it only generates when they are
# absent. Removing this step means each container mints its own key on first
# start, and drops the image's need for an openssl binary entirely.


# Install dependencies first, lessens cache hit on rebuilds. cync-lan (the
# core protocol library this add-on depends on) is installed from PyPI here
# too, rather than deferred to the final `pip install .` below, so its own
# transitive deps (pydantic, tzlocal, cryptography) land in this cached
# layer as well.
RUN set -x \
    && pip install --no-cache-dir \
        "cync-lan>=0.1.0" \
        "pyyaml==6.0.2" \
        "uvloop>=0.21.0" \
        "aiomqtt==2.3.0" \
        "paho-mqtt" \
        "aiohttp>=3.10.8" \
        "fastapi>=0.115.12" \
        "uvicorn>=0.34.3"

# copy, install, remove src for smaller image
WORKDIR /tmp/cync-src
COPY ./ .
RUN set -x \
    && pip install . \
    && rm -rf /tmp/cync-src

# Web App. The exporter mounts StaticFiles at the URL "/static" pointing at
# this directory, so "/static/app.js" must resolve to "www/app.js" - hence the
# hashed assets are flattened out of dist/static/ into www/ rather than copied
# as a nested folder.
COPY --from=web /web/dist/index.html /root/cync-lan/www/index.html
COPY --from=web /web/dist/static/ /root/cync-lan/www/

VOLUME /root/cync-lan/config
EXPOSE 23779 23778

# image.source is what links a published package to its repository in the
# GitHub Packages UI. This pointed at baudneo/cync-lan - the upstream this
# was forked from - which would attach the package to somebody else's repo.
LABEL org.opencontainers.image.source="https://github.com/Proxy-alt/cync-lan"
LABEL org.opencontainers.image.description="Local async MQTT controller for Cync/C by GE Wi-Fi devices"
LABEL org.opencontainers.image.licenses="MIT"

CMD ["cync-lan"]