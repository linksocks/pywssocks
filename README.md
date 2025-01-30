# Pywssocks

Pywssocks is a SOCKS proxy implementation over WebSocket protocol.

## Overview

This tool allows you to securely expose SOCKS proxy services under Web Application Firewall (WAF) protection (forward socks), or enable clients to connect and serve as SOCKS proxy servers when they don't have public network access (reverse socks).

## Features

1. Both client and server modes, supporting command-line usage or library integration.
2. Forward and reverse proxy capabilities.
3. Round-robin load balancing.

## Usage

### As a tool

Forward Proxy:

```bash
# server
pywssocks server -t <token>

# client
pywssocks client -t <token> -u ws://localhost:8765 -p 1080
```

Reverse Proxy:

```bash
# server
pywssocks server -t <token> -p 1080 -r

# client
pywssocks client -t <token> -u ws://localhost:8765 -r
```

### As a library

Forward Proxy:

```python
import asyncio
from pywssocks import WSSocksServer, WSSocksClient

# Server
server = WSSocksServer(
    ws_host="0.0.0.0",
    ws_port=8765,
)
token = server.add_forward_token()
print(f"Token: {token}")
asyncio.run(server.start())

# Client
client = WSSocksClient(
    token="<token>",
    ws_url="ws://localhost:8765",
    socks_host="127.0.0.1",
    socks_port=1080,
)
asyncio.run(client.start())
```

Reverse Proxy:

```python
import asyncio
from pywssocks import WSSocksServer, WSSocksClient

# Server
server = WSSocksServer(
    ws_host="0.0.0.0",
    ws_port=8765,
    socks_host="127.0.0.1",
    socks_port_pool=range(1024, 10240),
)
token, port = server.add_reverse_token()
print(f"Token: {token}\nPort: {port}")
asyncio.run(server.start())

# Client
client = WSSocksClient(
    token="<token>",
    ws_url="ws://localhost:8765",
    reverse=True,
)
asyncio.run(client.start())
```

## Installation

Pywssocks requires `python >= 3.8`, and can be installed by:

```bash
pip install pywssocks
```

## Principles

### Forward Socks Proxy

Forward SOCKS proxy is a method that allows a server to expose its internal network environment while protecting its IP address from being exposed.

First, start a Pywssocks server on a server that can host websites and be accessed from the public internet, and add a token.

Then, on the device that needs to access the server's internal network environment, start the Pywssocks client and connect to the designated URL using the token. Since the transmission uses the WebSocket protocol, any WebSocket-supporting web firewall (such as Cloudflare) can be used as an intermediary layer to protect the server's IP address from being exposed.

After connecting, the client will open a configurable or random SOCKS5 port for other services to connect to. All requests will be forwarded through the established bidirectional channel, with the server performing the actual connections and sending data.

![Forward Socks Proxy Diagram](https://github.com/zetxtech/pywssocks/raw/main/images/forward_proxy_diagram.svg)

### Reverse Socks Proxy

Reverse socks proxy is a method that allows devices, which cannot be directly accessed from the public internet, to expose their internal network environment.

First, start a Pywssocks server on a server that can host websites and be accessed from the public internet, and add a token.

Then, start a Pywssocks client on the internal network server and connect to the designated URL using the token. Since the transmission uses the WebSocket protocol, any WebSocket-supporting web firewall (such as Cloudflare) can be used as an intermediary layer to protect the server's IP from being exposed.

After connecting, the server will expose a configurable or random socks5 port for other services to connect to. All requests will be forwarded through the established bidirectional channel, with the client performing the actual connections and sending data.

![Reverse Socks Proxy Diagram](https://github.com/zetxtech/pywssocks/raw/main/images/reverse_proxy_diagram.svg)

## Potential Applications

1. Distributed HTTP backend.
2. Bypassing CAPTCHA using client-side proxies.
3. Secure intranet penetration, using CDN network.
