FROM python:3.11-slim
WORKDIR /app
COPY pipeline2_retrain/requirements.txt .
COPY generator/requirements.txt ./generator-requirements.txt
RUN pip install --no-cache-dir -r requirements.txt -r generator-requirements.txt
COPY utils/s3.py ./utils/s3.py
COPY pipeline2_retrain/retrain.py .
COPY pipeline2_retrain/feedback_checks.py .
COPY generator/generator.py .
CMD ["python", "retrain.py"]
