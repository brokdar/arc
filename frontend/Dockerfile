# Stage 1: install dependencies (cached unless package files change)
FROM oven/bun:1-alpine AS deps
WORKDIR /app

RUN apk add --no-cache libc6-compat

COPY package.json bun.lock ./
RUN --mount=type=cache,target=/root/.bun \
    bun install --frozen-lockfile

# Stage 2: build
FROM oven/bun:1-alpine AS builder
WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# NEXT_PUBLIC_* values are baked into the client bundle at build time
ARG NEXT_PUBLIC_API_BASE_URL
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}
ENV NEXT_TELEMETRY_DISABLED=1

RUN bun run build

# Stage 3: production runtime (standalone output, non-root)
FROM node:24-alpine AS runner
WORKDIR /app

RUN apk add --no-cache curl

RUN addgroup -g 1001 -S nodejs && \
    adduser -S nextjs -u 1001 -G nodejs

COPY --from=builder --chown=nextjs:nodejs /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

USER nextjs

EXPOSE 3000

CMD ["node", "server.js"]
