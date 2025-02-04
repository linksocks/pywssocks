# Pywssocks Commandline Usage

Pywssocks is a versatile SOCKS proxy implementation over the WebSocket protocol, supporting both forward and reverse proxy configurations. This guide provides detailed instructions on how to use Pywssocks via the command line.

## Basic Commands

Pywssocks provides two primary commands: `server` and `client`.

### Server

Starts the Pywssocks server which listens for incoming WebSocket connections and manages SOCKS proxy services.

### Client

Starts the Pywssocks client which connects to the server and provides SOCKS proxy functionality to the local machine.

## Options

Both `server` and `client` commands support a variety of options to customize their behavior.

### Common Options

- `--token`, `-t`: Authentication token for securing the connection.
- `--debug`, `-d`: Enable debug logging for more detailed output.

### Server-Specific Options

- `--ws-host`, `-H`: WebSocket server listen address. Defaults to `0.0.0.0`.
- `--ws-port`, `-P`: WebSocket server listen port. Defaults to `8765`.
- `--reverse`, `-r`: Use reverse SOCKS5 proxy mode.
- `--socks-host`, `-h`: SOCKS5 server listen address. Defaults to `127.0.0.1`.
- `--socks-port`, `-p`: SOCKS5 server listen port. Defaults to `1080`.
- `--socks-username`, `-n`: SOCKS5 username for authentication.
- `--socks-password`, `-w`: SOCKS5 password for authentication.
- `--socks-nowait`, `-i`: Start the SOCKS server immediately without waiting.
- `--no-reconnect`, `-R`: Disable automatic reconnection when the server disconnects.

### Client-Specific Options

- `--url`, `-u`: WebSocket server address. Defaults to `ws://localhost:8765`.
- `--reverse`, `-r`: Use reverse SOCKS5 proxy mode.
- `--socks-host`, `-h`: SOCKS5 server listen address for forward proxy. Defaults to `127.0.0.1`.
- `--socks-port`, `-p`: SOCKS5 server listen port for forward proxy. Defaults to `1080`.
- `--socks-username`, `-n`: SOCKS5 authentication username.
- `--socks-password`, `-w`: SOCKS5 authentication password.
- `--socks-no-wait`, `-i`: Start the SOCKS server immediately without waiting.
- `--no-reconnect`, `-R`: Disable automatic reconnection when the server disconnects.

## Examples

### Forward Proxy

**Server Side:**

Start the Pywssocks server with a specific token.

```bash
pywssocks server -t example_token
```

**Client Side:**

Connect the Pywssocks client to the server and provide SOCKS5 proxy on port `1080`.

```bash
pywssocks client -t example_token -u ws://localhost:8765 -p 1080
```

### Reverse Proxy

**Server Side:**

Start the Pywssocks server in reverse mode, exposing SOCKS5 proxy on port `1080`.

```bash
pywssocks server -t example_token -p 1080 -r
```

**Client Side:**

Connect the Pywssocks client in reverse mode to act as a network connector.

```bash
pywssocks client -t example_token -u ws://localhost:8765 -r
```

## Help

To display help for specific commands, use the `--help` flag:

```bash
pywssocks server --help
pywssocks client --help
```

This will provide a comprehensive list of available options and their descriptions.