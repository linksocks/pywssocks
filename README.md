# Pywssocks

Pywssocks is a SOCKS proxy implementation over WebSocket protocol.

## Overview

This tool allows you to securely expose SOCKS proxy services under Web Application Firewall (WAF) protection (forward socks), or enable clients to connect and serve as SOCKS proxy servers when they don't have public network access (reverse socks).

## Features

1. Both client and server modes, supporting command-line usage or library integration.
2. Forward and reverse proxy capabilities.
3. Round-robin load balancing.

## Reverse Socks Proxy

Reverse socks proxy is a method that allows devices, which cannot be directly accessed from the public internet, to expose their internal network environment.

First, start a Pywssocks server on a server that can host websites and be accessed from the public internet, and add a token.

Then, start the Pywssocks client on the internal network server and connect to the designated URL using the token. Since the transmission uses the WebSocket protocol, any WebSocket-supporting web firewall (such as Cloudflare) can be used as an intermediary layer to protect the server's IP from being exposed.

After connecting, the server will expose a configurable or random socks5 port for other services to connect to. All requests will be forwarded through the established bidirectional channel, with the client making the actual connections and sending data.

![Reverse Socks Proxy Diagram](https://github.com/zetxtech/pywssocks/raw/main/images/reverse_proxy_diagram.svg)

# Potential Applications

1. Distributed HTTP backend.
2. Bypassing CAPTCHA using client-side proxies.
3. Secure intranet penetration, using CDN network.
