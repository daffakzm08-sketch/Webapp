services:
  flaresolverr:
    image: ghcr.io/flaresolverr/flaresolverr:latest
    container_name: flaresolverr
    environment:
      - LOG_LEVEL=info
      - TZ=Asia/Jakarta
    ports:
      - "127.0.0.1:8191:8191"
    restart: unless-stopped
