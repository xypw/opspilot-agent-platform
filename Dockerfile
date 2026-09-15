ARG IMAGE_REGISTRY=docker.m.daocloud.io
FROM ${IMAGE_REGISTRY}/library/python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

RUN useradd --create-home --uid 10001 opspilot \
    && mkdir -p /home/opspilot/.cache/fastembed \
    && chown -R opspilot:opspilot /app /home/opspilot/.cache

USER opspilot

EXPOSE 8011

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8011"]
