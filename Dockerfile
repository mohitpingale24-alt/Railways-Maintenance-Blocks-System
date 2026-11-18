FROM node:22-bookworm-slim
ENV NODE_ENV=development PYTHONUNBUFFERED=1 CI=1 WRANGLER_SEND_METRICS=false WRANGLER_WRITE_LOGS=false
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv libgomp1 ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY planner/requirements.txt planner/requirements.txt
RUN python3 -m venv .venv && .venv/bin/pip install --no-cache-dir -r planner/requirements.txt
COPY . .
RUN mkdir -p .railblock && gzip -dc deploy/render/simulation.sqlite.gz > .railblock/simulation.sqlite && cp deploy/render/models.joblib deploy/render/model-comparison.json .railblock/ && .venv/bin/python scripts/check-render-artifacts.py && npm run build && cp -R drizzle dist/server/drizzle
EXPOSE 10000
CMD ["node", "scripts/start-render.mjs"]
