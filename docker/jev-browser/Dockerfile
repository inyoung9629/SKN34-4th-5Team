FROM node:24-bookworm-slim
ARG JEV_SHA=07c2dce0983181d6b2fee3dbdc8b892dae562656
RUN apt-get update && apt-get install -y --no-install-recommends chromium chromium-sandbox python3 python3-venv squid iptables curl ca-certificates util-linux tini && rm -rf /var/lib/apt/lists/*
RUN mkdir /opt/jev && curl -fsSL https://github.com/0x7067/jev-browse/archive/${JEV_SHA}.tar.gz | tar xz --strip-components=1 -C /opt/jev && node -e 'if(JSON.parse(require("fs").readFileSync("/opt/jev/package.json")).version!=="0.16.0")process.exit(1)'
COPY patch-helper.mjs test-helper.mjs /opt/service/
RUN node /opt/service/patch-helper.mjs && cd /opt/jev && npm ci --ignore-scripts && node --import tsx /opt/service/test-helper.mjs && npm run build:bundle && rm -rf node_modules
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir mcp==1.26.0 langsmith==0.14.4
COPY squid.conf /etc/squid/squid.conf
COPY server.py read-body.mjs entrypoint.sh /opt/service/
RUN chmod +x /opt/service/entrypoint.sh && mkdir -p /home/node/.jev-browse && chown -R node:node /home/node
ENV PATH="/opt/venv/bin:${PATH}" HOME=/home/node CHROME_PATH=/usr/bin/chromium JEV_PROVIDER=openrouter TEXT_MODEL=gpt-6-luna ANSWER_REVIEW_MODEL=gpt-6-luna NODE_USE_ENV_PROXY=1 HTTP_PROXY=http://127.0.0.1:3128 HTTPS_PROXY=http://127.0.0.1:3128 NO_PROXY=localhost,127.0.0.1 JEV_CHROME_ARGS="--proxy-server=http://127.0.0.1:3128 --proxy-bypass-list=<-loopback> --disable-quic --disable-dev-shm-usage"
EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini", "--", "/opt/service/entrypoint.sh"]
