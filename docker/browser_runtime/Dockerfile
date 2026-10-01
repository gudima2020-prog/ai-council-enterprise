FROM mcr.microsoft.com/playwright/python:v1.55.0-noble

LABEL org.ai-studio.browser-runtime="arch-browser-runtime-001" \
      org.ai-studio.browser-runtime-version="fixture-v1" \
      org.ai-studio.component="browser-runtime"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/tmp/home \
    XDG_CACHE_HOME=/tmp/cache

WORKDIR /opt/browser-runtime

COPY requirements.txt /opt/browser-runtime/requirements.txt
RUN python -m pip install \
        --no-cache-dir \
        --requirement /opt/browser-runtime/requirements.txt

COPY runner.py /opt/browser-runtime/runner.py
COPY worker.py /opt/browser-runtime/worker.py
RUN chmod 0555 /opt/browser-runtime/runner.py /opt/browser-runtime/worker.py

USER 65534:65534

ENTRYPOINT ["python", "-I", "/opt/browser-runtime/runner.py"]
