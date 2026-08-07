# --- UI build stage ---
FROM node:22-slim AS ui
WORKDIR /ui
COPY ui/package.json ui/package-lock.json* ./
RUN npm install --no-fund --no-audit
COPY ui/ ./
RUN npm run build

# --- API stage ---
FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ app/
COPY rules/ rules/
COPY scripts/ scripts/
COPY --from=ui /ui/dist ui/dist
EXPOSE 8000
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
