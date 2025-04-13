from typing import Optional, Tuple
import asyncio
import logging

import click


def parse_socks_proxy(proxy_url: str) -> Tuple[str, Optional[str], Optional[str]]:
    import urllib.parse

    """Parse a SOCKS5 proxy URL and return address, username, and password.
    
    Args:
        proxy_url: URL string in format socks5://[user:pass@]host[:port]
        
    Returns:
        Tuple of (address, username, password)
        
    Raises:
        ValueError: If URL is invalid or scheme is not socks5
    """
    if not proxy_url:
        return "", None, None

    try:
        u = urllib.parse.urlparse(proxy_url)
    except Exception as e:
        raise ValueError(f"Invalid proxy URL: {e}")

    if u.scheme != "socks5":
        raise ValueError(f"Unsupported proxy scheme: {u.scheme}")

    # Get authentication info
    username = None
    password = None
    if u.username:
        username = urllib.parse.unquote(u.username)
    if u.password:
        password = urllib.parse.unquote(u.password)

    # Build address with default port if needed
    host = u.hostname
    port = u.port or 1080
    address = f"{host}:{port}"

    return address, username, password


@click.group()
def cli():
    """SOCKS5 over WebSocket proxy tool"""
    pass


@click.command()
@click.option("--token", "-t", required=True, help="Authentication token")
@click.option(
    "--url", "-u", default="ws://localhost:8765", help="WebSocket server address"
)
@click.option(
    "--reverse", "-r", is_flag=True, default=False, help="Use reverse socks5 proxy"
)
@click.option(
    "--socks-host",
    "-h",
    default="127.0.0.1",
    help="SOCKS5 server listen address for forward proxy",
)
@click.option(
    "--socks-port",
    "-p",
    default=1080,
    help="SOCKS5 server listen port for forward proxy, auto-generate if not provided",
)
@click.option("--socks-username", "-n", help="SOCKS5 authentication username")
@click.option("--socks-password", "-w", help="SOCKS5 authentication password")
@click.option(
    "--socks-no-wait",
    "-i",
    is_flag=True,
    default=False,
    help="Start the SOCKS server immediately",
)
@click.option(
    "--no-reconnect",
    "-R",
    is_flag=True,
    default=False,
    help="Stop when the server disconnects",
)
@click.option(
    "--upstream-proxy",
    "-x",
    default=None,
    help="Upstream SOCKS5 proxy (e.g., socks5://user:pass@127.0.0.1:1080)",
)
@click.option(
    "--connector-token",
    "-c",
    default=None,
    help="Specify connector token for reverse proxy",
)
@click.option("--debug", "-d", is_flag=True, default=False, help="Show debug logs")
def _client_cli(
    token: str,
    url: str,
    reverse: bool,
    socks_host: str,
    socks_port: int,
    socks_username: Optional[str],
    socks_password: Optional[str],
    socks_no_wait: bool,
    no_reconnect: bool,
    upstream_proxy: Optional[str],
    connector_token: Optional[str],
    debug: bool,
):
    """Start SOCKS5 over WebSocket proxy client"""

    from pywssocks.client import WSSocksClient
    from pywssocks.common import init_logging

    async def main(client: WSSocksClient):
        task = await client.wait_ready()
        if connector_token:
            await client.add_connector(connector_token)
        return await task

    init_logging(level=logging.DEBUG if debug else logging.INFO)

    if upstream_proxy:
        upstream_host, upstream_username, upstream_password = parse_socks_proxy(
            upstream_proxy
        )
    else:
        upstream_host = upstream_username = upstream_password = None

    # Start server
    client = WSSocksClient(
        ws_url=url,
        token=token,
        reverse=reverse,
        socks_host=socks_host,
        socks_port=socks_port,
        socks_username=socks_username,
        socks_password=socks_password,
        socks_wait_server=not socks_no_wait,
        reconnect=not no_reconnect,
        upstream_proxy=upstream_host,
        upstream_username=upstream_username,
        upstream_password=upstream_password,
    )

    asyncio.run(main(client))


@click.command()
@click.option(
    "--ws-host", "-H", default="0.0.0.0", help="WebSocket server listen address"
)
@click.option("--ws-port", "-P", default=8765, help="WebSocket server listen port")
@click.option(
    "--token",
    "-t",
    default=None,
    help="Specify auth token, auto-generate if not provided",
)
@click.option(
    "--reverse", "-r", is_flag=True, default=False, help="Use reverse socks5 proxy"
)
@click.option(
    "--socks-host",
    "-h",
    default="127.0.0.1",
    help="SOCKS5 server listen address for reverse proxy",
)
@click.option(
    "--socks-port",
    "-p",
    default=1080,
    help="SOCKS5 server listen port for reverse proxy, auto-generate if not provided",
)
@click.option(
    "--socks-username", "-n", default=None, help="SOCKS5 username for authentication"
)
@click.option(
    "--socks-password", "-w", default=None, help="SOCKS5 password for authentication"
)
@click.option(
    "--socks-nowait",
    "-i",
    is_flag=True,
    default=False,
    help="Start the SOCKS server immediately",
)
@click.option(
    "--upstream-proxy",
    "-x",
    default=None,
    help="Upstream SOCKS5 proxy (e.g., socks5://user:pass@127.0.0.1:1080)",
)
@click.option(
    "--connector-token",
    "-c",
    default=None,
    help="Specify connector token for reverse proxy, auto-generate if not provided",
)
@click.option(
    "--connector-autonomy",
    "-a",
    default=False,
    is_flag=True,
    help="Allow connector clients to manage their own tokens",
)
@click.option("--debug", "-d", is_flag=True, default=False, help="Show debug logs")
def _server_cli(
    ws_host: str,
    ws_port: int,
    token: str,
    reverse: bool,
    socks_host: str,
    socks_port: int,
    socks_username: Optional[str],
    socks_password: Optional[str],
    socks_nowait: bool,
    upstream_proxy: Optional[str],
    connector_token: Optional[str],
    connector_autonomy: bool,
    debug: bool,
):
    """Start SOCKS5 over WebSocket proxy server"""

    from pywssocks.server import WSSocksServer
    from pywssocks.common import init_logging

    init_logging(level=logging.DEBUG if debug else logging.INFO)

    if upstream_proxy:
        upstream_host, upstream_username, upstream_password = parse_socks_proxy(
            upstream_proxy
        )
    else:
        upstream_host = upstream_username = upstream_password = None

    # Create server instance
    server = WSSocksServer(
        ws_host=ws_host,
        ws_port=ws_port,
        socks_host=socks_host,
        socks_wait_client=not socks_nowait,
        upstream_proxy=upstream_host,
        upstream_username=upstream_username,
        upstream_password=upstream_password,
    )

    # Add token based on mode
    if reverse:
        use_token, port = server.add_reverse_token(
            token=token,
            port=socks_port,
            username=socks_username,
            password=socks_password,
            allow_manage_connector=connector_autonomy,
        )
        if not port:
            server._log.error(f"Cannot allocate SOCKS5 port: {socks_host}:{socks_port}")
            return

        if not connector_autonomy:
            use_connector_token = server.add_connector_token(connector_token, use_token)

        server._log.info(f"Configuration:")
        server._log.info(
            f"  Mode: reverse proxy (SOCKS5 on server -> client -> network)"
        )
        server._log.info(f"  Token: {use_token}")
        server._log.info(f"  SOCKS5 port: {port}")
        if socks_username and socks_password:
            server._log.info(f"  SOCKS5 auth: enabled (username: {socks_username})")
            server._log.info(f"  SOCKS5 port: {socks_port}")
        if not connector_autonomy:
            server._log.info(f"  Connector Token: {use_connector_token}")
        if socks_username or socks_password:
            server._log.info(f"  SOCKS5 username: {socks_username}")
        if connector_autonomy:
            server._log.info(f"  Connector autonomy: enabled")
    else:
        use_token = server.add_forward_token(token)
        server._log.info(f"Configuration:")
        server._log.info(
            f"  Mode: forward proxy (SOCKS5 on client -> server -> network)"
        )
        server._log.info(f"  Token: {use_token}")

    # Start server
    asyncio.run(server.serve())


cli.add_command(_client_cli, name="client")
cli.add_command(_server_cli, name="server")

if __name__ == "__main__":
    cli()
