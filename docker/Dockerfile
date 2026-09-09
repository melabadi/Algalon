FROM node:22-alpine

WORKDIR /app

COPY package.json package-lock.json ./
ARG NPM_REGISTRY=https://registry.npmjs.org/
RUN npm config set registry "$NPM_REGISTRY" \
	&& npm ci --omit=dev --ignore-scripts --no-audit --no-fund

COPY dist/src ./dist/src
COPY dist/shared ./dist/shared

CMD ["node", "dist/src/continuous-cli.js"]
