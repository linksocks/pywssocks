import click
import socket
import socks
import time
import statistics
from typing import List


@click.group()
def cli():
    """UDP Performance Testing Tool"""
    pass


@cli.command()
@click.option("--port", "-p", default=8888, help="UDP server port")
def server(port: int):
    """Start UDP echo server"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", port))
    click.echo(f"UDP echo server listening on port {port}")

    while True:
        try:
            data, addr = sock.recvfrom(1024)
            click.echo(f"Received from {addr}: {data}")
            sock.sendto(data, addr)
        except KeyboardInterrupt:
            break

    sock.close()
    click.echo("Server stopped")


@cli.command()
@click.option("--proxy-port", "-x", default=1080, help="SOCKS5 proxy port")
@click.option("--server-port", "-p", default=8888, help="UDP server port")
@click.option("--count", "-n", default=100, help="Number of packets to send")
@click.option("--size", "-s", default=64, help="Packet size in bytes")
def test(proxy_port: int, server_port: int, count: int, size: int):
    """Test UDP performance through SOCKS5 proxy"""
    sock = socks.socksocket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.set_proxy(socks.SOCKS5, "127.0.0.1", proxy_port)

    server_addr = ("127.0.0.1", server_port)
    data = b"x" * size

    latencies: List[float] = []
    failed = 0
    start_time = time.time()

    for i in range(count):
        try:
            send_time = time.time()
            sock.sendto(data, server_addr)

            sock.settimeout(1.0)
            try:
                response, _ = sock.recvfrom(size)
                if response == data:
                    latency = (time.time() - send_time) * 1000
                    latencies.append(latency)
                else:
                    failed += 1
            except socket.timeout:
                failed += 1

        except Exception as e:
            click.echo(f"Error: {e.__class__.__name__}: {e}")
            failed += 1

    total_time = time.time() - start_time
    success_count = count - failed

    click.echo("\nTest Results:")
    click.echo(f"Total packets: {count}")
    click.echo(f"Successful packets: {success_count}")
    click.echo(f"Failed packets: {failed}")
    click.echo(f"Success rate: {(success_count/count)*100:.2f}%")

    if latencies:
        click.echo(f"Average latency: {statistics.mean(latencies):.2f}ms")
        click.echo(f"Min latency: {min(latencies):.2f}ms")
        click.echo(f"Max latency: {max(latencies):.2f}ms")
        click.echo(f"Median latency: {statistics.median(latencies):.2f}ms")

    click.echo(f"Total time: {total_time:.2f}s")
    click.echo(f"Throughput: {success_count/total_time:.2f} packets/s")


if __name__ == "__main__":
    cli()
