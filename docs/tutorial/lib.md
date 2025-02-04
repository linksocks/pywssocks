# Pywssocks Library Usage

Pywssocks can be integrated into your Python applications as a library, providing programmatic control over SOCKS proxy services. This guide demonstrates how to use Pywssocks's core classes in your code.

## Core Classes

### WSSocksServer

The server class that handles WebSocket connections and manages SOCKS proxy services.

### WSSocksClient

The client class that connects to a WSSocksServer and provides SOCKS proxy functionality.

## Basic Usage

### Forward Proxy

In forward proxy mode, the server acts as a network connector while the client provides SOCKS proxy services locally.

**Server Side:**

```python
import asyncio
from pywssocks import WSSocksServer

# Create and configure the server
server = WSSocksServer(
    ws_host="0.0.0.0",  # WebSocket listen address
    ws_port=8765,       # WebSocket listen port
)

# Add authentication token
token = server.add_forward_token()
print(f"Token: {token}")

# Start the server
asyncio.run(server.start())
```

**Client Side:**

```python
import asyncio
from pywssocks import WSSocksClient

# Create and configure the client
client = WSSocksClient(
    token="<token>",              # Authentication token from server
    ws_url="ws://localhost:8765", # Server WebSocket address
    socks_host="127.0.0.1",      # Local SOCKS listen address
    socks_port=1080,             # Local SOCKS listen port
)

# Start the client
asyncio.run(client.start())
```

!!! note

    A Pywssocks server can work as both a reverse proxy server and a forward proxy server (by adding different tokens), while a client can only choose one of them.

### Reverse Proxy

In reverse proxy mode, the server provides SOCKS services while clients act as network connectors.

**Server Side:**

```python
import asyncio
from pywssocks import WSSocksServer

# Create and configure the server
server = WSSocksServer(
    ws_host="0.0.0.0",                    # WebSocket listen address
    ws_port=8765,                         # WebSocket listen port
    socks_host="127.0.0.1",              # SOCKS listen address
    socks_port_pool=range(1024, 10240),  # SOCKS port pool
)

# Add authentication token and get assigned port
token, port = server.add_reverse_token()
print(f"Token: {token}\nPort: {port}")

# Start the server
asyncio.run(server.start())
```

**Client Side:**

```python
import asyncio
from pywssocks import WSSocksClient

# Create and configure the client
client = WSSocksClient(
    token="<token>",              # Authentication token from server
    ws_url="ws://localhost:8765", # Server WebSocket address
    reverse=True,                 # Enable reverse proxy mode
)

# Start the client
asyncio.run(client.start())
```

## Advanced Features

### Token Management for Server

The server provides methods to manage authentication tokens:

```python
# Add forward proxy token (optionally with custom token string)
forward_token = server.add_forward_token("<custom_token>")

# Add reverse proxy token with optional custom port and token
reverse_token, port = server.add_reverse_token(
    token="<custom_token>",  # Optional custom token
    port=8080               # Optional specific port to use
)

# Remove token (works for both forward and reverse tokens)
server.remove_token("<token>")
```

When using `add_reverse_token()`:

  - If no port is specified, one will be automatically allocated from the `socks_port_pool`.
  - If a specific port is requested but unavailable, the method returns `(token, None)`.
  - The port pool is defined during server initialization and can be either:
    - A range object: `range(1024, 10240)`
    - An iterable of specific ports: `[8080, 8081, 8082]`

### Logging Configuration

You can configure logging for detailed debugging:

```python
import logging

# Configure logging level
logging.basicConfig(level=logging.DEBUG)

# Or assign specific logger to client
logger = logging.getLogger("custom_logger")
client = WSSocksClient(
    # ... other parameters ...
    logger=logger
)
```

### Start Methods

The server provides two methods to start:

```python
# Method 1: Start and wait indefinitely
await server.serve()

# Method 2: Start with timeout and get task
server_task = await server.wait_ready(timeout=5)
```

The client provides two methods to start:

```python
# Method 1: Start and wait indefinitely
await client.connect()

# Method 2: Start with timeout and get task
client_task = await client.wait_ready(timeout=5)
```

Key differences:

- `serve()` / `connect()`: Blocks indefinitely until the server/client stops
- `wait_ready()`: Returns a task once the server/client is ready to accept connections
  - Accepts optional timeout parameter
  - Useful for testing or when you need to perform actions after startup

### SOCKS Server Start Behavior

Both server and client provide options to control startup behavior:

**Server Side:**
```python
server = WSSocksServer(
    # ... other parameters ...
    socks_wait_client=True  # Wait for client before starting SOCKS server
)
```

- `socks_wait_client=True`: SOCKS server starts only after client connects
- `socks_wait_client=False`: SOCKS server starts immediately when token is added

**Client Side:**
```python
client = WSSocksClient(
    # ... other parameters ...
    socks_wait_server=True  # Wait for server before starting SOCKS server
)
```

- `socks_wait_server=True`: SOCKS server starts only after connecting to server
- `socks_wait_server=False`: SOCKS server starts immediately when client starts
