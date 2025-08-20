[![GitHub Actions Workflow Status](https://img.shields.io/github/actions/workflow/status/linksocks/pywssocks/ci.yml?logo=github&label=Tests)](https://github.com/linksocks/pywssocks/actions) [![Codecov](https://img.shields.io/codecov/c/github/linksocks/pywssocks?logo=codecov&logoColor=white)](https://app.codecov.io/gh/linksocks/pywssocks/tree/main) ![Python Version](https://img.shields.io/badge/python_version-%3E%203.8-blue?logo=python&logoColor=white) [![PyPI - Version](https://img.shields.io/pypi/v/pywssocks?logo=pypi&logoColor=white)](https://pypi.org/project/pywssocks/) [![Docker Pulls](https://img.shields.io/docker/pulls/jackzzs/pywssocks?logo=docker&logoColor=white)](https://hub.docker.com/r/jackzzs/pywssocks) ![License](https://img.shields.io/github/license/linksocks/pywssocks)

# Pywssocks

Pywssocks is a SOCKS proxy implementation over WebSocket protocol.

## Overview

This tool allows you to securely expose SOCKS proxy services under Web Application Firewall (WAF) protection (forward socks), or enable clients to connect and serve as SOCKS proxy servers when they don't have public network access (reverse socks).

![Main Diagram](https://github.com/linksocks/pywssocks/raw/main/images/abstract.svg)

For golang version, please check [linksocks/linksocks](https://github.com/linksocks/linksocks).

> **Note**: **linksocks** is implemented in Go and also ships with a thin Python binding. **Pywssocks**, however, is written entirely in pure Python (no C extensions) and remains **minimally compatible with the latest linksocks protocol and feature set**.

## Features

1. Both client and server modes, supporting command-line usage or library integration.
2. Forward and reverse proxy capabilities.
3. Round-robin load balancing for reverse proxy.
4. SOCKS proxy authentication support.
5. IPv6 over SOCKS5 support.
6. UDP over SOCKS5 support.

## Potential Applications

1. Distributed HTTP backend.
2. Bypassing CAPTCHA using client-side proxies.
3. Secure intranet penetration, using CDN network.

## Usage

### As a tool

Forward Proxy:

```bash
# Server (WebSockets at port 8765, as network connector)
pywssocks server -t example_token

# Client (SOCKS5 at port 1080)
pywssocks client -t example_token -u ws://localhost:8765 -p 1080
```

Reverse Proxy:

```bash
# Server (WebSockets at port 8765, SOCKS at port 1080)
pywssocks server -t example_token -p 1080 -r

# Client (as network connector)
pywssocks client -t example_token -u ws://localhost:8765 -r
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

Pywssocks is also available via docker:

```bash
docker run --rm -it jackzzs/pywssocks --help
```

## Documentation

Visit the documentation: [https://pywssocks.zetx.tech](https://pywssocks.zetx.tech)

## License

Pywssocks is open source under the MIT license.
