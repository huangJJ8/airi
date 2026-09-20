# AIRI frontend image: build the Vue SPA, serve it with nginx.
#
# The SPA is built with an empty VITE_API_BASE_URL so axios issues same-origin
# requests, and nginx proxies /api and /health to the backend container. That
# avoids CORS entirely in the Compose setup.
#
# NOTE: provided for convenience; runtime behaviour NOT verified in the
# development environment (no Docker available).

# --- build stage ---------------------------------------------------------------
FROM node:22-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Same-origin API calls (see nginx.conf, which proxies to the api service).
ENV VITE_API_BASE_URL=""
RUN npm run build

# --- serve stage ---------------------------------------------------------------
FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
