"""
RAPID — Reliable Asynchronous Parallel Internet Downloader
"""

from __future__ import annotations

import colorsys
import configparser
import importlib
import importlib.util
import logging
import subprocess
import sys
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# Early logging — must be FIRST so crashes are captured
# ─────────────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "rapid.log"
LANGUAGES_DIR = BASE_DIR / "languages"
DEFAULT_LANG = "en"
THEMES_DIR = BASE_DIR / "themes"
DEFAULT_THEME = "dark"
CONFIG_FILE = BASE_DIR / "config.cfg"


class _FlushRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler that fsyncs after every emit for real-time persistence."""

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        try:
            if self.stream:
                self.stream.flush()
                try:
                    os.fsync(self.stream.fileno())
                except Exception:
                    pass
        except Exception:
            pass


def setup_logging(debug: bool = False, clear: bool = False) -> logging.Logger:
    log = logging.getLogger("rapid_gui")
    log.setLevel(logging.DEBUG if debug else logging.INFO)
    for h in list(log.handlers):
        try:
            h.close()
        except Exception:
            pass
    log.handlers.clear()
    log.propagate = False

    if clear:
        try:
            LOG_FILE.write_text("", encoding="utf-8")
        except Exception:
            pass
        for p in LOG_FILE.parent.glob(f"{LOG_FILE.name}.*"):
            try:
                p.unlink()
            except Exception:
                pass

    fh = _FlushRotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8", delay=False
    )
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    fh.setLevel(logging.DEBUG)
    log.addHandler(fh)
    return log


_EARLY_LOG = setup_logging(clear=True)


def _install_crash_handlers(log: logging.Logger) -> None:
    orig_excepthook = sys.excepthook

    def _excepthook(exc_type, exc_value, exc_tb):
        try:
            log.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_tb))
            for h in log.handlers:
                try:
                    h.flush()
                    if hasattr(h.stream, "fileno"):
                        os.fsync(h.stream.fileno())
                except Exception:
                    pass
        except Exception:
            pass
        orig_excepthook(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook

    _thr = sys.modules.get("threading")
    orig_thread_hook = getattr(_thr, "excepthook", None) if _thr is not None else None

    def _thread_excepthook(args):
        try:
            log.critical(
                f"Uncaught exception in thread {args.thread.name if args.thread else '?'}",
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
            for h in log.handlers:
                try:
                    h.flush()
                    if hasattr(h.stream, "fileno"):
                        os.fsync(h.stream.fileno())
                except Exception:
                    pass
        except Exception:
            pass
        if orig_thread_hook:
            try:
                orig_thread_hook(args)
            except Exception:
                pass
        else:
            import traceback

            traceback.print_exception(args.exc_type, args.exc_value, args.exc_traceback)

    try:
        import threading

        threading.excepthook = _thread_excepthook
    except Exception:
        pass


try:
    import threading

    _install_crash_handlers(_EARLY_LOG)
    _EARLY_LOG.info("=== RAPID started ===")
except Exception:
    pass

# ─────────────────────────────────────────────────────────────────────────────
# Self-installing dependencies
# ─────────────────────────────────────────────────────────────────────────────

_THIRD_PARTY_PACKAGES = {
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
        _EARLY_LOG.debug("All third-party dependencies present.")
        return

    msg = f"Missing dependencies: {', '.join(missing)}. Installing…"
    print(f"[RAPID] {msg}")
    _EARLY_LOG.info(msg)
    try:
        subprocess.check_call([
            sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", *missing,
        ])
    except Exception as e:
        err = f"Automatic install failed: {e}"
        print(f"[RAPID] {err}")
        _EARLY_LOG.critical(err, exc_info=True)
        print(f"[RAPID] Please install manually:\n    {sys.executable} -m pip install {' '.join(missing)}")
        sys.exit(1)

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
        err = f"Still missing after install attempt: {', '.join(still_missing)}"
        print(f"[RAPID] {err}")
        _EARLY_LOG.critical(err)
        print(f"[RAPID] Please install manually:\n    {sys.executable} -m pip install {' '.join(still_missing)}")
        sys.exit(1)

    ok = "Dependencies installed successfully."
    print(f"[RAPID] {ok}")
    _EARLY_LOG.info(ok)


def _check_tkinter() -> None:
    if importlib.util.find_spec("tkinter") is None:
        err = (
            "The 'tkinter' module is missing from this Python install.\n"
            "        pip cannot install it — it ships with the Python interpreter itself.\n"
            "        Debian/Ubuntu:  sudo apt install python3-tk\n"
            "        Fedora:         sudo dnf install python3-tkinter\n"
            "        macOS (brew):   brew install python-tk\n"
            "        Windows:        reinstall Python from python.org with the\n"
            "                        \"tcl/tk and IDLE\" option checked."
        )
        print(f"[RAPID] {err}")
        _EARLY_LOG.critical(err)
        sys.exit(1)


_check_tkinter()
_ensure_dependencies()

import json
import locale
import queue
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
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
CHUNK_SIZE      = 1  * 1024 * 1024
CHUNK_PART_SIZE = 64 * 1024 * 1024
MIN_SPLIT_SIZE  = 8  * 1024 * 1024
MAX_RETRIES     = 6
CONNECT_TIMEOUT = 15
READ_TIMEOUT    = 45
WRITE_BUF_SIZE  = 4  * 1024 * 1024



# ─────────────────────────────────────────────────────────────────────────────
# i18n — translation loader
# ─────────────────────────────────────────────────────────────────────────────

class Translator:
    """
    Loads *.language files from LANGUAGES_DIR (flat JSON key -> string).
    Each file's code is its filename stem (e.g. "pt-br.language" -> "pt-br").
    English is now loaded from languages/en.language like any other language.
    Fallback is DEFAULT_LANG catalog, then key name.
    """

    def __init__(self, languages_dir: Path, log: Optional[logging.Logger] = None):
        self.languages_dir = languages_dir
        self.log = log
        self.catalogs: dict[str, dict[str, str]] = {}
        self.display_names: dict[str, str] = {}
        self.current = DEFAULT_LANG
        self._discover()
        if not self.catalogs:
            self.catalogs[DEFAULT_LANG] = {}
            self.display_names[DEFAULT_LANG] = "English"

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
            catalog = {k: str(v) for k, v in data.items() if k != "_meta_name"}
            self.catalogs[code] = catalog
            self.display_names[code] = display_name

    def available(self) -> list[tuple[str, str]]:
        """Returns list of (code, display_name), English first, then alphabetical."""
        codes = sorted(self.catalogs.keys(), key=lambda c: (c != "en", self.display_names.get(c, c)))
        return [(c, self.display_names.get(c, c)) for c in codes]

    def set_language(self, code: str) -> None:
        code = code.lower()
        if code in self.catalogs:
            self.current = code
        elif DEFAULT_LANG in self.catalogs:
            self.current = DEFAULT_LANG
        elif self.catalogs:
            self.current = next(iter(self.catalogs))
        else:
            self.current = DEFAULT_LANG

    def t(self, key: str, **kwargs) -> str:
        catalog = self.catalogs.get(self.current, {})
        template = catalog.get(key)
        if template is None:
            template = self.catalogs.get(DEFAULT_LANG, {}).get(key, key)
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
            for getter in (locale.getlocale, locale.getdefaultlocale):
                try:
                    loc = getter()
                except Exception:
                    continue
                if loc and loc[0]:
                    candidates.append(loc[0])
        except Exception:
            pass

        for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
            val = os.environ.get(var)
            if val:
                candidates.append(val.split(":")[0])

        available = set(self.catalogs.keys())

        for raw in candidates:
            if not raw:
                continue
            norm = raw.split(".")[0].replace("_", "-").lower()
            if norm in available:
                return norm
            primary = norm.split("-")[0]
            if primary in available:
                return primary
            for code in available:
                if code.split("-")[0] == primary:
                    return code

        return DEFAULT_LANG


# ─────────────────────────────────────────────────────────────────────────────
# Theming — theme loader
# ─────────────────────────────────────────────────────────────────────────────

_BUILTIN_LIGHT = {
    "_meta_name": "Light",
    "background_color":        "#f0f0f0",
    "text_color":               "#000000",
    "secondary_text_color":     "#555555",
    "border_color":             "#b5b5b5",
    "button_background_color":  "#e6e6e6",
    "button_text_color":        "#000000",
    "input_background_color":   "#ffffff",
    "input_text_color":         "#000000",
    "accent_color":             "#3a7bd5",
    "log_background_color":     "#111111",
    "log_text_color":           "#dddddd",
}

THEME_KEYS = [k for k in _BUILTIN_LIGHT if k != "_meta_name"]



CYCLE_KEYS = ["cycle_interval_ms", "cycle_step_deg"]


class ThemeManager:
    """
    Loads *.theme files from THEMES_DIR (flat JSON key -> hex color string).
    Each file's code is its filename stem (e.g. "dark.theme" -> "dark").
    The built-in "light" theme is always available and is used as the
    fallback for any missing file or missing key in any theme.
    """

    def __init__(self, themes_dir: Path, log: Optional[logging.Logger] = None):
        self.themes_dir = themes_dir
        self.log = log
        self.catalogs: dict[str, dict[str, str]] = {"light": dict(_BUILTIN_LIGHT)}
        self.display_names: dict[str, str] = {"light": "Light"}

        self.cycle_params: dict[str, dict[str, float]] = {}
        self.current = DEFAULT_THEME
        self._discover()

    def _discover(self) -> None:
        if not self.themes_dir.is_dir():
            return
        for path in sorted(self.themes_dir.glob("*.theme")):
            code = path.stem.strip().lower()
            if not code:
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("root JSON element must be an object")
            except Exception as e:
                if self.log:
                    self.log.warning(f"Failed to load theme file '{path.name}': {e}")
                continue

            display_name = str(data.get("_meta_name", code))
            merged = dict(_BUILTIN_LIGHT)
            merged.update({k: v for k, v in data.items() if k in THEME_KEYS})
            self.catalogs[code] = merged
            self.display_names[code] = display_name

            cycle_cfg: dict[str, float] = {}
            for ck in CYCLE_KEYS:
                if ck in data:
                    try:

                        cycle_cfg[ck] = float(data[ck])
                    except Exception:
                        if self.log:
                            self.log.warning(f"Invalid {ck} in theme '{code}': {data[ck]!r}")
            if cycle_cfg:
                self.cycle_params[code] = cycle_cfg

    def available(self) -> list[tuple[str, str]]:
        """Returns list of (code, display_name) ordered for preview:

        Light is always first (default), RGB Cycle always last (animated),
        then remaining themes sorted by background luminance (bright → dark).
        This groups light themes together and dark themes together, so
        arrow Up/Down and hover preview feel like a smooth light-to-dark
        sweep instead of random alphabetical jumps.
        """
        def _lum(code: str) -> float:
            hx = self.catalogs[code].get("background_color", "#808080").lstrip("#")
            try:
                r = int(hx[0:2], 16)
                g = int(hx[2:4], 16)
                b = int(hx[4:6], 16)
                return 0.299 * r + 0.587 * g + 0.114 * b
            except Exception:
                return 0.0


        others = [c for c in self.catalogs.keys() if c not in ("light", "rgb")]
        others_sorted = sorted(others, key=lambda c: -_lum(c))
        codes: list[str] = []
        if "light" in self.catalogs:
            codes.append("light")
        codes.extend(others_sorted)
        if "rgb" in self.catalogs:
            codes.append("rgb")
        return [(c, self.display_names[c]) for c in codes]

    def set_theme(self, code: str) -> None:
        code = code.lower()
        self.current = code if code in self.catalogs else DEFAULT_THEME

    def colors(self) -> dict[str, str]:
        return self.catalogs.get(self.current, _BUILTIN_LIGHT)


# ─────────────────────────────────────────────────────────────────────────────
# App config — persists theme / language to config.cfg
# ─────────────────────────────────────────────────────────────────────────────

def load_app_config(log: Optional[logging.Logger] = None) -> dict[str, str]:
    """Load Preferences from CONFIG_FILE. Returns dict with optional keys:
    'theme', 'language'. Missing/invalid file -> empty dict."""
    if not CONFIG_FILE.is_file():
        return {}
    parser = configparser.ConfigParser()
    try:
        parser.read(CONFIG_FILE, encoding="utf-8")
        if parser.has_section("Preferences"):
            cfg: dict[str, str] = {}
            for key in ("theme", "language"):
                if parser.has_option("Preferences", key):
                    val = parser.get("Preferences", key).strip()
                    if val:
                        cfg[key] = val
            return cfg


        text = CONFIG_FILE.read_text(encoding="utf-8")
        if "=" in text and "[" not in text:
            parser2 = configparser.ConfigParser()
            parser2.read_string("[Preferences]\n" + text)
            cfg = {}
            for key in ("theme", "language"):
                if parser2.has_option("Preferences", key):
                    val = parser2.get("Preferences", key).strip()
                    if val:
                        cfg[key] = val
            return cfg
    except Exception as e:
        if log:
            log.warning(f"Failed to load config.cfg: {e}")
    return {}


def save_app_config(
    *,
    theme: Optional[str] = None,
    language: Optional[str] = None,
    log: Optional[logging.Logger] = None,
) -> None:
    """Persist given preferences to CONFIG_FILE. Only provided values are updated;
    existing unrelated keys are preserved. Atomic write via temp file + replace."""
    parser = configparser.ConfigParser()
    if CONFIG_FILE.is_file():
        try:
            parser.read(CONFIG_FILE, encoding="utf-8")
        except Exception:
            parser = configparser.ConfigParser()
    if not parser.has_section("Preferences"):
        parser.add_section("Preferences")
    if theme is not None:
        parser.set("Preferences", "theme", theme.strip().lower())
    if language is not None:
        parser.set("Preferences", "language", language.strip().lower())
    try:
        tmp = CONFIG_FILE.with_suffix(".cfg.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            parser.write(f)
        tmp.replace(CONFIG_FILE)
        if log:
            log.info(f"Config saved to {CONFIG_FILE.name}: theme={theme} language={language}")
    except Exception as e:
        if log:
            log.warning(f"Failed to save config.cfg: {e}")


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

        try:
            self.log = setup_logging(clear=True)
            self.log.info("GUI initialized")
        except NameError:
            self.log = setup_logging(clear=True)
            self.log.info("GUI initialized")
        self.tr = Translator(LANGUAGES_DIR, self.log)
        self.th = ThemeManager(THEMES_DIR, self.log)
        self._load_preferences()
        self.style = ttk.Style(self)
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass

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



        self._rgb_hue: float = 200.0
        self._rgb_job: Optional[str] = None
        self._rgb_interval_ms: int = int(self.th.cycle_params.get("rgb", {}).get("cycle_interval_ms", 80))
        self._rgb_step_deg: float = float(self.th.cycle_params.get("rgb", {}).get("cycle_step_deg", 0.5))

        self._rgb_interval_ms = max(10, min(1000, self._rgb_interval_ms))
        self._rgb_step_deg = max(0.05, min(10.0, self._rgb_step_deg))

        self._build_ui()
        self._apply_theme()
        self.after(150, self._poll_log)
        self.after(200, self._poll_progress)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── UI ──

    def _build_ui(self):
        pad = {"padx": 6, "pady": 4}

        frm_lang = ttk.Frame(self)
        frm_lang.pack(fill="x", padx=6, pady=(6, 0))
        self.lbl_lang = ttk.Label(frm_lang, text=self.tr.t("language_label"))
        self.lbl_lang.pack(side="left")

        self._lang_codes = self.tr.available()
        self.lang_var = tk.StringVar(value=dict(self._lang_codes).get(self.tr.current, "English"))
        lang_combo = ttk.Combobox(
            frm_lang, textvariable=self.lang_var, state="readonly",
            values=[name for _, name in self._lang_codes], width=24,
        )
        lang_combo.pack(side="left", padx=(6, 0))
        lang_combo.bind("<<ComboboxSelected>>", self._on_language_change)

        self.lbl_theme = ttk.Label(frm_lang, text=self.tr.t("theme_label"))
        self.lbl_theme.pack(side="left", padx=(18, 0))

        self._theme_codes = self.th.available()
        self.theme_var = tk.StringVar(value=dict(self._theme_codes).get(self.th.current, "Light"))
        theme_combo = ttk.Combobox(
            frm_lang, textvariable=self.theme_var, state="readonly",
            values=[name for _, name in self._theme_codes], width=16,
        )
        theme_combo.pack(side="left", padx=(6, 0))
        theme_combo.bind("<<ComboboxSelected>>", self._on_theme_change)
        # ── theme live preview (arrow keys / mouse hover) ──
        self.theme_combo = theme_combo
        self._committed_theme = self.th.current
        self._theme_listbox = None

        theme_combo.bind("<Button-1>", lambda _e: self.after(20, self._hook_theme_listbox), add="+")
        theme_combo.bind("<KeyPress>", self._on_theme_combo_keypress_hook, add="+")
        for _seq in ("<KeyRelease-Up>", "<KeyRelease-Down>", "<KeyRelease-Prior>",
                     "<KeyRelease-Next>", "<KeyRelease-Home>", "<KeyRelease-End>"):
            theme_combo.bind(_seq, self._on_theme_key_preview, add="+")
        theme_combo.bind("<Escape>", self._on_theme_preview_revert, add="+")
        theme_combo.bind("<FocusOut>", self._on_theme_focus_out, add="+")

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
        self.lbl_info = ttk.Label(self.frm_top, textvariable=self.info_var, style="Muted.TLabel")
        self.lbl_info.grid(row=2, column=2, columnspan=3, sticky="w", **pad)

        self.frm_top.columnconfigure(1, weight=1)

        frm_actions = ttk.Frame(self)
        frm_actions.pack(fill="x", **pad)

        self.btn_start = ttk.Button(frm_actions, text=self.tr.t("download_btn"), command=self._on_start)
        self.btn_start.pack(side="left", padx=4)

        self.btn_cancel = ttk.Button(frm_actions, text=self.tr.t("cancel_btn"), command=self._on_cancel, state="disabled")
        self.btn_cancel.pack(side="left", padx=4)

        self.btn_open_log = ttk.Button(frm_actions, text=self.tr.t("open_log_btn"), command=self._open_log)
        self.btn_open_log.pack(side="right", padx=4)

        self.frm_prog = ttk.LabelFrame(self, text=self.tr.t("progress_frame"))
        self.frm_prog.pack(fill="x", **pad)

        self.progress = ttk.Progressbar(self.frm_prog, orient="horizontal", mode="determinate", length=600)
        self.progress.pack(fill="x", padx=6, pady=(6, 2))

        self.status_var = tk.StringVar(value=self.tr.t("status_ready"))
        ttk.Label(self.frm_prog, textvariable=self.status_var).pack(anchor="w", padx=6, pady=(0, 6))

        self.frm_log = ttk.LabelFrame(self, text=self.tr.t("log_frame"))
        self.frm_log.pack(fill="both", expand=True, **pad)

        self.log_text = tk.Text(self.frm_log, height=16,
                                 font=("Consolas", 9) if self._font_ok("Consolas") else ("Courier", 9))
        self.log_text.pack(fill="both", expand=True, side="left", padx=(6, 0), pady=6)
        scroll = ttk.Scrollbar(self.frm_log, command=self.log_text.yview)
        scroll.pack(side="right", fill="y", pady=6, padx=(0, 6))
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.configure(state="disabled")

        frm_stats = ttk.Frame(self)
        frm_stats.pack(fill="x", **pad)
        self.stats_var = tk.StringVar(value="")
        self.lbl_stats = ttk.Label(frm_stats, textvariable=self.stats_var, style="Muted.TLabel")
        self.lbl_stats.pack(anchor="w")

    def _font_ok(self, name: str) -> bool:
        try:
            import tkinter.font as tkfont
            return name in tkfont.families()
        except Exception:
            return False

    # ── Preferences persistence ──

    def _load_preferences(self) -> None:
        """Load saved theme/language from config.cfg. Falls back to autodetect/default."""
        cfg = load_app_config(self.log)

        saved_theme = cfg.get("theme", "").strip().lower()
        if saved_theme:
            if saved_theme in self.th.catalogs:
                self.th.set_theme(saved_theme)
                self.log.info(f"Loaded theme from config.cfg: {saved_theme}")
            else:
                self.log.warning(f"Saved theme '{saved_theme}' not found in {list(self.th.catalogs.keys())}, using default")

        saved_lang = cfg.get("language", "").strip().lower()
        if saved_lang:
            if saved_lang in self.tr.catalogs:
                self.tr.set_language(saved_lang)
                self.log.info(f"Loaded language from config.cfg: {saved_lang}")
            else:
                self.log.warning(f"Saved language '{saved_lang}' not found, using autodetect")
                self.tr.set_language(self.tr.autodetect())
        else:
            self.tr.set_language(self.tr.autodetect())

    # ── i18n ──

    def _on_language_change(self, _event=None):
        name_to_code = {name: code for code, name in self._lang_codes}
        code = name_to_code.get(self.lang_var.get(), DEFAULT_LANG)
        self.tr.set_language(code)
        save_app_config(language=code, log=self.log)
        self._retranslate_static_ui()

    def _retranslate_static_ui(self):
        """Updates all static widget texts after a language change (dynamic
        log/status text keeps whatever language it was generated in)."""
        self.title(self.tr.t("window_title"))
        self.lbl_lang.configure(text=self.tr.t("language_label"))
        self.lbl_theme.configure(text=self.tr.t("theme_label"))
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

    # ── Theming ──

    def _on_theme_change(self, _event=None):
        name_to_code = {name: code for code, name in self._theme_codes}
        code = name_to_code.get(self.theme_var.get(), DEFAULT_THEME)
        self.th.set_theme(code)
        save_app_config(theme=code, log=self.log)
        self._committed_theme = code
        self._apply_theme()

    # ── Theme live preview (arrow keys / mouse hover) ──

    def _preview_theme(self, code: str) -> None:
        """Apply theme immediately without persisting to config.cfg (preview)."""
        if code not in self.th.catalogs:
            return
        if code == self.th.current:
            return
        self.th.set_theme(code)
        self._apply_theme()

    def _get_theme_listbox(self):
        """Return a wrapper for the internal Listbox widget of the theme Combobox popdown, or None.

        The popdown Listbox is created at Tcl level by ttk::combobox, so
        tkinter's nametowidget cannot find it. We wrap the existing Tcl
        window path manually (tk.Listbox.__new__) so Python bindings work.
        """
        try:
            pop = self.tk.call("ttk::combobox::PopdownWindow", str(self.theme_combo))
            lb_path = f"{pop}.f.l"
            if str(self.tk.call("winfo", "exists", lb_path)) != "1":
                return None
            lb = tk.Listbox.__new__(tk.Listbox)  # type: ignore
            lb.master = self  # type: ignore
            lb._w = lb_path  # type: ignore
            lb.tk = self.tk  # type: ignore
            return lb
        except Exception:
            pass
        return None

    def _get_theme_popdown(self):
        """Return a wrapper for the popdown Toplevel, or None."""
        try:
            pop = self.tk.call("ttk::combobox::PopdownWindow", str(self.theme_combo))
            if str(self.tk.call("winfo", "exists", pop)) != "1":
                return None
            w = tk.Toplevel.__new__(tk.Toplevel)  # type: ignore
            w.master = self  # type: ignore
            w._w = pop  # type: ignore
            w.tk = self.tk  # type: ignore
            return w
        except Exception:
            return None

    def _hook_theme_listbox(self) -> None:
        lb = self._get_theme_listbox()
        if lb is None:

            self.after(40, self._hook_theme_listbox)
            return
        lb_path = lb._w  # type: ignore

        hooked = getattr(self, "_theme_lb_hooked_paths", set())
        first_time = lb_path not in hooked
        if first_time:
            try:
                lb.bind("<Motion>", self._on_theme_listbox_hover, add="+")
            except Exception:

                try:
                    self.tk.call("bind", lb_path, "<Motion>", f"+{self._on_theme_listbox_hover}")
                except Exception:
                    pass
            hooked.add(lb_path)
            self._theme_lb_hooked_paths = hooked  # type: ignore
            self._theme_listbox = lb

            try:
                pop_w = self._get_theme_popdown()
                if pop_w is not None:
                    pop_w.bind("<Unmap>", self._on_theme_popdown_unmap, add="+")
            except Exception:
                pass

        self._start_theme_preview_poll()

    def _on_theme_combo_keypress_hook(self, event=None) -> None:

        if event is not None and event.keysym in ("Up", "Down", "Next", "Prior", "Home", "End", "F4", "Alt_L", "Alt_R"):
            self.after(20, self._hook_theme_listbox)

    def _on_theme_listbox_hover(self, event) -> None:
        lb = event.widget
        try:
            idx = lb.nearest(event.y)
            if idx < 0:
                return
            name = lb.get(idx)
        except Exception:
            return
        name_to_code = {name: code for code, name in self._theme_codes}
        code = name_to_code.get(name)
        if code:
            self._preview_theme(code)

    def _on_theme_key_preview(self, _event=None) -> None:

        self.after(10, self._do_theme_key_preview)

    def _do_theme_key_preview(self) -> None:
        code = None
        lb = self._get_theme_listbox()
        if lb is not None:
            try:
                if lb.winfo_ismapped():
                    sel = lb.curselection()
                    if sel:
                        name = lb.get(sel[0])
                    else:
                        idx = lb.index("active")
                        name = lb.get(idx)
                    name_to_code = {name: code for code, name in self._theme_codes}
                    code = name_to_code.get(name)
            except Exception:
                code = None
        if code is None:
            name = self.theme_var.get()
            name_to_code = {name: code for code, name in self._theme_codes}
            code = name_to_code.get(name)
        if code:
            self._preview_theme(code)

    def _on_theme_preview_revert(self, _event=None) -> None:
        if self.th.current != self._committed_theme:
            self.th.set_theme(self._committed_theme)
            committed_name = dict(self._theme_codes).get(self._committed_theme, self._committed_theme)

            self.theme_var.set(committed_name)
            self._apply_theme()

    def _on_theme_popdown_unmap(self, _event=None) -> None:



        pass

    def _start_theme_preview_poll(self) -> None:
        """Poll active Listbox index while popdown is mapped to catch arrow/hover."""
        self._poll_theme_preview()

    def _poll_theme_preview(self) -> None:
        try:
            pop = self._get_theme_popdown()
            if pop is None or not pop.winfo_ismapped():  # type: ignore
                return
            lb = self._get_theme_listbox()
            if lb is None:
                return

            try:
                if lb.size() == 0:  # type: ignore
                    self.after(80, self._poll_theme_preview)
                    return

                idx = lb.index("active")  # type: ignore
                name = lb.get(idx)  # type: ignore
                name_to_code = {name: code for code, name in self._theme_codes}
                code = name_to_code.get(name)
                if code and code != self.th.current:
                    self._preview_theme(code)
            except Exception:
                pass

            if pop.winfo_ismapped():  # type: ignore
                self.after(80, self._poll_theme_preview)
        except Exception:
            pass

    def _on_theme_focus_out(self, _event=None) -> None:



        def _check():
            try:
                focused = self.focus_get()
            except Exception:
                focused = None
            lb = self._get_theme_listbox()

            try:
                focused_path = str(focused) if focused is not None else ""
                lb_path = lb._w if lb is not None else ""  # type: ignore
                combo_path = str(self.theme_combo)
                if focused_path in (combo_path, lb_path):
                    return
            except Exception:
                if focused is not None and (focused == self.theme_combo or focused == lb):
                    return

            if lb is not None:
                try:
                    if lb.winfo_ismapped():
                        return
                except Exception:
                    pass

            committed_name = dict(self._theme_codes).get(self._committed_theme, self._committed_theme)
            if self.theme_var.get() == committed_name and self.th.current != self._committed_theme:
                self._on_theme_preview_revert()
        self.after(150, _check)

    def _apply_theme(self):
        c = self.th.colors()

        self.configure(bg=c["background_color"])

        self.style.configure(".", background=c["background_color"], foreground=c["text_color"])
        self.style.configure("TFrame", background=c["background_color"])

        self.style.configure(
            "TLabelframe", background=c["background_color"], foreground=c["text_color"],
            bordercolor=c["border_color"], darkcolor=c["border_color"], lightcolor=c["border_color"],
        )
        self.style.configure("TLabelframe.Label", background=c["background_color"], foreground=c["text_color"])
        self.style.configure("TLabel", background=c["background_color"], foreground=c["text_color"])
        self.style.configure("Muted.TLabel", background=c["background_color"], foreground=c["secondary_text_color"])

        self.style.configure(
            "TButton", background=c["button_background_color"], foreground=c["button_text_color"],
            bordercolor=c["border_color"], darkcolor=c["button_background_color"], lightcolor=c["button_background_color"],
        )
        self.style.map(
            "TButton",
            background=[("active", c["accent_color"]), ("disabled", c["button_background_color"])],
            foreground=[("disabled", c["secondary_text_color"])],
            bordercolor=[("disabled", c["border_color"])],
        )

        self.style.configure(
            "TEntry", fieldbackground=c["input_background_color"], foreground=c["input_text_color"],
            bordercolor=c["border_color"], darkcolor=c["border_color"], lightcolor=c["border_color"],
            insertcolor=c["input_text_color"],
        )

        self.style.configure(
            "TSpinbox", fieldbackground=c["input_background_color"], foreground=c["input_text_color"],
            background=c["button_background_color"], bordercolor=c["border_color"],
            darkcolor=c["border_color"], lightcolor=c["border_color"], arrowcolor=c["text_color"],
            insertcolor=c["input_text_color"],
        )

        self.style.configure(
            "TCombobox", fieldbackground=c["input_background_color"], foreground=c["input_text_color"],
            background=c["button_background_color"], bordercolor=c["border_color"],
            darkcolor=c["border_color"], lightcolor=c["border_color"], arrowcolor=c["text_color"],
        )
        self.style.map(
            "TCombobox",
            fieldbackground=[("readonly", c["input_background_color"])],
            foreground=[("readonly", c["input_text_color"])],
            background=[("readonly", c["button_background_color"])],
        )
        self.option_add("*TCombobox*Listbox.background", c["input_background_color"])
        self.option_add("*TCombobox*Listbox.foreground", c["input_text_color"])
        self.option_add("*TCombobox*Listbox.selectBackground", c["accent_color"])
        self.option_add("*TCombobox*Listbox.selectForeground", c["input_background_color"])

        self.style.configure(
            "TProgressbar", background=c["accent_color"], troughcolor=c["button_background_color"],
            bordercolor=c["border_color"], darkcolor=c["accent_color"], lightcolor=c["accent_color"],
        )

        self.style.configure(
            "TScrollbar", background=c["button_background_color"], troughcolor=c["background_color"],
            bordercolor=c["border_color"], arrowcolor=c["text_color"],
        )
        self.style.map("TScrollbar", background=[("active", c["accent_color"])])

        self.log_text.configure(
            bg=c["log_background_color"], fg=c["log_text_color"], insertbackground=c["log_text_color"],
        )


        if self.th.current == "rgb":
            self._start_rgb_cycle()
        else:
            self._stop_rgb_cycle()

    # ── RGB Cycle theme — slow and steady ──

    @staticmethod
    def _hsv_to_hex(h: float, s: float, v: float) -> str:
        """h in 0-360, s/v in 0-1 → #rrggbb."""
        h = (h % 360) / 360.0
        r, g, b = colorsys.hsv_to_rgb(h, max(0, min(1, s)), max(0, min(1, v)))
        return f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"

    def _update_rgb_catalog(self) -> None:
        """Mutate the rgb catalog in-place based on current hue."""
        h = self._rgb_hue % 360

        cat = self.th.catalogs.get("rgb")
        if cat is None:
            return
        cat["background_color"]       = self._hsv_to_hex(h, 0.55, 0.12)
        cat["border_color"]           = self._hsv_to_hex(h, 0.35, 0.32)
        cat["button_background_color"] = self._hsv_to_hex(h, 0.45, 0.20)
        cat["input_background_color"]  = self._hsv_to_hex(h, 0.40, 0.16)
        cat["accent_color"]           = self._hsv_to_hex(h, 0.85, 1.0)
        cat["log_background_color"]   = self._hsv_to_hex(h, 0.60, 0.07)
        cat["log_text_color"]         = self._hsv_to_hex((h + 180) % 360, 0.15, 0.95)

        cat["text_color"]             = "#e6e6ff"
        cat["secondary_text_color"]   = self._hsv_to_hex(h, 0.18, 0.72)
        cat["button_text_color"]      = "#e6e6ff"
        cat["input_text_color"]       = "#e6e6ff"

    def _refresh_rgb_params(self) -> None:
        """Reload interval/step from rgb.theme's cycle_interval_ms / cycle_step_deg if present."""
        cfg = self.th.cycle_params.get("rgb", {})
        try:
            interval = int(float(cfg.get("cycle_interval_ms", self._rgb_interval_ms)))
        except Exception:
            interval = self._rgb_interval_ms
        try:
            step = float(cfg.get("cycle_step_deg", self._rgb_step_deg))
        except Exception:
            step = self._rgb_step_deg
        self._rgb_interval_ms = max(10, min(1000, interval))
        self._rgb_step_deg = max(0.05, min(10.0, step))

    def _start_rgb_cycle(self) -> None:
        if self._rgb_job is not None:
            return
        self._refresh_rgb_params()

        self._update_rgb_catalog()
        self._rgb_tick()

    def _stop_rgb_cycle(self) -> None:
        if self._rgb_job is not None:
            try:
                self.after_cancel(self._rgb_job)
            except Exception:
                pass
            self._rgb_job = None

    def _rgb_tick(self) -> None:
        if self.th.current != "rgb":
            self._rgb_job = None
            return
        self._refresh_rgb_params()
        self._rgb_hue = (self._rgb_hue + self._rgb_step_deg) % 360
        self._update_rgb_catalog()

        if self.th.current == "rgb":

            c = self.th.catalogs["rgb"]
            self.configure(bg=c["background_color"])
            self.style.configure(".", background=c["background_color"], foreground=c["text_color"])
            self.style.configure("TFrame", background=c["background_color"])
            self.style.configure("TLabelframe", background=c["background_color"], foreground=c["text_color"],
                                 bordercolor=c["border_color"], darkcolor=c["border_color"], lightcolor=c["border_color"])
            self.style.configure("TLabelframe.Label", background=c["background_color"], foreground=c["text_color"])
            self.style.configure("TLabel", background=c["background_color"], foreground=c["text_color"])
            self.style.configure("Muted.TLabel", background=c["background_color"], foreground=c["secondary_text_color"])
            self.style.configure("TButton", background=c["button_background_color"], foreground=c["button_text_color"],
                                 bordercolor=c["border_color"], darkcolor=c["button_background_color"], lightcolor=c["button_background_color"])
            self.style.map("TButton", background=[("active", c["accent_color"]), ("disabled", c["button_background_color"])],
                           foreground=[("disabled", c["secondary_text_color"])], bordercolor=[("disabled", c["border_color"])])
            self.style.configure("TEntry", fieldbackground=c["input_background_color"], foreground=c["input_text_color"],
                                 bordercolor=c["border_color"], darkcolor=c["border_color"], lightcolor=c["border_color"], insertcolor=c["input_text_color"])
            self.style.configure("TSpinbox", fieldbackground=c["input_background_color"], foreground=c["input_text_color"],
                                 background=c["button_background_color"], bordercolor=c["border_color"], darkcolor=c["border_color"], lightcolor=c["border_color"], arrowcolor=c["text_color"], insertcolor=c["input_text_color"])
            self.style.configure("TCombobox", fieldbackground=c["input_background_color"], foreground=c["input_text_color"],
                                 background=c["button_background_color"], bordercolor=c["border_color"], darkcolor=c["border_color"], lightcolor=c["border_color"], arrowcolor=c["text_color"])
            self.style.map("TCombobox", fieldbackground=[("readonly", c["input_background_color"])], foreground=[("readonly", c["input_text_color"])], background=[("readonly", c["button_background_color"])])
            self.option_add("*TCombobox*Listbox.background", c["input_background_color"])
            self.option_add("*TCombobox*Listbox.foreground", c["input_text_color"])
            self.option_add("*TCombobox*Listbox.selectBackground", c["accent_color"])
            self.option_add("*TCombobox*Listbox.selectForeground", c["input_background_color"])
            self.style.configure("TProgressbar", background=c["accent_color"], troughcolor=c["button_background_color"],
                                 bordercolor=c["border_color"], darkcolor=c["accent_color"], lightcolor=c["accent_color"])
            self.style.configure("TScrollbar", background=c["button_background_color"], troughcolor=c["background_color"],
                                 bordercolor=c["border_color"], arrowcolor=c["text_color"])
            self.style.map("TScrollbar", background=[("active", c["accent_color"])])
            self.log_text.configure(bg=c["log_background_color"], fg=c["log_text_color"], insertbackground=c["log_text_color"])
        self._rgb_job = self.after(self._rgb_interval_ms, self._rgb_tick)

    # ── Actions ──

    def _append_log(self, msg: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        try:
            self.log.info(msg)
        except Exception:
            pass

    def _queue_log(self, msg: str, level: int = logging.INFO) -> None:
        """Enqueue to GUI and write to file immediately — use instead of log_q.put()."""
        try:
            self.log.log(level, msg)
        except Exception:
            pass
        self.log_q.put(msg)

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

        try:
            self._stop_rgb_cycle()
        except Exception:
            pass
        if self.running:
            if not messagebox.askyesno(self.tr.t("confirm_close_title"), self.tr.t("confirm_close_msg")):
                return
            self.stop_event.set()
        try:
            self.log.info("RAPID closed by user")
            for h in self.log.handlers:
                try:
                    h.flush()
                    if hasattr(h, "stream") and hasattr(h.stream, "fileno"):
                        os.fsync(h.stream.fileno())
                except Exception:
                    pass
        except Exception:
            pass
        self.destroy()

    def _open_log(self):
        try:
            LOG_FILE.touch(exist_ok=True)
            for h in self.log.handlers:
                try:
                    h.flush()
                    if hasattr(h, "stream") and hasattr(h.stream, "fileno"):
                        os.fsync(h.stream.fileno())
                except Exception:
                    pass
        except Exception:
            pass
        try:
            if sys.platform.startswith("win"):
                os.startfile(LOG_FILE)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(LOG_FILE)])
            else:
                subprocess.Popen(["xdg-open", str(LOG_FILE)])
        except Exception as e:
            messagebox.showinfo(self.tr.t("log_file_title"), f"{LOG_FILE}\n\n{e}")

    # ── Download core ──

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

    # ── Pollers ──

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
