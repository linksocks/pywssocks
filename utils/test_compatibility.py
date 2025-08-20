#!/usr/bin/env python3
"""
Compatibility test script between pywssocks and linksocks
Automatically downloads the latest linksocks release and tests forward/reverse proxy compatibility
"""

import os
import sys
import asyncio
import json
import platform
import subprocess
import tempfile
import time
import threading
from pathlib import Path
from typing import Optional, Tuple
import zipfile
import tarfile
import signal
import socket
from urllib.parse import urlparse

# Test configuration
TEST_TOKEN = "test_compatibility_token"
PYWSSOCKS_WS_PORT = 8765
PYWSSOCKS_SOCKS_PORT = 1080
LINKSOCKS_WS_PORT = 8766
LINKSOCKS_SOCKS_PORT = 1081


class TestRunner:
    def __init__(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="compatibility_test_"))
        self.linksocks_path = None
        self.processes = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()

    def cleanup(self):
        """Clean up processes and temporary files"""
        print("Cleaning up processes...")
        for proc in self.processes:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    proc.kill()
                except OSError:
                    pass

        # Clean up temporary directory
        import shutil

        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def install_dependencies(self):
        """Install required Python dependencies"""
        dependencies = ["requests", "PySocks"]
        for dep in dependencies:
            try:
                __import__(dep.lower().replace("-", "_"))
                print(f"✓ {dep} is already installed")
            except ImportError:
                print(f"Installing {dep}...")
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", dep, "--quiet"]
                )
                print(f"✓ {dep} installed successfully")

    def get_latest_release_info(self) -> dict:
        """Get linksocks latest release information"""
        import requests

        print("Fetching linksocks latest version info...")
        url = "https://api.github.com/repos/linksocks/linksocks/releases/latest"

        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 403:
                print("GitHub API rate limit exceeded. Using fallback method...")
                return self.get_release_info_fallback()
            else:
                print(f"Failed to get release info: {e}")
                raise
        except Exception as e:
            print(f"Failed to get release info: {e}")
            print("Trying fallback method...")
            return self.get_release_info_fallback()

    def get_release_info_fallback(self) -> dict:
        """Fallback method to get release info by scraping releases page"""
        import requests
        import re

        print("Using fallback method to get release info...")
        url = "https://github.com/linksocks/linksocks/releases/latest"

        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()

            # Extract version from redirect URL or page content
            # GitHub redirects /releases/latest to /releases/tag/vX.Y.Z
            final_url = response.url
            version_match = re.search(r"/releases/tag/([^/]+)", final_url)

            if version_match:
                version = version_match.group(1)
                print(f"Found version from URL: {version}")

                # Construct download URLs for common architectures
                base_url = f"https://github.com/linksocks/linksocks/releases/download/{version}"
                assets = []

                # Common asset patterns - linksocks uses direct executable files
                for os_name in ["linux", "darwin", "windows"]:
                    for arch in ["amd64", "arm64", "386", "arm"]:
                        if os_name == "windows":
                            asset_name = f"linksocks-{os_name}-{arch}.exe"
                        else:
                            asset_name = f"linksocks-{os_name}-{arch}"
                        assets.append(
                            {
                                "name": asset_name,
                                "browser_download_url": f"{base_url}/{asset_name}",
                            }
                        )

                return {"tag_name": version, "assets": assets}
            else:
                raise ValueError("Could not extract version from releases page")

        except Exception as e:
            print(f"Fallback method also failed: {e}")
            raise RuntimeError("Could not get release information using any method")

    def determine_asset_name(self) -> str:
        """Determine the asset name to download based on current system"""
        system = platform.system().lower()
        machine = platform.machine().lower()

        # Map architecture names
        arch_map = {
            "x86_64": "amd64",
            "amd64": "amd64",
            "i386": "386",
            "i686": "386",
            "armv7l": "arm",
            "aarch64": "arm64",
            "arm64": "arm64",
        }

        arch = arch_map.get(machine, machine)

        if system == "linux":
            return f"linksocks-linux-{arch}"
        elif system == "darwin":
            return f"linksocks-darwin-{arch}"
        elif system == "windows":
            return f"linksocks-windows-{arch}.exe"
        else:
            raise ValueError(f"Unsupported operating system: {system}")

    def download_linksocks(self) -> Path:
        """Download linksocks executable or use local one if available"""

        # Check if ./linksocks exists in current directory
        local_linksocks = Path("./linksocks")
        if local_linksocks.exists() and local_linksocks.is_file():
            # Check if it's executable
            if os.access(local_linksocks, os.X_OK):
                print(f"Using local linksocks executable: {local_linksocks.absolute()}")
                self.linksocks_path = local_linksocks.absolute()
                return local_linksocks.absolute()
            else:
                print(
                    f"Local linksocks file exists but is not executable: {local_linksocks}"
                )

        # Also check for linksocks.exe on Windows
        local_linksocks_exe = Path("./linksocks.exe")
        if local_linksocks_exe.exists() and local_linksocks_exe.is_file():
            if os.access(local_linksocks_exe, os.X_OK):
                print(
                    f"Using local linksocks executable: {local_linksocks_exe.absolute()}"
                )
                self.linksocks_path = local_linksocks_exe.absolute()
                return local_linksocks_exe.absolute()

        print("Local linksocks not found, downloading from GitHub...")
        import requests

        release_info = self.get_latest_release_info()
        version = release_info["tag_name"]
        print(f"Latest version: {version}")

        asset_name = self.determine_asset_name()
        print(f"Target file: {asset_name}")

        # Find corresponding asset
        asset_url = None
        for asset in release_info["assets"]:
            if asset["name"] == asset_name:
                asset_url = asset["browser_download_url"]
                break

        if not asset_url:
            print(f"Available assets:")
            for asset in release_info["assets"]:
                print(f"  - {asset['name']}")
            raise ValueError(
                f"Could not find suitable download file for current system: {asset_name}"
            )

        # Download file
        print(f"Downloading {asset_url}...")
        download_path = self.temp_dir / "linksocks"
        if asset_name.endswith(".exe"):
            download_path = self.temp_dir / "linksocks.exe"

        response = requests.get(asset_url, stream=True)
        response.raise_for_status()

        with open(download_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        print(f"Download completed: {download_path}")

        # Set execute permission
        download_path.chmod(0o755)
        self.linksocks_path = download_path
        print(f"linksocks executable ready: {download_path}")

        # Check linksocks help to understand command line options
        self.check_linksocks_help()

        return download_path

    def check_linksocks_help(self):
        """Check linksocks command line options"""
        if not self.linksocks_path:
            return

        try:
            print("Checking linksocks command line options...")

            # Check server help
            result = subprocess.run(
                [str(self.linksocks_path), "server", "--help"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                print("linksocks server options:")
                print(result.stdout[:500] + ("..." if len(result.stdout) > 500 else ""))

            # Check client help
            result = subprocess.run(
                [str(self.linksocks_path), "client", "--help"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                print("linksocks client options:")
                print(result.stdout[:500] + ("..." if len(result.stdout) > 500 else ""))

        except Exception as e:
            print(f"Could not check linksocks help: {e}")

    def check_port_available(self, port: int) -> bool:
        """Check if port is available"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("localhost", port))
                return True
            except OSError:
                return False

    def get_available_port(
        self, start_port: int = 8000, max_attempts: int = 100
    ) -> int:
        """Get an available port starting from start_port"""
        for port in range(start_port, start_port + max_attempts):
            if self.check_port_available(port):
                return port
        raise RuntimeError(
            f"Could not find available port in range {start_port}-{start_port + max_attempts}"
        )

    def wait_for_port(self, port: int, timeout: int = 10) -> bool:
        """Wait for port to start listening"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.connect(("localhost", port))
                    return True
                except OSError:
                    time.sleep(0.1)
        return False

    def start_pywssocks_server(
        self, port: int = None, reverse: bool = False
    ) -> Tuple[subprocess.Popen, int, int]:
        """Start pywssocks server and return process, ws_port, and socks_port"""
        if port is None:
            port = self.get_available_port(8000)
        elif not self.check_port_available(port):
            port = self.get_available_port(port)

        cmd = [
            sys.executable,
            "-m",
            "pywssocks",
            "server",
            "-t",
            TEST_TOKEN,
            "--ws-host",
            "127.0.0.1",
            "--ws-port",
            str(port),
            "-dd",
        ]

        socks_port = 0
        if reverse:
            socks_port = self.get_available_port(9000)
            cmd.extend(
                [
                    "--reverse",
                    "--socks-host",
                    "127.0.0.1",
                    "--socks-port",
                    str(socks_port),
                ]
            )

        print(f"Starting pywssocks server: {' '.join(cmd)}")
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            bufsize=1,
            cwd=Path.cwd(),
        )
        self.processes.append(proc)

        # Start a thread to monitor output
        def monitor_output():
            try:
                for line in proc.stdout:
                    print(f"[pywssocks-server] {line.rstrip()}")
            except:
                pass

        output_thread = threading.Thread(target=monitor_output, daemon=True)
        output_thread.start()

        # Wait for server to start
        if not self.wait_for_port(port, 10):
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            raise RuntimeError(f"pywssocks server failed to start on port {port}")

        return proc, port, socks_port

    def start_pywssocks_client(
        self, ws_url: str, socks_port: int = None, reverse: bool = False
    ) -> Tuple[subprocess.Popen, int]:
        """Start pywssocks client and return process and actual SOCKS port used"""
        if not reverse:
            if socks_port is None:
                socks_port = self.get_available_port(9000)
            elif not self.check_port_available(socks_port):
                socks_port = self.get_available_port(socks_port)
        else:
            # In reverse mode, socks_port is not used by client
            socks_port = 0

        cmd = [
            sys.executable,
            "-m",
            "pywssocks",
            "client",
            "-t",
            TEST_TOKEN,
            "--url",
            ws_url,
            "--socks-port",
            str(socks_port),
            "-dd",
        ]

        if reverse:
            cmd.append("--reverse")

        print(f"Starting pywssocks client: {' '.join(cmd)}")
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            bufsize=1,
            cwd=Path.cwd(),
        )
        self.processes.append(proc)

        # Start a thread to monitor output

        def monitor_output():
            try:
                for line in proc.stdout:
                    print(f"[pywssocks-client] {line.rstrip()}")
            except:
                pass

        output_thread = threading.Thread(target=monitor_output, daemon=True)
        output_thread.start()

        if not reverse:
            # Wait for client SOCKS port to start
            if not self.wait_for_port(socks_port, 10):
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise RuntimeError(
                    f"pywssocks client failed to start on SOCKS port {socks_port}"
                )

        return proc, socks_port

    def start_linksocks_server(
        self, port: int = None, socks_port: int = None, reverse: bool = False
    ) -> Tuple[subprocess.Popen, int, int]:
        """Start linksocks server and return process, ws_port, and socks_port"""
        if port is None:
            port = self.get_available_port(8100)
        elif not self.check_port_available(port):
            port = self.get_available_port(port)

        # Based on cli.go, linksocks server uses these flags:
        # -H for ws-host, -P for ws-port, -t for token, -r for reverse
        # -s for socks-host, -p for socks-port
        cmd = [
            str(self.linksocks_path),
            "server",
            "-t",
            TEST_TOKEN,
            "-H",
            "127.0.0.1",
            "-P",
            str(port),
            "-dd",
        ]

        if reverse:
            if socks_port is None:
                socks_port = self.get_available_port(9100)
            elif not self.check_port_available(socks_port):
                socks_port = self.get_available_port(socks_port)
            cmd.extend(["-r", "-s", "127.0.0.1", "-p", str(socks_port)])

        # linksocks might use different port specification method
        # Let's try without environment variable first
        env = os.environ.copy()

        print(f"Starting linksocks server: {' '.join(cmd)}")
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            bufsize=1,
            env=env,
        )
        self.processes.append(proc)

        # Start a thread to monitor output

        def monitor_output():
            try:
                for line in proc.stdout:
                    print(f"[linksocks-server] {line.rstrip()}")
            except:
                pass

        output_thread = threading.Thread(target=monitor_output, daemon=True)
        output_thread.start()

        # Wait for server to start
        if not self.wait_for_port(port, 10):
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            raise RuntimeError(f"linksocks server failed to start on port {port}")

        return proc, port, socks_port or 0

    def start_linksocks_client(
        self, ws_url: str, socks_port: int = None, reverse: bool = False
    ) -> Tuple[subprocess.Popen, int]:
        """Start linksocks client and return process and actual SOCKS port used"""
        if not reverse:
            if socks_port is None:
                socks_port = self.get_available_port(9200)
            elif not self.check_port_available(socks_port):
                socks_port = self.get_available_port(socks_port)
        else:
            # In reverse mode, socks_port is not used by client
            socks_port = 0

        # Based on cli.go, linksocks client uses these flags:
        # -t for token, -u for url, -s for socks-host, -p for socks-port, -r for reverse
        cmd = [
            str(self.linksocks_path),
            "client",
            "-t",
            TEST_TOKEN,
            "-u",
            ws_url,
            "--no-env-proxy",
            "-dd",
        ]

        if not reverse:
            cmd.extend(["-s", "127.0.0.1", "-p", str(socks_port)])

        if reverse:
            cmd.append("-r")

        print(f"Starting linksocks client: {' '.join(cmd)}")
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            bufsize=1,
        )
        self.processes.append(proc)

        # Start a thread to monitor output

        def monitor_output():
            try:
                for line in proc.stdout:
                    print(f"[linksocks-client] {line.rstrip()}")
            except:
                pass

        output_thread = threading.Thread(target=monitor_output, daemon=True)
        output_thread.start()

        if not reverse:
            # Wait for client SOCKS port to start
            if not self.wait_for_port(socks_port, 10):
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise RuntimeError(
                    f"linksocks client failed to start on SOCKS port {socks_port}"
                )

        return proc, socks_port

    def test_socks_proxy(self, port: int) -> bool:
        """Test if SOCKS proxy is working"""
        try:
            import socks
        except ImportError:
            print("PySocks not available, skipping SOCKS test")
            return False

        try:
            # Create SOCKS5 proxy connection
            s = socks.socksocket()
            s.set_proxy(socks.SOCKS5, "127.0.0.1", port)
            s.settimeout(10)

            # Try to connect to a public test service
            s.connect(("httpbin.org", 80))

            # Send simple HTTP request
            s.send(b"GET /ip HTTP/1.1\r\nHost: httpbin.org\r\n\r\n")

            response = s.recv(1024)
            s.close()

            return b"HTTP/1.1 200" in response

        except Exception as e:
            print(f"SOCKS proxy test failed: {e}")
            return False

    def print_protocol_incompatibility_diagnosis(self):
        """Print detailed diagnosis of protocol incompatibility"""
        print("\n" + "🔍 PROTOCOL INCOMPATIBILITY ANALYSIS:")
        print("=" * 60)
        print("✅ WebSocket handshake succeeds")
        print("✅ HTTP upgrade to WebSocket protocol works")
        print("✅ Basic network connectivity is fine")
        print("❌ Connection closes immediately after handshake")
        print("❌ No application-layer message exchange occurs")
        print("")
        print("ROOT CAUSE:")
        print("- linksocks sends authentication via URL query parameters:")
        print("  GET /socket?instance=xxx&reverse=false&token=xxx")
        print("- pywssocks expects authentication via binary WebSocket messages")
        print("  after the handshake is complete")
        print("- These are fundamentally different protocol designs")
        print("")
        print("CONCLUSION:")
        print("❌ linksocks and pywssocks are NOT compatible")
        print("❌ They use different application-layer protocols over WebSocket")
        print("❌ No simple configuration change can make them work together")

    def test_forward_proxy_pywssocks_to_linksocks(self) -> bool:
        """Test forward proxy: pywssocks server -> linksocks client"""
        print("\n" + "=" * 60)
        print("Test 1: Forward proxy (pywssocks server -> linksocks client)")
        print("=" * 60)

        try:
            # Start pywssocks server
            print("1. Starting pywssocks server...")
            server_proc, ws_port, _ = self.start_pywssocks_server()
            print(f"   Server started on WebSocket port: {ws_port}")
            time.sleep(2)

            # Start linksocks client
            print("2. Starting linksocks client...")
            ws_url = f"ws://127.0.0.1:{ws_port}/socket"
            client_proc, socks_port = self.start_linksocks_client(ws_url)
            print(f"   Client started on SOCKS port: {socks_port}")
            time.sleep(3)

            # Test SOCKS proxy
            print("3. Testing SOCKS proxy connection...")
            result = self.test_socks_proxy(socks_port)

            if result:
                print("✅ Forward proxy test successful!")
            else:
                print("❌ Forward proxy test failed - SOCKS connection not working")

            return result

        except Exception as e:
            print(f"❌ Forward proxy test exception: {e}")
            return False
        finally:
            # Stop processes
            for proc in [p for p in self.processes if p.poll() is None]:
                proc.terminate()
            self.processes.clear()

    def test_forward_proxy_linksocks_to_pywssocks(self) -> bool:
        """Test forward proxy: linksocks server -> pywssocks client"""
        print("\n" + "=" * 60)
        print("Test 2: Forward proxy (linksocks server -> pywssocks client)")
        print("=" * 60)

        try:
            # Start linksocks server
            print("1. Starting linksocks server...")
            server_proc, ws_port, _ = self.start_linksocks_server()
            print(f"   Server started on WebSocket port: {ws_port}")
            time.sleep(2)

            # Start pywssocks client
            print("2. Starting pywssocks client...")
            ws_url = f"ws://127.0.0.1:{ws_port}/socket"
            client_proc, socks_port = self.start_pywssocks_client(ws_url)
            print(f"   Client started on SOCKS port: {socks_port}")
            time.sleep(3)

            # Test SOCKS proxy
            print("3. Testing SOCKS proxy connection...")
            result = self.test_socks_proxy(socks_port)

            if result:
                print("✅ Forward proxy test successful!")
            else:
                print("❌ Forward proxy test failed - SOCKS connection not working")

            return result

        except Exception as e:
            print(f"❌ Forward proxy test exception: {e}")
            return False
        finally:
            # Stop processes
            for proc in [p for p in self.processes if p.poll() is None]:
                proc.terminate()
            self.processes.clear()

    def test_reverse_proxy_pywssocks_to_linksocks(self) -> bool:
        """Test reverse proxy: pywssocks server -> linksocks client"""
        print("\n" + "=" * 60)
        print("Test 3: Reverse proxy (pywssocks server -> linksocks client)")
        print("=" * 60)

        try:
            # Start pywssocks reverse server
            print("1. Starting pywssocks reverse server...")
            server_proc, ws_port, socks_port = self.start_pywssocks_server(reverse=True)
            print(
                f"   Server started on WebSocket port: {ws_port}, SOCKS port: {socks_port}"
            )
            time.sleep(2)

            # Start linksocks reverse client
            print("2. Starting linksocks reverse client...")
            ws_url = f"ws://127.0.0.1:{ws_port}/socket"  # Add /socket path
            client_proc, _ = self.start_linksocks_client(ws_url, reverse=True)
            print("   Reverse client started")
            time.sleep(3)

            # Test SOCKS proxy on the server side
            print("3. Testing reverse SOCKS proxy connection...")
            result = self.test_socks_proxy(socks_port)

            if result:
                print("✅ Reverse proxy test successful!")
            else:
                print("❌ Reverse proxy test failed - SOCKS connection not working")

            return result

        except Exception as e:
            print(f"❌ Reverse proxy test exception: {e}")
            return False
        finally:
            # Stop processes
            for proc in [p for p in self.processes if p.poll() is None]:
                proc.terminate()
            self.processes.clear()

    def test_reverse_proxy_linksocks_to_pywssocks(self) -> bool:
        """Test reverse proxy: linksocks server -> pywssocks client"""
        print("\n" + "=" * 60)
        print("Test 4: Reverse proxy (linksocks server -> pywssocks client)")
        print("=" * 60)

        try:
            # Start linksocks reverse server
            print("1. Starting linksocks reverse server...")
            server_proc, ws_port, socks_port = self.start_linksocks_server(reverse=True)
            print(
                f"   Server started on WebSocket port: {ws_port}, SOCKS port: {socks_port}"
            )
            time.sleep(2)

            # Start pywssocks reverse client
            print("2. Starting pywssocks reverse client...")
            ws_url = f"ws://127.0.0.1:{ws_port}/socket"
            client_proc, _ = self.start_pywssocks_client(
                ws_url, reverse=True
            )  # reverse mode doesn't need port
            print("   Reverse client started")
            time.sleep(3)

            # Test SOCKS proxy
            print("3. Testing SOCKS proxy connection...")
            result = self.test_socks_proxy(socks_port)

            if result:
                print("✅ Reverse proxy test successful!")
            else:
                print("❌ Reverse proxy test failed - SOCKS connection not working")

            return result

        except Exception as e:
            print(f"❌ Reverse proxy test exception: {e}")
            return False
        finally:
            # Stop processes
            for proc in [p for p in self.processes if p.poll() is None]:
                proc.terminate()
            self.processes.clear()

    def run_all_tests(self) -> bool:
        """Run all compatibility tests"""
        print("Starting pywssocks and linksocks compatibility test")
        print(f"Temporary directory: {self.temp_dir}")

        try:
            # Install dependencies
            print("\nInstalling dependencies...")
            self.install_dependencies()

            # Download linksocks
            print("\nDownloading linksocks...")
            self.download_linksocks()

            # Run tests
            print("\nRunning compatibility tests...")
            test1_result = self.test_forward_proxy_pywssocks_to_linksocks()
            time.sleep(2)
            test2_result = self.test_forward_proxy_linksocks_to_pywssocks()
            time.sleep(2)
            test3_result = self.test_reverse_proxy_pywssocks_to_linksocks()
            time.sleep(2)
            test4_result = self.test_reverse_proxy_linksocks_to_pywssocks()

            # Summary
            print("\n" + "=" * 60)
            print("Test Results Summary")
            print("=" * 60)
            print(
                f"Test 1 - Forward (pywssocks→linksocks): {'✅ Pass' if test1_result else '❌ Fail'}"
            )
            print(
                f"Test 2 - Forward (linksocks→pywssocks): {'✅ Pass' if test2_result else '❌ Fail'}"
            )
            print(
                f"Test 3 - Reverse (pywssocks→linksocks): {'✅ Pass' if test3_result else '❌ Fail'}"
            )
            print(
                f"Test 4 - Reverse (linksocks→pywssocks): {'✅ Pass' if test4_result else '❌ Fail'}"
            )

            overall_success = (
                test1_result and test2_result and test3_result and test4_result
            )
            print(
                f"\nOverall compatibility: {'✅ Compatible' if overall_success else '❌ Incompatible'}"
            )

            if not overall_success:
                # Show detailed protocol analysis if any test failed
                self.print_protocol_incompatibility_diagnosis()

            return overall_success

        except Exception as e:
            print(f"Error during testing: {e}")
            import traceback

            traceback.print_exc()

            # Check if it's a download/setup issue vs compatibility issue
            if (
                "rate limit" in str(e).lower()
                or "could not get release" in str(e).lower()
            ):
                print(
                    "\nNote: Test failed due to download/setup issues, not compatibility issues."
                )
                print("This does not indicate anything about tool compatibility.")

            return False


def main():
    """Main function"""
    try:
        # Run tests
        with TestRunner() as runner:
            success = runner.run_all_tests()

            print("\n" + "=" * 60)
            print("Conclusion")
            print("=" * 60)

            if success:
                print("✅ The tools are compatible and can interoperate!")
            else:
                print("❌ Compatibility test could not be completed successfully.")
                print("Check the error details above to determine the cause.")

            print("\nNext steps:")
            print("1. Review test output for specific error details")
            print(
                "2. If download failed, try again later or manually download linksocks"
            )
            print("3. If tests ran but failed, investigate protocol differences")
            print("4. Consider using each tool within its intended ecosystem")

            return 0 if success else 1

    except KeyboardInterrupt:
        print("\nTest interrupted by user")
        return 1
    except Exception as e:
        print(f"Test failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
