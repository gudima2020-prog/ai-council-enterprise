FROM python:3.13-slim-bookworm

LABEL org.ai-studio.browser-script-runtime="arch-browser-runtime-001-script" \
      org.ai-studio.browser-script-runtime-version="mediated-v1" \
      org.ai-studio.component="browser-script-runtime"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp/home \
    XDG_CACHE_HOME=/tmp/cache

WORKDIR /opt/browser-script

COPY script_runner.py /opt/browser-script/script_runner.py
RUN chmod 0555 /opt/browser-script/script_runner.py

USER 65534:65534

ENTRYPOINT ["python", "-I", "/opt/browser-script/script_runner.py"]
