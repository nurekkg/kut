# syntax=docker/dockerfile:1
FROM node:24-bookworm-slim

ENV NEXT_TELEMETRY_DISABLED=1

RUN install -d -o node -g node /app
USER node
WORKDIR /app

COPY --chown=node:node package.json package-lock.json ./
RUN --mount=type=cache,target=/home/node/.npm,uid=1000,gid=1000 \
    npm ci --no-audit --no-fund

COPY --chown=node:node . .

EXPOSE 3000
ENV WATCHPACK_POLLING=true
CMD ["npm", "run", "dev", "--", "--webpack", "--hostname", "0.0.0.0"]
