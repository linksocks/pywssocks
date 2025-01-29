import click
from .client import _client_cli
from .server import _server_cli

@click.group()
def cli():
    """SOCKS5 over WebSocket proxy tool"""
    pass

cli.add_command(_client_cli, name='client')
cli.add_command(_server_cli, name='server')

if __name__ == '__main__':
    cli()