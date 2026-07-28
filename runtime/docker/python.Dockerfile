FROM python:3.13-slim
LABEL org.ai-studio.runtime="p2-010"

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt /tmp/ai-studio-requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/ai-studio-requirements.txt

WORKDIR /workspace
