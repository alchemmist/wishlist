FROM node:24-bookworm-slim AS optimizer
WORKDIR /opt/image-optimizer
COPY services/image-optimizer/package*.json ./
RUN npm ci --omit=dev --ignore-scripts=false
COPY services/image-optimizer/server.cjs ./

FROM python:3.13-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends libstdc++6 && rm -rf /var/lib/apt/lists/*
COPY --from=optimizer /usr/local/bin/node /usr/local/bin/node
COPY --from=optimizer /opt/image-optimizer /opt/image-optimizer
RUN node --version && node -e "require('/opt/image-optimizer/node_modules/sharp')"
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
COPY static static
ENV DATA_DIR=/data
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
