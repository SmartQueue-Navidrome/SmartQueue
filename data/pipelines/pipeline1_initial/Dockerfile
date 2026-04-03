FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ingest.py feature_engineering.py run_pipeline.sh ./
CMD ["bash", "run_pipeline.sh"]
