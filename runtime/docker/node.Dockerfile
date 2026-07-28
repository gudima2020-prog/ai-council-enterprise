FROM node:22-bookworm-slim
LABEL org.ai-studio.runtime="p2-010"

WORKDIR /opt/ai-studio
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

WORKDIR /workspace
