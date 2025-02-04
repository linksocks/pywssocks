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