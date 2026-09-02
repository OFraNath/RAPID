"""
RAPID GUI — Reliable Asynchronous Parallel Internet Downloader
Simple, functional Tkinter interface for the RAPID download engine.

Usage:
    python RAPID.py

Dependencies:
    requests and urllib3 are installed automatically on first run if missing
    (via "pip install"). tkinter ships with standard Python; if it's absent
    the interpreter itself needs a tkinter-enabled Python install, which pip
    cannot fix — the script will tell you how to get it.

Internationalization (i18n):
    The program is built with English as the default/base language.
    Additional languages are loaded from the "languages" folder next to
    this script. Any file named "<code>.language" (e.g. "pt-br.language")
    containing a flat JSON object of translation keys will be picked up
    automatically and offered in the language selector.

    If a translation file is missing, or a key is missing from it, the
    program silently falls back to the built-in English text — no file,
    no translation, falls back to English.
"""

from __future__ import annotations

import importlib
import importlib.util
import subprocess
import sys
import os

# ─────────────────────────────────────────────────────────────────────────────
# Self-installing dependencies
# ─────────────────────────────────────────────────────────────────────────────
#
# Third-party packages (everything not shipped with the standard library) are
# checked on startup and installed automatically with pip if missing, so the
# user can just "python RAPID.py" without a manual setup step. tkinter is
# stdlib but built into the Python interpreter itself, so it can't be pip
# installed — if it's missing we just explain how to fix that instead.

_THIRD_PARTY_PACKAGES = {
    # import name -> pip package name (usually identical, kept separate in
    # case a future dependency's import name differs from its PyPI name)
    "requests": "requests",
    "urllib3":  "urllib3",
}


def _ensure_dependencies() -> None:
    missing = [
        pip_name
        for module_name, pip_name in _THIRD_PARTY_PACKAGES.items()
        if importlib.util.find_spec(module_name) is None
    ]
    if not missing:
        return

    print(f"[RAPID] Missing dependencies: {', '.join(missing)}. Installing…")
    try:
        subprocess.check_call([
            sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", *missing,
        ])
    except Exception as e:
        print(f"[RAPID] Automatic install failed: {e}")
        print(f"[RAPID] Please install manually:\n    {sys.executable} -m pip install {' '.join(missing)}")
        sys.exit(1)

    # Make the freshly installed packages importable without restarting.
    importlib.invalidate_caches()
    for module_name in _THIRD_PARTY_PACKAGES:
        if module_name in sys.modules:
            importlib.reload(sys.modules[module_name])

    still_missing = [
        pip_name
        for module_name, pip_name in _THIRD_PARTY_PACKAGES.items()
        if importlib.util.find_spec(module_name) is None
    ]
    if still_missing:
        print(f"[RAPID] Still missing after install attempt: {', '.join(still_missing)}")
        print(f"[RAPID] Please install manually:\n    {sys.executable} -m pip install {' '.join(still_missing)}")
        sys.exit(1)

    print("[RAPID] Dependencies installed successfully.")


def _check_tkinter() -> None:
    if importlib.util.find_spec("tkinter") is None:
        print(
            "[RAPID] The 'tkinter' module is missing from this Python install.\n"
            "        pip cannot install it — it ships with the Python interpreter itself.\n"
            "        Debian/Ubuntu:  sudo apt install python3-tk\n"
            "        Fedora:         sudo dnf install python3-tkinter\n"
            "        macOS (brew):   brew install python-tk\n"
            "        Windows:        reinstall Python from python.org with the\n"
            "                        \"tcl/tk and IDLE\" option checked."
        )
        sys.exit(1)


_check_tkinter()
_ensure_dependencies()

import json
import locale
import logging
import queue
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional
from urllib.parse import unquote, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_WORKERS = 16
MAX_WORKERS     = 32
CHUNK_SIZE      = 1  * 1024 * 1024   # 1 MB per stream read
CHUNK_PART_SIZE = 64 * 1024 * 1024   # 64 MB per queue slice
MIN_SPLIT_SIZE  = 8  * 1024 * 1024   # below this, don't split
MAX_RETRIES     = 6
CONNECT_TIMEOUT = 15
READ_TIMEOUT    = 45
WRITE_BUF_SIZE  = 4  * 1024 * 1024   # 4 MB write buffer

# Everything (log and downloads) is restricted to the directory the script runs from.
BASE_DIR        = Path(__file__).resolve().parent
LOG_FILE        = BASE_DIR / "rapid.log"
LANGUAGES_DIR   = BASE_DIR / "languages"
DEFAULT_LANG    = "en"


def setup_logging(debug: bool = False) -> logging.Logger:
    log = logging.getLogger("rapid_gui")
    log.setLevel(logging.DEBUG if debug else logging.INFO)
    log.handlers.clear()

    fh = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    fh.setLevel(logging.DEBUG)
    log.addHandler(fh)
    return log


# ─────────────────────────────────────────────────────────────────────────────
# i18n — translation loader
# ─────────────────────────────────────────────────────────────────────────────
#
# Built-in English strings act as the ultimate fallback, so the app always
# works even if the "languages" folder or "en.language" file is missing.

_BUILTIN_EN = {
    "window_title": "RAPID — Reliable Asynchronous Parallel Internet Downloader",
    "language_label": "Language:",

    "source_frame": "Source",
    "url_label": "URL:",
    "verify_btn": "Verify",
    "save_as_label": "Save as:",
    "browse_btn": "Browse…",
    "workers_label": "Workers:",
    "info_default": "Size: —    Range: —    Type: —",
    "info_format": "Size: {size}    Range: {range}    Type: {type}",
    "range_yes": "yes",
    "range_no": "no",

    "download_btn": "▶ Download",
    "cancel_btn": "■ Cancel",
    "open_log_btn": "Open log",

    "progress_frame": "Progress",
    "status_ready": "Ready.",
    "log_frame": "Log",

    "app_title": "RAPID",
    "warn_need_url_verify": "Enter a URL first.",
    "warn_need_url": "Enter a URL.",
    "confirm_close_title": "RAPID",
    "confirm_close_msg": "A download is in progress. Quit anyway?",
    "log_file_title": "Log file",

    "error_checking_url": "✗ Error checking URL: {error}",
    "verified_msg": "Verified: {url} -> {size}, range={range}",
    "status_checking": "Checking URL…",
    "cancel_requested": "Cancellation requested…",
    "size_range_type_msg": "Size: {size} | Range: {range} | Type: {type}",
    "error_create_file": "✗ Error creating file: {error}",
    "starting_download": "Starting download: {file} | {chunks} chunk(s) × {mb} MB | {workers} workers",
    "worker_finished_error": "Worker finished with error: {error}",
    "cancelled_by_user": "✗ Download cancelled by user.",
    "chunks_incomplete": "✗ {count} chunk(s) not completed: {list}",
    "download_complete": "✓ Download complete! {file} — {size} in {elapsed}s ({speed} MB/s)",
    "status_done": "Done.",
    "status_interrupted": "Interrupted.",
    "status_progress": "{done} / {total}  —  {pct}%",
    "stats_line": "Speed: {speed} MB/s    Elapsed: {elapsed}s    ETA: {remaining}s",

    "chunk_ok": "[W{worker_id}] chunk {chunk} OK ({size} MB)",
    "chunk_timeout": "[W{worker_id}] chunk {chunk} timeout (attempt {attempt})",
    "chunk_stream_interrupted": "[W{worker_id}] chunk {chunk} stream interrupted (attempt {attempt})",
    "chunk_connection_error": "[W{worker_id}] chunk {chunk} connection: {error} (attempt {attempt})",
    "chunk_http_error": "[W{worker_id}] chunk {chunk} HTTP {status} (attempt {attempt})",
    "chunk_disk_error": "[W{worker_id}] chunk {chunk} disk error: {error}",
    "chunk_failed": "[W{worker_id}] chunk {chunk} FAILED after {retries} attempts.",
}


class Translator:
    """
    Loads *.language files from LANGUAGES_DIR (flat JSON key -> string).
    Each file's code is its filename stem (e.g. "pt-br.language" -> "pt-br").
    English ("en") is always available, built into the program, and is used
    as the fallback for any missing file or missing key in any language.
    """

    def __init__(self, languages_dir: Path, log: Optional[logging.Logger] = None):
        self.languages_dir = languages_dir
        self.log = log
        self.catalogs: dict[str, dict[str, str]] = {"en": dict(_BUILTIN_EN)}
        self.display_names: dict[str, str] = {"en": "English"}
        self.current = DEFAULT_LANG
        self._discover()

    def _discover(self) -> None:
        if not self.languages_dir.is_dir():
            return
        for path in sorted(self.languages_dir.glob("*.language")):
            code = path.stem.strip().lower()
            if not code:
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("root JSON element must be an object")
            except Exception as e:
                if self.log:
                    self.log.warning(f"Failed to load language file '{path.name}': {e}")
                continue

            display_name = str(data.get("_meta_name", code))
            # Merge on top of English so missing keys fall back automatically.
            merged = dict(_BUILTIN_EN)
            merged.update({k: v for k, v in data.items() if k != "_meta_name"})
            self.catalogs[code] = merged
            self.display_names[code] = display_name

    def available(self) -> list[tuple[str, str]]:
        """Returns list of (code, display_name), English first, then alphabetical."""
        codes = sorted(self.catalogs.keys(), key=lambda c: (c != "en", self.display_names[c]))
        return [(c, self.display_names[c]) for c in codes]

    def set_language(self, code: str) -> None:
        code = code.lower()
        self.current = code if code in self.catalogs else DEFAULT_LANG

    def t(self, key: str, **kwargs) -> str:
        catalog = self.catalogs.get(self.current, _BUILTIN_EN)
        template = catalog.get(key) or _BUILTIN_EN.get(key) or key
        if kwargs:
            try:
                return template.format(**kwargs)
            except Exception:
                return template
        return template

    def autodetect(self) -> str:
        """
        Tries to guess the system's language and returns the best matching
        available code (falling back to DEFAULT_LANG if nothing matches).
        Does not change self.current — call set_language() with the result.
        """
        candidates: list[str] = []
        try:
            # locale.getlocale() is preferred over the deprecated getdefaultlocale().
            for getter in (locale.getlocale, locale.getdefaultlocale):
                try:
                    loc = getter()
                except Exception:
                    continue
                if loc and loc[0]:
                    candidates.append(loc[0])
        except Exception:
            pass

        # Also check common environment variables (useful on Linux/macOS).
        for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
            val = os.environ.get(var)
            if val:
                candidates.append(val.split(":")[0])

        available = set(self.catalogs.keys())

        for raw in candidates:
            if not raw:
                continue
            # Normalize e.g. "pt_BR.UTF-8" -> "pt-br", "en_US" -> "en-us"
            norm = raw.split(".")[0].replace("_", "-").lower()
            if norm in available:
                return norm
            # Try just the primary subtag: "pt-br" -> "pt"
            primary = norm.split("-")[0]
            if primary in available:
                return primary
            # Try matching an available code by its primary subtag too,
            # e.g. system "pt-pt" should still match an available "pt-br".
            for code in available:
                if code.split("-")[0] == primary:
                    return code

        return DEFAULT_LANG


# ─────────────────────────────────────────────────────────────────────────────
# Structures
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


@dataclass
class DownloadState:
    url:         str
    file_name:   str
    total_bytes: int
    n_workers:   int
    chunks:      list = field(default_factory=list)

    _lock:       threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    _bytes_done: int            = field(default=0, repr=False, compare=False)

    def record_bytes(self, n: int) -> None:
        with self._lock:
            self._bytes_done += n

    @property
    def bytes_downloaded(self) -> int:
        with self._lock:
            return self._bytes_done


@dataclass
class ServerInfo:
    total_bytes:    int
    accepts_range:  bool
    suggested_name: str
    content_type:   str


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


def inspect_url(session: requests.Session, url: str, log: logging.Logger) -> ServerInfo:
    resp = session.head(url, timeout=(CONNECT_TIMEOUT, 15), allow_redirects=True)
    resp.raise_for_status()

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


def _backoff(attempt: int) -> None:
    time.sleep(min(2 ** (attempt - 1), 30))


def worker(
    worker_id: int,
    session:   requests.Session,
    state:     DownloadState,
    chunk_q:   "queue.Queue[Chunk]",
    file_lock: threading.Lock,
    log:       logging.Logger,
    stop_event: threading.Event,
    log_q:     "queue.Queue[str]",
    tr:        Translator,
) -> None:
    while not stop_event.is_set():
        try:
            chunk = chunk_q.get_nowait()
        except queue.Empty:
            return

        headers = {"Range": f"bytes={chunk.start}-{chunk.end}"}
        success = False

        for attempt in range(1, MAX_RETRIES + 1):
            if stop_event.is_set():
                chunk_q.task_done()
                return
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
                    if stop_event.is_set():
                        chunk_q.task_done()
                        return
                    if not data:
                        continue
                    buf.extend(data)
                    n = len(data)
                    state.record_bytes(n)

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
                log_q.put(tr.t("chunk_ok", worker_id=f"{worker_id:02d}", chunk=chunk.index, size=f"{chunk.size/1e6:.1f}"))
                log.debug(f"[W{worker_id:02d}] chunk {chunk.index} OK ({chunk.size/1e6:.1f} MB)")
                break

            except requests.exceptions.Timeout:
                log_q.put(tr.t("chunk_timeout", worker_id=f"{worker_id:02d}", chunk=chunk.index, attempt=attempt))
            except requests.exceptions.ChunkedEncodingError:
                log_q.put(tr.t("chunk_stream_interrupted", worker_id=f"{worker_id:02d}", chunk=chunk.index, attempt=attempt))
            except requests.exceptions.ConnectionError as e:
                log_q.put(tr.t("chunk_connection_error", worker_id=f"{worker_id:02d}", chunk=chunk.index, error=e, attempt=attempt))
            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else "?"
                log_q.put(tr.t("chunk_http_error", worker_id=f"{worker_id:02d}", chunk=chunk.index, status=status, attempt=attempt))
            except OSError as e:
                log_q.put(tr.t("chunk_disk_error", worker_id=f"{worker_id:02d}", chunk=chunk.index, error=e))
                chunk_q.task_done()
                return

            if attempt < MAX_RETRIES:
                _backoff(attempt)

        if not success:
            log_q.put(tr.t("chunk_failed", worker_id=f"{worker_id:02d}", chunk=chunk.index, retries=MAX_RETRIES))

        chunk_q.task_done()


# ─────────────────────────────────────────────────────────────────────────────
# GUI
# ─────────────────────────────────────────────────────────────────────────────

def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


class RapidGUI(tk.Tk):
    def __init__(self):
        super().__init__()

        self.log = setup_logging()
        self.tr = Translator(LANGUAGES_DIR, self.log)
        self.tr.set_language(self.tr.autodetect())

        self.title(self.tr.t("window_title"))
        self.geometry("640x560")
        self.resizable(False, False)

        self.log_q: "queue.Queue[str]" = queue.Queue()
        self.stop_event = threading.Event()
        self.download_thread: Optional[threading.Thread] = None
        self.state: Optional[DownloadState] = None
        self.info: Optional[ServerInfo] = None
        self.start_time = 0.0
        self.running = False

        self._build_ui()
        self.after(150, self._poll_log)
        self.after(200, self._poll_progress)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------------------------------------------------------- UI ----

    def _build_ui(self):
        pad = {"padx": 6, "pady": 4}

        # Language selector
        frm_lang = ttk.Frame(self)
        frm_lang.pack(fill="x", padx=6, pady=(6, 0))
        ttk.Label(frm_lang, text=self.tr.t("language_label")).pack(side="left")

        self._lang_codes = self.tr.available()  # list[(code, display_name)]
        self.lang_var = tk.StringVar(value=dict(self._lang_codes).get(self.tr.current, "English"))
        lang_combo = ttk.Combobox(
            frm_lang, textvariable=self.lang_var, state="readonly",
            values=[name for _, name in self._lang_codes], width=24,
        )
        lang_combo.pack(side="left", padx=(6, 0))
        lang_combo.bind("<<ComboboxSelected>>", self._on_language_change)

        self.frm_top = ttk.LabelFrame(self, text=self.tr.t("source_frame"))
        self.frm_top.pack(fill="x", **pad)

        self.lbl_url = ttk.Label(self.frm_top, text=self.tr.t("url_label"))
        self.lbl_url.grid(row=0, column=0, sticky="w", **pad)
        self.url_var = tk.StringVar()
        ttk.Entry(self.frm_top, textvariable=self.url_var, width=64).grid(row=0, column=1, columnspan=3, sticky="we", **pad)

        self.btn_verify = ttk.Button(self.frm_top, text=self.tr.t("verify_btn"), command=self._on_inspect)
        self.btn_verify.grid(row=0, column=4, **pad)

        self.lbl_save_as = ttk.Label(self.frm_top, text=self.tr.t("save_as_label"))
        self.lbl_save_as.grid(row=1, column=0, sticky="w", **pad)
        self.filename_var = tk.StringVar()
        ttk.Entry(self.frm_top, textvariable=self.filename_var, width=48).grid(row=1, column=1, columnspan=2, sticky="we", **pad)
        self.btn_browse = ttk.Button(self.frm_top, text=self.tr.t("browse_btn"), command=self._on_browse)
        self.btn_browse.grid(row=1, column=3, **pad)

        self.lbl_workers = ttk.Label(self.frm_top, text=self.tr.t("workers_label"))
        self.lbl_workers.grid(row=2, column=0, sticky="w", **pad)
        self.workers_var = tk.IntVar(value=DEFAULT_WORKERS)
        ttk.Spinbox(self.frm_top, from_=1, to=MAX_WORKERS, textvariable=self.workers_var, width=6).grid(row=2, column=1, sticky="w", **pad)

        self.info_var = tk.StringVar(value=self.tr.t("info_default"))
        ttk.Label(self.frm_top, textvariable=self.info_var, foreground="#555").grid(row=2, column=2, columnspan=3, sticky="w", **pad)

        self.frm_top.columnconfigure(1, weight=1)

        # Action buttons
        frm_actions = ttk.Frame(self)
        frm_actions.pack(fill="x", **pad)

        self.btn_start = ttk.Button(frm_actions, text=self.tr.t("download_btn"), command=self._on_start)
        self.btn_start.pack(side="left", padx=4)

        self.btn_cancel = ttk.Button(frm_actions, text=self.tr.t("cancel_btn"), command=self._on_cancel, state="disabled")
        self.btn_cancel.pack(side="left", padx=4)

        self.btn_open_log = ttk.Button(frm_actions, text=self.tr.t("open_log_btn"), command=self._open_log)
        self.btn_open_log.pack(side="right", padx=4)

        # Progress
        self.frm_prog = ttk.LabelFrame(self, text=self.tr.t("progress_frame"))
        self.frm_prog.pack(fill="x", **pad)

        self.progress = ttk.Progressbar(self.frm_prog, orient="horizontal", mode="determinate", length=600)
        self.progress.pack(fill="x", padx=6, pady=(6, 2))

        self.status_var = tk.StringVar(value=self.tr.t("status_ready"))
        ttk.Label(self.frm_prog, textvariable=self.status_var).pack(anchor="w", padx=6, pady=(0, 6))

        # Log
        self.frm_log = ttk.LabelFrame(self, text=self.tr.t("log_frame"))
        self.frm_log.pack(fill="both", expand=True, **pad)

        self.log_text = tk.Text(self.frm_log, height=16, bg="#111", fg="#ddd", insertbackground="#ddd",
                                 font=("Consolas", 9) if self._font_ok("Consolas") else ("Courier", 9))
        self.log_text.pack(fill="both", expand=True, side="left", padx=(6, 0), pady=6)
        scroll = ttk.Scrollbar(self.frm_log, command=self.log_text.yview)
        scroll.pack(side="right", fill="y", pady=6, padx=(0, 6))
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.configure(state="disabled")

        # Footer with stats
        frm_stats = ttk.Frame(self)
        frm_stats.pack(fill="x", **pad)
        self.stats_var = tk.StringVar(value="")
        ttk.Label(frm_stats, textvariable=self.stats_var, foreground="#333").pack(anchor="w")

    def _font_ok(self, name: str) -> bool:
        try:
            import tkinter.font as tkfont
            return name in tkfont.families()
        except Exception:
            return False

    # ------------------------------------------------------------ i18n ----

    def _on_language_change(self, _event=None):
        name_to_code = {name: code for code, name in self._lang_codes}
        code = name_to_code.get(self.lang_var.get(), DEFAULT_LANG)
        self.tr.set_language(code)
        self._retranslate_static_ui()

    def _retranslate_static_ui(self):
        """Updates all static widget texts after a language change (dynamic
        log/status text keeps whatever language it was generated in)."""
        self.title(self.tr.t("window_title"))
        self.frm_top.configure(text=self.tr.t("source_frame"))
        self.lbl_url.configure(text=self.tr.t("url_label"))
        self.btn_verify.configure(text=self.tr.t("verify_btn"))
        self.lbl_save_as.configure(text=self.tr.t("save_as_label"))
        self.btn_browse.configure(text=self.tr.t("browse_btn"))
        self.lbl_workers.configure(text=self.tr.t("workers_label"))
        self.btn_start.configure(text=self.tr.t("download_btn"))
        self.btn_cancel.configure(text=self.tr.t("cancel_btn"))
        self.btn_open_log.configure(text=self.tr.t("open_log_btn"))
        self.frm_prog.configure(text=self.tr.t("progress_frame"))
        self.frm_log.configure(text=self.tr.t("log_frame"))
        if not self.running and self.state is None:
            self.status_var.set(self.tr.t("status_ready"))
        if self.info is None:
            self.info_var.set(self.tr.t("info_default"))

    # ------------------------------------------------------------ Actions ----

    def _append_log(self, msg: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _on_browse(self):
        path = filedialog.asksaveasfilename(initialfile=self.filename_var.get() or "download")
        if path:
            self.filename_var.set(path)

    def _on_inspect(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning(self.tr.t("app_title"), self.tr.t("warn_need_url_verify"))
            return

        def task():
            try:
                info = inspect_url(make_session(), url, self.log)
            except Exception as e:
                self.log_q.put(self.tr.t("error_checking_url", error=e))
                return
            self.info = info
            if not self.filename_var.get():
                self.filename_var.set(info.suggested_name)
            self.info_var.set(self.tr.t(
                "info_format",
                size=human_size(info.total_bytes),
                range=self.tr.t("range_yes") if info.accepts_range else self.tr.t("range_no"),
                type=info.content_type,
            ))
            self.log_q.put(self.tr.t(
                "verified_msg", url=url, size=human_size(info.total_bytes), range=info.accepts_range,
            ))

        threading.Thread(target=task, daemon=True).start()

    def _on_start(self):
        if self.running:
            return
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning(self.tr.t("app_title"), self.tr.t("warn_need_url"))
            return

        file_name = self.filename_var.get().strip() or unquote(Path(urlparse(url).path).name) or "download"
        self.filename_var.set(file_name)
        n_workers = max(1, min(self.workers_var.get(), MAX_WORKERS))

        self.stop_event = threading.Event()
        self.running = True
        self.btn_start.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        self.progress.configure(value=0)
        self.status_var.set(self.tr.t("status_checking"))

        self.download_thread = threading.Thread(
            target=self._run_download, args=(url, file_name, n_workers), daemon=True
        )
        self.download_thread.start()

    def _on_cancel(self):
        if self.running:
            self.stop_event.set()
            self.log_q.put(self.tr.t("cancel_requested"))

    def _on_close(self):
        if self.running:
            if not messagebox.askyesno(self.tr.t("confirm_close_title"), self.tr.t("confirm_close_msg")):
                return
            self.stop_event.set()
        self.destroy()

    def _open_log(self):
        try:
            if sys.platform.startswith("win"):
                os.startfile(LOG_FILE)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(LOG_FILE)])
            else:
                subprocess.Popen(["xdg-open", str(LOG_FILE)])
        except Exception as e:
            messagebox.showinfo(self.tr.t("log_file_title"), f"{LOG_FILE}\n\n{e}")

    # ------------------------------------------------------- Download core --

    def _run_download(self, url: str, file_name: str, n_workers: int):
        session = make_session()
        self.start_time = time.monotonic()

        try:
            info = inspect_url(session, url, self.log)
        except Exception as e:
            self.log_q.put(self.tr.t("error_checking_url", error=e))
            self._finish(success=False)
            return

        self.info = info
        self.log_q.put(self.tr.t(
            "size_range_type_msg",
            size=human_size(info.total_bytes),
            range=self.tr.t("range_yes") if info.accepts_range else self.tr.t("range_no"),
            type=info.content_type,
        ))

        if not info.accepts_range or info.total_bytes < MIN_SPLIT_SIZE:
            chunks = [Chunk(index=0, start=0, end=max(info.total_bytes - 1, 0))]
        else:
            chunks, offset, idx = [], 0, 0
            while offset < info.total_bytes:
                end = min(offset + CHUNK_PART_SIZE - 1, info.total_bytes - 1)
                chunks.append(Chunk(index=idx, start=offset, end=end))
                offset = end + 1
                idx += 1

        self.state = DownloadState(
            url=url, file_name=file_name, total_bytes=info.total_bytes,
            n_workers=n_workers, chunks=chunks,
        )

        try:
            with open(file_name, "wb") as f:
                if info.total_bytes > 0:
                    f.seek(info.total_bytes - 1)
                    f.write(b"\x00")
        except OSError as e:
            self.log_q.put(self.tr.t("error_create_file", error=e))
            self._finish(success=False)
            return

        self.log_q.put(self.tr.t(
            "starting_download",
            file=file_name, chunks=len(chunks), mb=CHUNK_PART_SIZE // 1024 // 1024, workers=n_workers,
        ))

        file_lock = threading.Lock()
        chunk_q: "queue.Queue[Chunk]" = queue.Queue()
        for c in chunks:
            chunk_q.put(c)

        with ThreadPoolExecutor(max_workers=min(n_workers, len(chunks))) as executor:
            futures = [
                executor.submit(
                    worker, i, session, self.state, chunk_q, file_lock, self.log, self.stop_event, self.log_q, self.tr
                )
                for i in range(min(n_workers, len(chunks)))
            ]
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception as e:
                    self.log_q.put(self.tr.t("worker_finished_error", error=e))

        if self.stop_event.is_set():
            self.log_q.put(self.tr.t("cancelled_by_user"))
            self._finish(success=False)
            return

        failed = [c.index for c in self.state.chunks if not c.done]
        if failed:
            self.log_q.put(self.tr.t("chunks_incomplete", count=len(failed), list=failed[:10]))
            self._finish(success=False)
            return

        elapsed = time.monotonic() - self.start_time
        avg = info.total_bytes / elapsed / 1e6 if elapsed > 0 else 0
        self.log_q.put(self.tr.t(
            "download_complete",
            file=file_name, size=human_size(info.total_bytes), elapsed=f"{elapsed:.1f}", speed=f"{avg:.2f}",
        ))
        self._finish(success=True)

    def _finish(self, success: bool):
        self.running = False
        self.btn_start.configure(state="normal")
        self.btn_cancel.configure(state="disabled")
        self.status_var.set(self.tr.t("status_done") if success else self.tr.t("status_interrupted"))

    # ----------------------------------------------------------- Pollers ----

    def _poll_log(self):
        try:
            while True:
                msg = self.log_q.get_nowait()
                self._append_log(msg)
        except queue.Empty:
            pass
        self.after(150, self._poll_log)

    def _poll_progress(self):
        if self.state is not None and self.info is not None and self.info.total_bytes > 0:
            done = self.state.bytes_downloaded
            total = self.info.total_bytes
            pct = min(100.0, done / total * 100)
            self.progress.configure(value=pct)

            elapsed = time.monotonic() - self.start_time if self.start_time else 0
            speed = done / elapsed / 1e6 if elapsed > 0 else 0
            remaining = (total - done) / (speed * 1e6) if speed > 0 else 0

            self.status_var.set(self.tr.t(
                "status_progress", done=human_size(done), total=human_size(total), pct=f"{pct:.1f}",
            ))
            self.stats_var.set(self.tr.t(
                "stats_line", speed=f"{speed:.2f}", elapsed=f"{elapsed:.0f}", remaining=f"{remaining:.0f}",
            ))
        self.after(300, self._poll_progress)


def main():
    app = RapidGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
