"""Reusable offline benchmark fixtures and fake network infrastructure."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpcore

from email_scanner.dns import AsyncDNSResolver
from email_scanner.models import NormalizedURL


class SyntheticSiteGenerator:
    """Generates deterministic offline synthetic web pages for benchmarks."""

    @staticmethod
    def get_page_content(site_index: int, path: str) -> tuple[int, dict[str, str], bytes]:
        """Return (status_code, headers, body) for a synthetic site and path."""
        domain = f"site-{site_index}.org"
        headers = {"Content-Type": "text/html; charset=utf-8"}

        if path == "/robots.txt":
            content = f"User-agent: *\nAllow: /\n# Synthetic site {site_index}\n"
            return (200, {"Content-Type": "text/plain"}, content.encode("utf-8"))

        if path in {"", "/", "/index.html"}:
            body = (
                f"<!doctype html><html><head><title>Home Site {site_index}</title></head>"
                f"<body><h1>Welcome to {domain}</h1>"
                f'<p>Contact us at <a href="mailto:contact@{domain}">contact@{domain}</a> '
                f"or info@{domain}</p>"
                f'<a href="https://{domain}/about">About Us</a> '
                f'<a href="https://{domain}/contact">Contact Us</a> '
                f'<a href="https://{domain}/team">Our Team</a>'
                f"</body></html>"
            )
            return (200, headers, body.encode("utf-8"))

        if path == "/about":
            body = (
                f"<!doctype html><html><head><title>About Site {site_index}</title></head>"
                f"<body><h1>About {domain}</h1>"
                f"<p>Email our press team at press@{domain}</p></body></html>"
            )
            return (200, headers, body.encode("utf-8"))

        if path == "/contact":
            body = (
                f"<!doctype html><html><head><title>Contact Site {site_index}</title></head>"
                f"<body><h1>Contact {domain}</h1>"
                f"<p>Support: support@{domain}</p>"
                f'<p>Sales: <a href="mailto:sales@{domain}">sales@{domain}</a></p></body></html>'
            )
            return (200, headers, body.encode("utf-8"))

        if path == "/team":
            body = (
                f"<!doctype html><html><head><title>Team Site {site_index}</title></head>"
                f"<body><h1>Team {domain}</h1>"
                f"<p>CEO: ceo@{domain}</p>"
                f"<p>CTO: cto@{domain}</p></body></html>"
            )
            return (200, headers, body.encode("utf-8"))

        return (404, headers, b"<!doctype html><html><body>404 Not Found</body></html>")


class OfflineBenchmarkNetworkStream(httpcore.AsyncNetworkStream):
    """Network stream returning synthetic response bytes without real socket calls."""

    def __init__(
        self,
        target_host: str,
        simulated_delay_sec: float = 0.0,
        async_sleeper: Callable[[float], Awaitable[None]] | None = None,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self.target_host = target_host
        self.simulated_delay_sec = simulated_delay_sec
        self._sleeper = async_sleeper
        self._on_close = on_close
        self.written_bytes = b""
        self.read_buffer = b""
        self.closed = False

    async def read(self, max_bytes: int = 4096, timeout: float | None = None) -> bytes:
        if not self.read_buffer:
            return b""
        chunk = self.read_buffer[:max_bytes]
        self.read_buffer = self.read_buffer[max_bytes:]
        return chunk

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self.written_bytes += buffer
        if b"\r\n\r\n" in self.written_bytes:
            header_part, body_remainder = self.written_bytes.split(b"\r\n\r\n", 1)
            lines = header_part.split(b"\r\n")
            first_line = lines[0].decode("latin1")
            parts = first_line.split(" ")
            path = parts[1] if len(parts) > 1 else "/"

            site_index = 0
            for line in lines[1:]:
                line_str = line.decode("latin1", errors="ignore").lower()
                if line_str.startswith("host:"):
                    host_val = line_str.split("host:")[1].strip()
                    if "site-" in host_val:
                        try:
                            idx_str = host_val.split("site-")[1].split(".")[0]
                            site_index = int(idx_str)
                        except ValueError:
                            site_index = 0
                    break

            status_code, headers, body_bytes = SyntheticSiteGenerator.get_page_content(
                site_index, path
            )

            status_text = "OK" if status_code == 200 else "Not Found"
            header_lines = "\r\n".join(f"{k}: {v}" for k, v in headers.items())
            resp_header = (
                f"HTTP/1.1 {status_code} {status_text}\r\n"
                f"Content-Length: {len(body_bytes)}\r\n"
                f"{header_lines}\r\n"
                f"\r\n"
            )
            self.read_buffer = resp_header.encode("latin1") + body_bytes
            self.written_bytes = body_remainder

            if self.simulated_delay_sec > 0.0:
                if self._sleeper is not None:
                    await self._sleeper(self.simulated_delay_sec)
                else:
                    await asyncio.sleep(self.simulated_delay_sec)

    async def aclose(self) -> None:
        if not self.closed:
            self.closed = True
            if self._on_close is not None:
                self._on_close()

    async def start_tls(
        self,
        ssl_context: Any,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        return self

    def get_extra_info(self, info: str, default: Any = None) -> Any:
        return default


class OfflineBenchmarkNetworkBackend(httpcore.AsyncNetworkBackend):
    """Network backend routing connection attempts to synthetic streams."""

    def __init__(
        self,
        simulated_delay_sec: float = 0.0,
        async_sleeper: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self.simulated_delay_sec = simulated_delay_sec
        self._sleeper = async_sleeper
        self.connected_hosts: list[tuple[str, int]] = []
        self.active_concurrency = 0
        self.peak_concurrency = 0

    def _decrement_active_concurrency(self) -> None:
        self.active_concurrency = max(0, self.active_concurrency - 1)

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        self.connected_hosts.append((host, port))
        self.active_concurrency += 1
        if self.active_concurrency > self.peak_concurrency:
            self.peak_concurrency = self.active_concurrency

        stream = OfflineBenchmarkNetworkStream(
            target_host=host,
            simulated_delay_sec=self.simulated_delay_sec,
            async_sleeper=self._sleeper,
            on_close=self._decrement_active_concurrency,
        )
        return stream

    async def sleep(self, seconds: float) -> None:
        if self._sleeper is not None:
            await self._sleeper(seconds)
        else:
            await asyncio.sleep(seconds)


class OfflineBenchmarkDNSResolver(AsyncDNSResolver):
    """Async DNS resolver mapping site-{i}.example.org to synthetic public IP addresses."""

    async def resolve(self, url: NormalizedURL) -> tuple[str, ...]:
        return await self.resolve_host(url.hostname, url.port or 80)

    async def resolve_host(self, hostname: str, port: int = 80) -> tuple[str, ...]:
        clean_host = hostname.strip().strip("[]")
        if "site-" in clean_host:
            try:
                idx_str = clean_host.split("site-")[1].split(".")[0]
                site_idx = int(idx_str)
                # Map to public TEST-NET-2 IP range 198.51.100.X
                ip_octet = (site_idx % 250) + 1
                return (f"198.51.100.{ip_octet}",)
            except ValueError:
                pass
        return ("198.51.100.1",)
