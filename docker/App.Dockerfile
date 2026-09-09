FROM node:22-alpine AS web-build

WORKDIR /web
COPY web/package.json web/package-lock.json ./
ARG NPM_REGISTRY=https://registry.npmjs.org/
RUN npm config set registry "$NPM_REGISTRY" \
    && npm ci --ignore-scripts --no-audit --no-fund
COPY shared/ /shared/
COPY config/value-model.example.json /config/value-model.example.json
COPY web/ ./
RUN npm run build

FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --no-cache-dir --index-url "$PIP_INDEX_URL" -r requirements.txt
COPY backend/app ./app
COPY --from=web-build /web/dist ./static

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]