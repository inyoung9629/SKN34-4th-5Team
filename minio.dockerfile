FROM golang:1.24.8-bookworm AS builder

RUN apt-get update && \
    apt-get install -y git make ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /src

RUN git clone https://github.com/minio/minio.git . && \
    git checkout RELEASE.2025-10-15T17-29-55Z

RUN make build


FROM debian:bookworm-slim

RUN apt-get update && \
    apt-get install -y ca-certificates curl && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /src/minio /usr/local/bin/minio

EXPOSE 9000 9001

ENTRYPOINT ["minio"]
CMD ["server", "/data", "--console-address", ":9001"]