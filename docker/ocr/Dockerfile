FROM python:3.13-slim-bookworm

LABEL org.ai-studio.ocr-runtime="p3-001.4a" \
      org.ai-studio.ocr-version="5-v1" \
      org.ai-studio.component="documents-ocr"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-rus \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/ocr

COPY requirements.txt /opt/ocr/requirements.txt
RUN python -m pip install \
        --no-cache-dir \
        --requirement /opt/ocr/requirements.txt

COPY runner.py /opt/ocr/runner.py
RUN chmod 0555 /opt/ocr/runner.py

USER 65534:65534

ENTRYPOINT ["python", "-I", "/opt/ocr/runner.py"]
