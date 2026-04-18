FROM python:3.10-slim

RUN apt-get update && apt-get install -y \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    scikit-learn \
    lightgbm \
    optuna \
    mlflow \
    boto3 \
    pandas \
    numpy \
    pyyaml \
    pyarrow \
    joblib

WORKDIR /workspace
