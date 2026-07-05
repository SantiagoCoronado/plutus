FROM node:22-slim

WORKDIR /app
ENV NODE_ENV=production

COPY package.json ./
RUN npm install --omit=dev && npm cache clean --force

COPY server.mjs tools.mjs ./

# the agent SDK spawns the vendored Claude CLI, which writes session state under $HOME
USER node
ENV HOME=/home/node

EXPOSE 8787
CMD ["node", "server.mjs"]
