# SPDX-License-Identifier: MIT OR Apache-2.0
FROM node:22.23.4-bookworm-slim@sha256:de170b39439eb0e5a3444ad7d60b015614a381e10fecae2e30ec9e735122f38d AS web
WORKDIR /build/web
RUN npm install --global pnpm@10.33.3 && test "$(node --version)" = v22.23.4
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

FROM ghcr.io/astral-sh/uv:0.13.0@sha256:cdc6093146eb3ff6a40107b38f008b789e050e77ad87865e381d9917da55a168 AS uv
FROM python:3.12.15-alpine3.24@sha256:b658b05ce9c4eb97ec6fc19424818a1f31993659c2b505a532f470e83807815d AS app
LABEL org.opencontainers.image.title="Offerloom M0" org.opencontainers.image.licenses="MIT OR Apache-2.0"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH=/app/.venv/bin:$PATH HOME=/tmp OFFERLOOM_WEB_DIST=/app/web/dist
WORKDIR /app
COPY --from=uv /uv /usr/local/bin/uv
# Preserve APK ownership/license metadata for the exported-image collector.
# libpq-dev supplies the linker name; binutils supplies CPython's libpq discovery.
RUN test "$(python --version)" = 'Python 3.12.15' \
 && apk add --no-cache libpq libpq-dev binutils pango harfbuzz-subset libjpeg-turbo openjpeg libffi fontconfig font-dejavu ca-certificates \
 && mkdir -p /usr/share/offerloom /data/blobs /spool /import-spool /run/offerloom \
 && apk info -v > /usr/share/offerloom/native-packages.txt \
 && fc-list > /usr/share/offerloom/fonts.txt \
 && addgroup -g 10001 offerloom \
 && adduser -D -u 10001 -G offerloom -h /tmp offerloom \
 && chown -R 10001:10001 /data /spool /import-spool /run/offerloom
COPY pyproject.toml uv.lock .python-version alembic.ini README.md LICENSE-MIT LICENSE-APACHE NOTICE ./
COPY src/ src/
COPY migrations/ migrations/
COPY deploy/ deploy/
RUN uv sync --frozen --only-group build --no-build \
 && uv sync --frozen --no-dev --group build --no-editable --no-build-isolation \
 && UV_NO_BUILD_ISOLATION=false uv sync --frozen --no-dev --no-group build --no-editable --no-build \
 && uv cache clean \
 && chmod -R a-w /app
COPY --from=web /build/web/dist /app/web/dist
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["python", "-m", "offerloom"]
CMD ["api"]
