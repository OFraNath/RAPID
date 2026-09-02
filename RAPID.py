"""
RAPID — Reliable Asynchronous Parallel Internet Downloader
Parallel download tool for everyday and professional use.

Usage:
    python RAPID.py                          # interactive mode (TUI)
    python RAPID.py <url> [options]          # direct CLI mode

Dependencies:
    pip install requests rich urllib3
"""

from __future__ import annotations

import argparse
import logging
import queue
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, unquote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.prompt import Prompt, IntPrompt

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_WORKERS = 16
MAX_WORKERS     = 32
CHUNK_SIZE      = 1  * 1024 * 1024   # 1 MB per stream read
CHUNK_PART_SIZE = 64 * 1024 * 1024   # 64 MB per queue slice
MIN_SPLIT_SIZE  = 8  * 1024 * 1024   # below this, do not split
MAX_RETRIES     = 6
CONNECT_TIMEOUT = 15
READ_TIMEOUT    = 45
WRITE_BUF_SIZE  = 4  * 1024 * 1024   # 4 MB write buffer
LOG_FILE        = Path.home() / ".rapid" / "rapid.log"

console = Console()


# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

def setup_logging(debug: bool = False) -> logging.Logger:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("rapid")
    log.setLevel(logging.DEBUG if debug else logging.INFO)
    log.handlers.clear()

    fh = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    fh.setLevel(logging.DEBUG)
    log.addHandler(fh)

    if debug:
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        log.addHandler(sh)

    return log


# ─────────────────────────────────────────────────────────────────────────────
# Chunk
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Chunk:
    index: int
    start: int
    end:   int
    done:  bool = False

    @property
    def size(self) -> int:
        return self.end - self.start + 1


# ─────────────────────────────────────────────────────────────────────────────
# Global download state — thread-safe
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DownloadState:
    url:         str
    file_name:   str
    total_bytes: int
    n_workers:   int
    chunks:      list[Chunk] = field(default_factory=list)

    _lock:        threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    _bytes_done:  int            = field(default=0, repr=False, compare=False)

    def record_bytes(self, n: int) -> None:
        with self._lock:
            self._bytes_done += n

    @property
    def bytes_downloaded(self) -> int:
        with self._lock:
            return self._bytes_done

    @property
    def chunks_done(self) -> int:
        return sum(1 for c in self.chunks if c.done)


# ─────────────────────────────────────────────────────────────────────────────
# HTTP session
# ─────────────────────────────────────────────────────────────────────────────

def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=MAX_RETRIES,
        backoff_factor=0.75,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=MAX_WORKERS,
        pool_maxsize=MAX_WORKERS * 2,
        pool_block=False,
    )
    session.mount("http://",  adapter)
    session.mount("https://", adapter)
    session.headers.update({
        "User-Agent":      "Reliable Asynchronous Parallel Internet Downloader",
        "Accept-Encoding": "identity",
        "Connection":      "keep-alive",
    })
    return session


# ─────────────────────────────────────────────────────────────────────────────
# Server inspection
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ServerInfo:
    total_bytes:    int
    accepts_range:  bool
    suggested_name: str
    content_type:   str


def inspect_url(session: requests.Session, url: str, log: logging.Logger) -> ServerInfo:
    try:
        resp = session.head(url, timeout=(CONNECT_TIMEOUT, 15), allow_redirects=True)
        resp.raise_for_status()
    except requests.exceptions.SSLError as e:
        raise RuntimeError(f"SSL error: {e}") from e
    except requests.exceptions.ConnectionError as e:
        raise RuntimeError(f"Connection failed: {e}") from e
    except requests.exceptions.Timeout:
        raise RuntimeError("Timeout while inspecting URL")
    except requests.exceptions.HTTPError as e:
        raise RuntimeError(f"HTTP {e.response.status_code if e.response is not None else '?'}") from e
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Network error: {e}") from e

    total   = int(resp.headers.get("Content-Length", 0))
    accepts = resp.headers.get("Accept-Ranges", "none").lower() != "none"
    ctype   = resp.headers.get("Content-Type", "application/octet-stream")

    name = ""
    cd = resp.headers.get("Content-Disposition", "")
    if cd:
        if "filename*=" in cd:
            try:
                rfc_part = cd.split("filename*=")[-1].split(";")[0].strip()
                if "''" in rfc_part:
                    charset, _, encoded = rfc_part.partition("''")
                    name = unquote(encoded, encoding=charset or "utf-8")
            except Exception:
                name = ""
        if not name and "filename=" in cd:
            try:
                raw = cd.split("filename=")[-1].split(";")[0].strip().strip('"\'')
                if not raw.startswith("UTF-"):
                    name = unquote(raw)
            except Exception:
                name = ""
    if not name:
        name = unquote(Path(urlparse(resp.url).path).name) or "download"

    log.info(f"HEAD {url} -> {total/1e6:.2f} MB | Range={accepts} | type={ctype}")
    return ServerInfo(total_bytes=total, accepts_range=accepts,
                      suggested_name=name, content_type=ctype)


# ─────────────────────────────────────────────────────────────────────────────
# Worker
# ─────────────────────────────────────────────────────────────────────────────

def _backoff(attempt: int) -> None:
    time.sleep(min(2 ** (attempt - 1), 30))


def worker(
    worker_id: int,
    session:   requests.Session,
    state:     DownloadState,
    chunk_q:   "queue.Queue[Chunk]",
    file_lock: threading.Lock,
    log:       logging.Logger,
    progress:  Progress,
    main_task: TaskID,
) -> None:
    while True:
        try:
            chunk = chunk_q.get_nowait()
        except queue.Empty:
            return

        headers = {"Range": f"bytes={chunk.start}-{chunk.end}"}
        success = False

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = session.get(
                    state.url,
                    headers=headers,
                    stream=True,
                    timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                )
                resp.raise_for_status()

                write_pos = chunk.start
                buf = bytearray()

                for data in resp.iter_content(chunk_size=CHUNK_SIZE):
                    if not data:
                        continue
                    buf.extend(data)
                    n = len(data)
                    state.record_bytes(n)
                    progress.advance(main_task, n)

                    if len(buf) >= WRITE_BUF_SIZE:
                        with file_lock:
                            with open(state.file_name, "r+b") as f:
                                f.seek(write_pos)
                                f.write(bytes(buf))
                        write_pos += len(buf)
                        buf.clear()

                if buf:
                    with file_lock:
                        with open(state.file_name, "r+b") as f:
                            f.seek(write_pos)
                            f.write(bytes(buf))

                chunk.done = True
                success = True
                log.debug(f"[W{worker_id:02d}] chunk {chunk.index} OK ({chunk.size/1e6:.1f} MB)")
                break

            except requests.exceptions.Timeout:
                log.warning(f"[W{worker_id:02d}] chunk {chunk.index} timeout (a{attempt})")
            except requests.exceptions.ChunkedEncodingError:
                log.warning(f"[W{worker_id:02d}] chunk {chunk.index} stream interrupted (a{attempt})")
            except requests.exceptions.ConnectionError as e:
                log.warning(f"[W{worker_id:02d}] chunk {chunk.index} connection: {e} (a{attempt})")
            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else "?"
                log.warning(f"[W{worker_id:02d}] chunk {chunk.index} HTTP {status} (a{attempt})")
            except OSError as e:
                log.error(f"[W{worker_id:02d}] chunk {chunk.index} disk error: {e}")
                chunk_q.task_done()
                return

            if attempt < MAX_RETRIES:
                _backoff(attempt)

        if not success:
            log.error(f"[W{worker_id:02d}] chunk {chunk.index} FAILED after {MAX_RETRIES} attempts.")

        chunk_q.task_done()


# ─────────────────────────────────────────────────────────────────────────────
# Main orchestrator
# ─────────────────────────────────────────────────────────────────────────────

def run_download(
    url:       str,
    file_name: str,
    n_workers: int,
    log:       Optional[logging.Logger] = None,
) -> bool:
    if log is None:
        log = setup_logging()

    session    = make_session()
    start_time = time.monotonic()

    console.print(f"\n[bold]Checking:[/bold] {url}")
    try:
        info = inspect_url(session, url, log)
    except RuntimeError as e:
        console.print(f"[red]✗ {e}[/red]")
        return False

    console.print(
        f"  Size   : [cyan]{info.total_bytes/1e6:.2f} MB[/cyan]\n"
        f"  Range  : {'[green]yes[/green]' if info.accepts_range else '[yellow]no (single download)[/yellow]'}\n"
        f"  Type   : [dim]{info.content_type}[/dim]"
    )

    # Split into chunks
    if not info.accepts_range or info.total_bytes < MIN_SPLIT_SIZE:
        chunks = [Chunk(index=0, start=0, end=max(info.total_bytes - 1, 0))]
    else:
        chunks, offset, idx = [], 0, 0
        while offset < info.total_bytes:
            end = min(offset + CHUNK_PART_SIZE - 1, info.total_bytes - 1)
            chunks.append(Chunk(index=idx, start=offset, end=end))
            offset = end + 1
            idx   += 1

    state = DownloadState(
        url=url, file_name=file_name,
        total_bytes=info.total_bytes, n_workers=n_workers,
        chunks=chunks,
    )

    # Pre-allocate the file
    try:
        with open(file_name, "wb") as f:
            if info.total_bytes > 0:
                f.seek(info.total_bytes - 1)
                f.write(b"\x00")
    except OSError as e:
        console.print(f"[red]✗ Error creating file: {e}[/red]")
        return False

    console.print(
        f"\n[bold]Starting download:[/bold] {file_name}\n"
        f"  {len(chunks)} chunks × {CHUNK_PART_SIZE//1024//1024} MB  |  {n_workers} workers\n"
    )

    file_lock = threading.Lock()

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}"),
        BarColumn(bar_width=None),
        "[progress.percentage]{task.percentage:>5.1f}%",
        "•",
        DownloadColumn(),
        "•",
        TransferSpeedColumn(),
        "•",
        TimeRemainingColumn(),
        expand=True,
    )
    main_task = progress.add_task(Path(file_name).name[:45], total=info.total_bytes)

    chunk_q: queue.Queue[Chunk] = queue.Queue()
    for c in chunks:
        chunk_q.put(c)

    _stop = threading.Event()

    def _tick():
        while not _stop.is_set():
            progress.update(main_task, completed=state.bytes_downloaded)
            time.sleep(0.5)

    ticker = threading.Thread(target=_tick, daemon=True)

    with progress:
        ticker.start()
        with ThreadPoolExecutor(max_workers=min(n_workers, len(chunks))) as executor:
            futures = [
                executor.submit(worker, i, session, state, chunk_q, file_lock, log, progress, main_task)
                for i in range(min(n_workers, len(chunks)))
            ]
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception as e:
                    log.error(f"Worker terminated with exception: {e}")

        _stop.set()
        ticker.join(timeout=2)
        progress.update(main_task, completed=info.total_bytes)

    failed = [c.index for c in state.chunks if not c.done]
    if failed:
        console.print(f"\n[red]✗ {len(failed)} chunk(s) not completed: {failed[:10]}{'…' if len(failed)>10 else ''}[/red]")
        log.error(f"Incomplete download. Failed chunks: {failed}")
        return False

    elapsed   = time.monotonic() - start_time
    avg_speed = info.total_bytes / elapsed / 1e6 if elapsed > 0 else 0

    console.print(
        f"\n[bold green]✓ Download complete![/bold green]\n"
        f"  File   : [bold]{file_name}[/bold]\n"
        f"  Size   : {info.total_bytes/1e6:.2f} MB\n"
        f"  Time   : {elapsed:.1f}s\n"
        f"  Average: [green]{avg_speed:.2f} MB/s[/green]\n"
    )
    log.info(f"Completed: {file_name} | {avg_speed:.2f} MB/s | {elapsed:.1f}s")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Interactive mode
# ─────────────────────────────────────────────────────────────────────────────

def interactive_mode(log: logging.Logger) -> None:
    console.print(
        Panel.fit(
            "[bold cyan]RAPID[/bold cyan] — Reliable Asynchronous Parallel Internet Downloader\n"
            "[dim]Parallel download tool for professional use[/dim]",
            border_style="cyan",
        )
    )

    while True:
        console.print(
            "\n[bold]What do you want to do?[/bold]\n"
            "  [cyan]1[/cyan]  Download a file\n"
            "  [cyan]2[/cyan]  Exit\n"
        )
        choice = Prompt.ask("Option", choices=["1", "2"], default="1")

        if choice == "2":
            console.print("[dim]Goodbye.[/dim]")
            break

        elif choice == "1":
            url = Prompt.ask("URL").strip()
            if not url:
                console.print("[red]URL cannot be empty.[/red]")
                continue

            try:
                info = inspect_url(make_session(), url, log)
                suggested = info.suggested_name
            except RuntimeError:
                suggested = unquote(Path(urlparse(url).path).name) or "download"

            file_name = Prompt.ask("File name", default=suggested).strip() or suggested
            n = IntPrompt.ask(f"Parallel workers (1–{MAX_WORKERS})", default=DEFAULT_WORKERS)
            n = max(1, min(n, MAX_WORKERS))

            run_download(url=url, file_name=file_name, n_workers=n, log=log)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rapid",
        description="RAPID — Reliable Asynchronous Parallel Internet Downloader",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
   rapid                                            interactive mode
   rapid https://example.com/file.zip              automatic name
   rapid https://example.com/file.zip -o out.zip -w 24
        """,
    )
    p.add_argument("url",             nargs="?",                         help="URL to download")
    p.add_argument("-o", "--output",  metavar="FILE",                    help="Output file name")
    p.add_argument("-w", "--workers", type=int, default=DEFAULT_WORKERS, metavar="N",
                   help=f"Parallel workers (default: {DEFAULT_WORKERS})")
    p.add_argument("--debug",         action="store_true",               help="Verbose logging to terminal")
    return p


def main() -> None:
    parser = build_parser()
    args   = parser.parse_args()
    log    = setup_logging(debug=args.debug)

    if args.url:
        file_name = args.output or unquote(Path(urlparse(args.url).path).name) or "download"
        success = run_download(
            url=args.url,
            file_name=file_name,
            n_workers=max(1, min(args.workers, MAX_WORKERS)),
            log=log,
        )
        sys.exit(0 if success else 1)

    interactive_mode(log)


if __name__ == "__main__":
    main()
