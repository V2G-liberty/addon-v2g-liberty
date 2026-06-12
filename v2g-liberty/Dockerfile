ARG BUILD_FROM=ghcr.io/hassio-addons/base:15.0.9
# hadolint ignore=DL3006
FROM ${BUILD_FROM}

# Set shell
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Copy Python requirements file
COPY requirements.txt /tmp/

# Setup base
# hadolint ignore=DL3003,DL3042
RUN \
    apk add --no-cache --virtual .build-dependencies \
        python3-dev \
    \
    && apk add --no-cache \
        py3-pip \
        py3-wheel \
        python3 \
    \
    && pip install -r /tmp/requirements.txt \
    \
    && find /usr \
        \( -type d -a -name test -o -name tests -o -name '__pycache__' \) \
        -o \( -type f -a -name '*.pyc' -o -name '*.pyo' \) \
        -exec rm -rf '{}' + \
    \
    && apk del --no-cache --purge .build-dependencies

# Copy root filesystem
COPY rootfs /

# Strip dev-only files so they are never shipped in the production add-on.
# The dev container loads everything straight from the repo (it does not use
# this image), so removing these does not affect development:
#   - apps/dev_tools/             : dev-only tooling (e.g. the grid/PV emulator)
#   - appdaemon/tests/ & /root/tests/ : the test suites
#   - appdaemon.devcontainer.yaml : the dev AppDaemon config (prod uses appdaemon.yaml)
# (appdaemon.yaml's exclude_dirs only stops app discovery; it does not remove files.)
RUN rm -rf /root/appdaemon/apps/dev_tools \
    /root/appdaemon/tests \
    /root/appdaemon/appdaemon.devcontainer.yaml \
    /root/tests

# Build arguments
ARG BUILD_ARCH
ARG BUILD_DATE
ARG BUILD_DESCRIPTION
ARG BUILD_NAME
ARG BUILD_REF
ARG BUILD_REPOSITORY
ARG BUILD_VERSION

# Labels
LABEL \
    io.hass.name="${BUILD_NAME}" \
    io.hass.description="${BUILD_DESCRIPTION}" \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.type="addon" \
    io.hass.version=${BUILD_VERSION} \
    maintainer="Ard Jonker <post@v2g-liberty.eu>" \
    org.opencontainers.image.title="${BUILD_NAME}" \
    org.opencontainers.image.description="${BUILD_DESCRIPTION}" \
    org.opencontainers.image.vendor="V2G Liberty" \
    org.opencontainers.image.authors="Ard Jonker <post@v2g-liberty.eu>" \
    org.opencontainers.image.licenses="Apache 2.0" \
    org.opencontainers.image.url="https://v2g-liberty.eu" \
    org.opencontainers.image.source="https://github.com/${BUILD_REPOSITORY}" \
    org.opencontainers.image.documentation="https://github.com/${BUILD_REPOSITORY}/blob/main/README.md" \
    org.opencontainers.image.created=${BUILD_DATE} \
    org.opencontainers.image.revision=${BUILD_REF} \
    org.opencontainers.image.version=${BUILD_VERSION}
