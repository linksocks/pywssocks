# Deployment Example with Cloudflare

This example demonstrates how to deploy Pywssocks behind Cloudflare's proxy for enhanced security and privacy.

## Server-side Setup

### 1. Create Docker Compose Configuration

Create a `docker-compose.yml` file:

```yaml
version: '3'
services:
  pywssocks:
    image: jackzzs/pywssocks
    command: server -t your_secret_token
    restart: always
    networks:
      - proxy

  nginx:
    image: nginx:alpine
    volumes:
      - ./nginx.conf:/etc/nginx/conf.d/default.conf:ro
    ports:
      - "80:80"
    depends_on:
      - pywssocks
    networks:
      - proxy

networks:
  proxy:
```

### 2. Configure Nginx

Create `nginx.conf`:

```nginx
server {
    listen 80;
    server_name example.com;

    location / {
        proxy_pass http://pywssocks:8765;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### 3. Cloudflare Configuration

1. Add your domain (example.com) to Cloudflare
2. Point your domain's A record to your server IP (e.g., 1.2.3.4)
3. Enable Cloudflare proxy (orange cloud)
4. Enable Flexible SSL in SSL/TLS settings

### 4. Start the Services

```bash
docker-compose up -d
```

## Client-side Configuration

Connect using the Cloudflare-proxied domain:

```bash
pywssocks client -t your_secret_token -u https://example.com -p 1080
```
