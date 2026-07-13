# Production frontend (spec phase 10 M5): build the Vite bundle, serve it with
# nginx, and reverse-proxy /api + /health + /ws to the app service so the
# browser talks to ONE origin (no CORS, no dev server).
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
