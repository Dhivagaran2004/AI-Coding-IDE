FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        build-essential \
        ca-certificates \
        git \
        nodejs \
        npm \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash sandboxuser

RUN python -m pip install --no-cache-dir pytest==9.2.0 fastapi==0.138.1 uvicorn==0.49.0

WORKDIR /workspace
RUN chown -R sandboxuser:sandboxuser /workspace

USER sandboxuser

CMD ["bash"]
