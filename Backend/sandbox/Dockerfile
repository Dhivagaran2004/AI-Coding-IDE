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

WORKDIR /workspace
RUN chown -R sandboxuser:sandboxuser /workspace

USER sandboxuser

CMD ["bash"]
