"""
RAPID — Reliable Asynchronous Parallel Internet Downloader
"""

from __future__ import annotations

import colorsys
import configparser
import hashlib
import importlib
import importlib.util
import logging
import subprocess
import sys
import os
import re
import socket
import ssl
import shutil
from logging.handlers import RotatingFileHandler
from pathlib import Path


if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "rapid.log"
if getattr(sys, "frozen", False):
    LANGUAGES_DIR = Path(sys._MEIPASS) / "languages"
    THEMES_DIR = Path(sys._MEIPASS) / "themes"
else:
    LANGUAGES_DIR = BASE_DIR / "languages"
    THEMES_DIR = BASE_DIR / "themes"
DEFAULT_LANG = "en"
DEFAULT_THEME = "ember"
CONFIG_FILE = BASE_DIR / "config.cfg"


class _FlushRotatingFileHandler(RotatingFileHandler):

    def emit(self, record: logging.LogRecord) -> None:
        
        
        super().emit(record)
        try:
            if self.stream:
                self.stream.flush()
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


_EARLY_LOG = setup_logging(clear=False)


class _GuiQueueHandler(logging.Handler):
    def __init__(self, target_queue: "queue.Queue[str]"):
        super().__init__()
        self.target_queue = target_queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.target_queue.put(self.format(record))
        except Exception:
            pass


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


_THIRD_PARTY_PACKAGES = {
    "requests": "requests",
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


if importlib.util.find_spec("truststore") is None:
    try:
        subprocess.check_call([
            sys.executable, "-m", "pip", "install", "--quiet",
            "--disable-pip-version-check", "--timeout", "8", "--retries", "1", "truststore",
        ], timeout=20)
        importlib.invalidate_caches()
    except Exception as _truststore_install_error:
        _EARLY_LOG.warning(
            "Could not install optional truststore integration. The app will use its configured CA bundle; "
            "a custom CA can be supplied through REQUESTS_CA_BUNDLE or SSL_CERT_FILE. "
            f"Reason: {_truststore_install_error}"
        )
try:
    import truststore
    truststore.inject_into_ssl()
    _EARLY_LOG.info("TLS trust: operating-system certificate store enabled (truststore)")
except Exception as _truststore_error:
    _EARLY_LOG.warning(
        "TLS truststore integration unavailable; using Python's configured CA bundle. "
        "A custom CA can be supplied through REQUESTS_CA_BUNDLE or SSL_CERT_FILE. "
        f"Reason: {_truststore_error}"
    )

import json
import locale
import queue
import random
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


_caffeinate_proc = None  


def prevent_sleep(log: "logging.Logger") -> None:
    global _caffeinate_proc
    try:
        if sys.platform.startswith("win"):
            import ctypes
            ES_CONTINUOUS = 0x80000000
            ES_SYSTEM_REQUIRED = 0x00000001
            ES_DISPLAY_REQUIRED = 0x00000002
            ctypes.windll.kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
            )
        elif sys.platform == "darwin":
            if _caffeinate_proc is None or _caffeinate_proc.poll() is not None:
                _caffeinate_proc = subprocess.Popen(
                    ["caffeinate", "-i"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
        else:
            try:
                if _caffeinate_proc is None or _caffeinate_proc.poll() is not None:
                    _caffeinate_proc = subprocess.Popen(
                        [
                            "systemd-inhibit", "--what=idle:sleep",
                            "--who=RAPID", "--why=Active download in progress",
                            "sleep", "infinity",
                        ],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
            except FileNotFoundError:
                _caffeinate_proc = None
    except Exception as e:
        log.debug(f"prevent_sleep: could not inhibit sleep ({e}), continuing without it.")


def allow_sleep(log: "logging.Logger") -> None:
    global _caffeinate_proc
    try:
        if sys.platform.startswith("win"):
            import ctypes
            ES_CONTINUOUS = 0x80000000
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        else:
            if _caffeinate_proc is not None:
                try:
                    _caffeinate_proc.terminate()
                except Exception:
                    pass
                _caffeinate_proc = None
    except Exception as e:
        log.debug(f"allow_sleep: could not release sleep inhibitor ({e}), continuing.")


DEFAULT_WORKERS = 8
MAX_WORKERS     = 32
CHUNK_SIZE      = 1  * 1024 * 1024
CHUNK_PART_SIZE = 64 * 1024 * 1024
MIN_SPLIT_SIZE  = 4  * 1024 * 1024
MAX_RETRIES     = 4
CONNECT_TIMEOUT = 20
READ_TIMEOUT    = 60
WRITE_BUF_SIZE  = 8  * 1024 * 1024
PROBE_RANGE_SIZE = 1023
PROBE_SMALL_SIZE = 1023
MAX_ROUNDS       = 3
UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
]
WAF_BODY_MARKERS = (
    b"checking your browser", b"challenge-platform", b"captcha",
    b"cf-challenge", b"just a moment", b"attention required",
    b"access denied", b"cloudflare ray", b"verify you are human",
)
CHALLENGE_DOMAINS = (
    "challenges.cloudflare.com", "accounts.google.com",
    "www.google.com/recaptcha", "/cdn-cgi/challenge",
)


class Translator:

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


def load_app_config(log: Optional[logging.Logger] = None) -> dict[str, str]:
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


@dataclass
class Chunk:
    index: int
    start: int
    end:   int
    done:  bool = False
    bytes_done: int = 0
    sha256: str = ""

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

    _lock:       threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)
    _bytes_done: int = field(default=0, repr=False, compare=False)
    range_error: Optional[str] = field(default=None, repr=False, compare=False)
    fatal_error: Optional[str] = field(default=None, repr=False, compare=False)
    dns_errors: int = field(default=0, repr=False, compare=False)

    def record_bytes(self, n: int) -> None:
        
        with self._lock:
            self._bytes_done = max(0, self._bytes_done + int(n))

    @property
    def bytes_downloaded(self) -> int:
        with self._lock:
            return self._bytes_done


STATE_SUFFIX = ".rapidstate"


def _state_path(file_name: str) -> Path:
    return Path(str(file_name) + STATE_SUFFIX)


def save_state(state: "DownloadState") -> None:
    path = _state_path(state.file_name)
    try:
        
        
        with state._lock:
            data = {
                "version": 2,
                
                
                "url_sha256": hashlib.sha256(state.url.encode("utf-8")).hexdigest(),
                "file_name": state.file_name,
                "total_bytes": state.total_bytes,
                "chunks": [
                    {
                        "index": c.index,
                        "start": c.start,
                        "end": c.end,
                        "done": bool(c.done),
                        "bytes_done": int(c.bytes_done),
                        "sha256": str(c.sha256 or ""),
                    }
                    for c in state.chunks
                ],
            }
            tmp = path.with_name(
                path.name + f".{os.getpid()}.{threading.get_ident()}.tmp"
            )
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
    except OSError as e:
        _EARLY_LOG.warning(
            f"Could not persist resume state for {Path(state.file_name).name}: {e}"
        )


def load_state(file_name: str) -> Optional[dict]:
    path = _state_path(file_name)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def clear_state(file_name: str) -> None:
    path = _state_path(file_name)
    for candidate in (path, path.with_name(path.name + ".tmp")):
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass
    for candidate in path.parent.glob(path.name + ".*.tmp"):
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass


def _saved_state_matches(saved: Optional[dict], fresh_chunks: list[Chunk], url: str,
                         total_bytes: int, file_name: str) -> bool:
    if not isinstance(saved, dict):
        return False
    try:
        saved_fingerprint = saved.get("url_sha256")
        if saved_fingerprint:
            current_fingerprint = hashlib.sha256(url.encode("utf-8")).hexdigest()
            if saved_fingerprint != current_fingerprint:
                return False
        elif saved.get("url") != url:  
            return False
        if int(saved.get("total_bytes", -1)) != total_bytes:
            return False
        if saved.get("file_name") and Path(str(saved["file_name"])).resolve() != Path(file_name).resolve():
            return False
        old = saved.get("chunks")
        if not isinstance(old, list) or len(old) != len(fresh_chunks):
            return False
        for item, fresh in zip(old, fresh_chunks):
            if not isinstance(item, dict):
                return False
            if (int(item.get("index", -1)) != fresh.index
                    or int(item.get("start", -1)) != fresh.start
                    or int(item.get("end", -1)) != fresh.end):
                return False
            done = bool(item.get("done", False))
            count = int(item.get("bytes_done", 0))
            if count < 0 or count > fresh.size:
                return False
            if done and count != fresh.size:
                return False
            saved_hash = str(item.get("sha256", ""))
            if (done or count > 0) and not re.fullmatch(r"[0-9a-f]{64}", saved_hash):
                return False
            if count == 0 and saved_hash:
                return False
            if not done and count == fresh.size:
                return False
        return Path(file_name).is_file() and Path(file_name).stat().st_size == total_bytes
    except (OSError, TypeError, ValueError, KeyError):
        return False


@dataclass
class ServerInfo:
    total_bytes:    int
    accepts_range:  bool
    suggested_name: str
    content_type:   str


class RangeProtocolError(RuntimeError):
    pass


class IncompleteDownloadError(RuntimeError):
    pass


class ServerBlockedError(RuntimeError):
    pass


def _safe_url_for_log(url: str) -> str:
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return f"{parsed.scheme}://{host}{parsed.path}"
    except Exception:
        return "<URL redacted>"


def _redact_error(error: object, url: str) -> str:
    message = str(error)
    if url:
        message = message.replace(url, _safe_url_for_log(url))
    
    
    message = re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?<query redacted>", message)
    message = re.sub(r"(?i)(url:\s*[^?\s]+)\?[^\s]+", r"\1?<query redacted>", message)
    return message


def _safe_filename(name: str) -> str:
    name = unquote(str(name or "")).replace("\\", "/")
    name = Path(name).name
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if not name:
        return "download"
    if name.upper().split(".")[0] in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        name = f"_{name}"
    return name[:240]


def _parse_content_range(value: str) -> Optional[tuple[int, int, int]]:
    match = re.fullmatch(r"\s*bytes\s+(\d+)-(\d+)/(\d+)\s*", str(value or ""), re.I)
    if not match:
        return None
    start, end, total = map(int, match.groups())
    if start < 0 or end < start or total <= end:
        return None
    return start, end, total


def _body_indicates_block(peek: bytes) -> bool:
    try:
        low = peek[:4096].lower()
    except Exception:
        return False
    return any(m in low for m in WAF_BODY_MARKERS)


def is_block_response(resp, peek: bytes | None = None) -> tuple[bool, str]:
    try:
        headers = {str(k).lower(): str(v) for k, v in dict(getattr(resp, "headers", {}) or {}).items()}
    except Exception:
        headers = {}
    try:
        final_url = str(getattr(resp, "url", "") or "").lower()
    except Exception:
        final_url = ""
    if "cf-mitigated" in headers:
        return True, "cf-mitigated header"
    if any(k.startswith("cf-chl-") for k in headers):
        return True, "cloudflare challenge header"
    if any(k.startswith("x-akamai-") and "bot" in str(v).lower() for k, v in headers.items()):
        return True, "akamai bot header"
    if any(d in final_url for d in CHALLENGE_DOMAINS):
        return True, "redirect to challenge page"
    if peek and _body_indicates_block(peek):
        return True, "challenge marker in body"
    return False, ""


def make_session(log_backend: bool = True) -> requests.Session:
    session = requests.Session()
    session.trust_env = True
    session.headers.update({
        "User-Agent": "RAPID/2.0 (parallel downloader)",
        "Accept-Encoding": "identity",
        "Connection": "keep-alive",
    })

    ca_bundle = (os.environ.get("REQUESTS_CA_BUNDLE")
                 or os.environ.get("CURL_CA_BUNDLE")
                 or os.environ.get("SSL_CERT_FILE"))
    if ca_bundle:
        session.verify = ca_bundle

    
    adapter = HTTPAdapter(
        pool_connections=2,
        pool_maxsize=2,
        max_retries=Retry(total=0, connect=0, read=0, redirect=0, status=0),
        pool_block=True,
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    if log_backend:
        _EARLY_LOG.info(
            "HTTP backend: requests/urllib3; TLS certificate verification enabled"
            + (f"; custom CA={ca_bundle}" if ca_bundle else "")
        )
    return session


def _response_filename(resp, fallback_url: str) -> str:
    cd = str(getattr(resp, "headers", {}).get("Content-Disposition", "") or "")
    name = ""
    if cd:
        star = re.search(r"filename\*\s*=\s*([^;]+)", cd, re.I)
        if star:
            raw = star.group(1).strip().strip('"')
            if "''" in raw:
                charset, _, encoded = raw.partition("''")
                try:
                    name = unquote(encoded, encoding=charset or "utf-8", errors="replace")
                except Exception:
                    name = unquote(encoded)
        if not name:
            plain = re.search(r'filename\s*=\s*(?:"([^"]+)"|([^;]+))', cd, re.I)
            if plain:
                name = (plain.group(1) or plain.group(2) or "").strip()
    if not name:
        final_url = str(getattr(resp, "url", "") or fallback_url)
        name = unquote(Path(urlparse(final_url).path).name)
    return _safe_filename(name or "download")


def _friendly_network_error(error: BaseException, url: str) -> str:
    detail = _redact_error(error, url)
    if isinstance(error, requests.exceptions.SSLError):
        return (
            "Falha na validação do certificado SSL. O RAPID mantém a verificação ativa; "
            "confira a inspeção HTTPS do antivírus/proxy, instale a CA raiz confiável no sistema "
            "ou configure REQUESTS_CA_BUNDLE/SSL_CERT_FILE. Detalhe: " + detail
        )
    if _is_dns_error(error):
        return (
            f"Falha de DNS ao resolver o servidor {_safe_url_for_log(url)}. "
            "Verifique DNS, VPN, proxy ou roteador. Detalhe: " + detail
        )
    if isinstance(error, requests.exceptions.Timeout):
        return "Tempo de conexão esgotado; verifique a estabilidade da rede. Detalhe: " + detail
    return detail


def _request_with_retries(session, method: str, url: str, log: logging.Logger, **kwargs):
    request_fn = getattr(session, method.lower())
    transient_statuses = {429, 500, 502, 503, 504}
    for attempt in range(1, MAX_RETRIES + 1):
        response = None
        try:
            response = request_fn(url, **kwargs)
            if response.status_code in transient_statuses:
                status = response.status_code
                if attempt >= MAX_RETRIES:
                    response.raise_for_status()
                log.warning(
                    f"{method.upper()} recebeu HTTP {status} ({attempt}/{MAX_RETRIES}); "
                    "tentando novamente."
                )
                response.close()
                _backoff(attempt)
                continue
            return response
        except requests.exceptions.SSLError:
            if response is not None:
                response.close()
            raise
        except requests.exceptions.RequestException as e:
            if response is not None:
                response.close()
            log.warning(
                f"{method.upper()} falhou ({attempt}/{MAX_RETRIES}): "
                f"{_friendly_network_error(e, url)}"
            )
            if attempt >= MAX_RETRIES:
                raise
            _backoff(attempt)
    raise RuntimeError("request retry loop ended unexpectedly")


def inspect_url(session, url: str, log: logging.Logger) -> ServerInfo:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("URL inválida: informe um endereço HTTP ou HTTPS completo.")

    head = None
    total = 0
    ctype = "application/octet-stream"
    name = _safe_filename(Path(unquote(parsed.path)).name or "download")
    try:
        head = _request_with_retries(
            session, "head", url, log,
            timeout=(CONNECT_TIMEOUT, 20), allow_redirects=True,
        )
        if head.status_code not in (405, 501):
            head.raise_for_status()
            total = int(head.headers.get("Content-Length", 0) or 0)
            ctype = str(head.headers.get("Content-Type", ctype))
            name = _response_filename(head, url)
    finally:
        if head is not None:
            head.close()

    probe = None
    accepts_range = False
    peek = b""
    try:
        probe = _request_with_retries(
            session, "get", url, log,
            headers={"Range": f"bytes=0-{PROBE_RANGE_SIZE}"},
            stream=True,
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            allow_redirects=True,
        )
        if probe.status_code == 206:
            parsed_range = _parse_content_range(probe.headers.get("Content-Range", ""))
            expected_probe_end = min(PROBE_RANGE_SIZE, parsed_range[2] - 1) if parsed_range else -1
            if (parsed_range is not None and parsed_range[0] == 0
                    and parsed_range[1] == expected_probe_end):
                accepts_range = True
                range_total = parsed_range[2]
                if total and total != range_total:
                    raise RuntimeError(
                        f"Metadata inconsistente: HEAD informou {total} bytes, "
                        f"mas Content-Range informou {range_total} bytes."
                    )
                total = range_total
            else:
                log.warning("Range probe returned 206 without a valid Content-Range; parallel mode disabled.")
        elif probe.status_code == 200:
            accepts_range = False
            if not total:
                total = int(probe.headers.get("Content-Length", 0) or 0)
        else:
            peek = next(probe.iter_content(chunk_size=4096), b"") or b""
            blocked, reason = is_block_response(probe, peek)
            if blocked:
                raise ServerBlockedError(f"CDN/WAF bloqueou a requisição ({reason}).")
            probe.raise_for_status()
            raise RuntimeError(f"Unexpected HTTP status on range probe: {probe.status_code}")

        ctype = str(probe.headers.get("Content-Type", ctype))
        if probe.headers.get("Content-Disposition"):
            name = _response_filename(probe, url)
        iterator = probe.iter_content(chunk_size=4096)
        peek = next(iterator, b"") or b""
        blocked, reason = is_block_response(probe, peek)
        if blocked:
            raise RuntimeError(f"Servidor retornou uma página de bloqueio/desafio ({reason}); download não iniciado.")
    finally:
        if probe is not None:
            probe.close()

    if total <= 0:
        raise RuntimeError("O servidor não informou o tamanho do arquivo (Content-Length/Content-Range).")

    log.info(
        f"HEAD/probe {_safe_url_for_log(url)} -> {total/1e9:.2f} GB | "
        f"Range verificado={accepts_range} | type={ctype}"
    )
    return ServerInfo(total_bytes=total, accepts_range=accepts_range,
                      suggested_name=name, content_type=ctype)


def _backoff(attempt: int, blocked: bool = False,
             stop_event: Optional[threading.Event] = None) -> None:
    base = min(2 ** (max(1, attempt) - 1), 30)
    if blocked:
        base = min(base * 1.5, 30)
    delay = base * (0.7 + random.random() * 0.6)
    deadline = time.monotonic() + delay
    while time.monotonic() < deadline:
        if stop_event is not None and stop_event.is_set():
            return
        time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))


def _is_dns_error(error: BaseException) -> bool:
    text = str(error).lower()
    return (isinstance(error, socket.gaierror)
            or "could not resolve host" in text
            or "name or service not known" in text
            or "getaddrinfo failed" in text
            or "temporary failure in name resolution" in text)


def _hash_file_region(file_name: str, start: int, length: int) -> str:
    digest = hashlib.sha256()
    remaining = int(length)
    with open(file_name, "rb") as source:
        source.seek(start)
        while remaining:
            block = source.read(min(4 * 1024 * 1024, remaining))
            if not block:
                raise OSError(f"arquivo parcial/truncado ao validar bytes em {start}")
            digest.update(block)
            remaining -= len(block)
    return digest.hexdigest()


def worker(
    worker_id: int,
    session: requests.Session,
    state: DownloadState,
    chunk_q: "queue.Queue[Chunk]",
    file_lock: threading.Lock,
    log: logging.Logger,
    stop_event: threading.Event,
    log_q: "queue.Queue[str]",
    tr: Translator,
    state_lock: Optional[threading.Lock] = None,
    abort_event: Optional[threading.Event] = None,
    range_mode: bool = True,
) -> None:
    abort_event = abort_event or threading.Event()

    def persist_progress(chunk: Chunk, amount: int, digest: "hashlib._Hash") -> None:
        with state._lock:
            chunk.bytes_done += amount
            chunk.sha256 = digest.hexdigest()
            state._bytes_done += amount
        save_state(state)

    while not stop_event.is_set() and not abort_event.is_set():
        try:
            chunk = chunk_q.get_nowait()
        except queue.Empty:
            return

        try:
            attempts = 0
            chunk_complete = bool(chunk.done and chunk.bytes_done == chunk.size)
            digest = hashlib.sha256()
            if chunk.bytes_done and not chunk.done:
                
                try:
                    remaining_prefix = chunk.bytes_done
                    with open(state.file_name, "rb") as existing:
                        existing.seek(chunk.start)
                        while remaining_prefix:
                            block = existing.read(min(CHUNK_SIZE, remaining_prefix))
                            if not block:
                                raise OSError("prefixo de retomada truncado")
                            digest.update(block)
                            remaining_prefix -= len(block)
                    if chunk.sha256 and digest.hexdigest() != chunk.sha256:
                        raise OSError("SHA-256 do prefixo salvo não confere")
                except OSError as e:
                    log.warning(f"[W{worker_id:02d}] prefixo da parte {chunk.index} inválido; reiniciando a parte: {e}")
                    with state._lock:
                        state._bytes_done = max(0, state._bytes_done - chunk.bytes_done)
                        chunk.bytes_done = 0
                        chunk.done = False
                        chunk.sha256 = ""
                    digest = hashlib.sha256()
            while not chunk_complete and attempts < MAX_RETRIES:
                if stop_event.is_set() or abort_event.is_set():
                    break

                
                if not range_mode and chunk.bytes_done:
                    with state._lock:
                        state._bytes_done = max(0, state._bytes_done - chunk.bytes_done)
                        chunk.bytes_done = 0
                        chunk.done = False
                        chunk.sha256 = ""
                    digest = hashlib.sha256()
                    with open(state.file_name, "wb"):
                        pass

                start = chunk.start + chunk.bytes_done
                if start > chunk.end:
                    with state._lock:
                        chunk.done = True
                    chunk_complete = True
                    save_state(state)
                    break

                attempts += 1
                headers = {}
                if range_mode:
                    headers["Range"] = f"bytes={start}-{chunk.end}"

                response = None
                stream_error = None
                bytes_in_response = 0
                expected_bytes = state.total_bytes if not range_mode else (chunk.end - start + 1)
                write_pos = start
                buffer = bytearray()

                try:
                    response = session.get(
                        state.url,
                        headers=headers,
                        stream=True,
                        timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                        allow_redirects=True,
                    )

                    if range_mode:
                        if response.status_code == 200:
                            raise RangeProtocolError(
                                "Servidor ignorou Range (HTTP 200); alternando para download sequencial seguro."
                            )
                        if response.status_code != 206:
                            if response.status_code >= 400:
                                error_peek = next(response.iter_content(chunk_size=4096), b"") or b""
                                blocked, reason = is_block_response(response, error_peek)
                                if blocked:
                                    raise ServerBlockedError(
                                        f"CDN/WAF bloqueou o pedaço {chunk.index} ({reason})."
                                    )
                            response.raise_for_status()
                            raise RuntimeError(f"HTTP inesperado para Range: {response.status_code}")
                        content_range = _parse_content_range(response.headers.get("Content-Range", ""))
                        if (content_range is None
                                or content_range[0] != start
                                or content_range[1] != chunk.end
                                or content_range[2] != state.total_bytes):
                            raise RangeProtocolError(
                                f"Content-Range inválido para o pedaço {chunk.index}: "
                                f"{response.headers.get('Content-Range', '<ausente>')}"
                            )
                    else:
                        if response.status_code != 200:
                            if response.status_code >= 400:
                                error_peek = next(response.iter_content(chunk_size=4096), b"") or b""
                                blocked, reason = is_block_response(response, error_peek)
                                if blocked:
                                    raise ServerBlockedError(
                                        f"CDN/WAF bloqueou o download ({reason})."
                                    )
                            response.raise_for_status()
                            raise RuntimeError(f"HTTP inesperado no download sequencial: {response.status_code}")

                    content_length = response.headers.get("Content-Length")
                    if content_length:
                        try:
                            if int(content_length) != expected_bytes:
                                raise RangeProtocolError(
                                    f"Content-Length inconsistente: esperado {expected_bytes}, recebido {content_length}."
                                )
                        except ValueError:
                            pass

                    blocked, reason = is_block_response(response)
                    if blocked:
                        raise RuntimeError(f"CDN/WAF retornou desafio: {reason}")

                    with open(state.file_name, "r+b", buffering=0) as output:
                        output.seek(write_pos)

                        def flush_buffer() -> None:
                            nonlocal write_pos
                            if not buffer:
                                return
                            payload = bytes(buffer)
                            with file_lock:
                                output.seek(write_pos)
                                written = output.write(payload)
                            if written != len(payload):
                                raise OSError(f"gravação incompleta no disco: {written}/{len(payload)} bytes")
                            write_pos += written
                            buffer.clear()
                            persist_progress(chunk, written, digest)

                        try:
                            for data in response.iter_content(chunk_size=CHUNK_SIZE):
                                if stop_event.is_set() or abort_event.is_set():
                                    break
                                if not data:
                                    continue
                                remaining = expected_bytes - bytes_in_response
                                if len(data) > remaining:
                                    raise RangeProtocolError(
                                        f"Servidor enviou bytes além do intervalo solicitado no pedaço {chunk.index}."
                                    )
                                bytes_in_response += len(data)
                                digest.update(data)
                                buffer.extend(data)
                                if len(buffer) >= WRITE_BUF_SIZE:
                                    flush_buffer()
                        except Exception as e:
                            stream_error = e
                        finally:
                            
                            
                            flush_buffer()

                    if stop_event.is_set() or abort_event.is_set():
                        save_state(state)
                        break
                    if stream_error is not None:
                        raise stream_error
                    if bytes_in_response != expected_bytes:
                        raise IncompleteDownloadError(
                            f"Conexão terminou antes do esperado: {bytes_in_response}/{expected_bytes} bytes."
                        )

                    if chunk.bytes_done == chunk.size:
                        with state._lock:
                            chunk.sha256 = digest.hexdigest()
                            chunk.done = True
                        chunk_complete = True
                        save_state(state)

                except RangeProtocolError as e:
                    with state._lock:
                        state.range_error = str(e)
                    abort_event.set()
                    log.warning(f"[W{worker_id:02d}] {e}")
                    break
                except ServerBlockedError as e:
                    with state._lock:
                        state.fatal_error = f"CDN/WAF: {e}"
                    abort_event.set()
                    log.error(f"[W{worker_id:02d}] {e}")
                    break
                except requests.exceptions.SSLError as e:
                    message = _redact_error(e, state.url)
                    hint = (
                        "Verificação SSL falhou. O RAPID não desativa a validação do certificado. "
                        "Instale/atualize o certificado raiz do antivírus/proxy no sistema ou configure "
                        "REQUESTS_CA_BUNDLE/SSL_CERT_FILE com o certificado CA correto."
                    )
                    with state._lock:
                        state.fatal_error = f"SSL: {message}"
                    log.error(f"[W{worker_id:02d}] SSL no pedaço {chunk.index}: {message}. {hint}")
                    abort_event.set()
                    break
                except requests.exceptions.RequestException as e:
                    message = _redact_error(e, state.url)
                    if _is_dns_error(e):
                        with state._lock:
                            state.dns_errors += 1
                            dns_failures = state.dns_errors
                        log.warning(f"[W{worker_id:02d}] DNS no pedaço {chunk.index}, tentativa {attempts}/{MAX_RETRIES}: {message}")
                        
                        
                        if dns_failures >= min(4, max(2, state.n_workers // 2)):
                            with state._lock:
                                state.fatal_error = "DNS: o host do servidor não está sendo resolvido pelo sistema."
                            abort_event.set()
                    elif isinstance(e, requests.exceptions.Timeout):
                        log.warning(f"[W{worker_id:02d}] timeout no pedaço {chunk.index}, tentativa {attempts}/{MAX_RETRIES}: {message}")
                    else:
                        log.warning(f"[W{worker_id:02d}] conexão no pedaço {chunk.index}, tentativa {attempts}/{MAX_RETRIES}: {message}")
                    if attempts < MAX_RETRIES:
                        _backoff(attempts, stop_event=stop_event)
                except OSError as e:
                    with state._lock:
                        state.fatal_error = f"Disco: {e}"
                    log.error(f"[W{worker_id:02d}] falha real de gravação no disco, pedaço {chunk.index}: {e}")
                    abort_event.set()
                    break
                except Exception as e:
                    message = _redact_error(e, state.url)
                    log.warning(f"[W{worker_id:02d}] erro no pedaço {chunk.index}, tentativa {attempts}/{MAX_RETRIES}: {message}")
                    if attempts < MAX_RETRIES:
                        _backoff(attempts, stop_event=stop_event)
                finally:
                    if response is not None:
                        try:
                            response.close()
                        except Exception:
                            pass

            if chunk_complete:
                log.info(tr.t("chunk_ok", worker_id=f"{worker_id:02d}", chunk=chunk.index,
                              size=f"{chunk.size/1e6:.1f}"))
            elif not stop_event.is_set() and not abort_event.is_set():
                log.error(tr.t("chunk_failed", worker_id=f"{worker_id:02d}",
                               chunk=chunk.index, retries=MAX_RETRIES))
        finally:
            chunk_q.task_done()


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


class _Tooltip:

    def __init__(self, widget, text_var):
        self.widget = widget
        self.text_var = text_var
        self.tipwindow = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, _event=None):
        text = self.text_var() if callable(self.text_var) else self.text_var
        if not text or self.tipwindow is not None:
            return
        try:
            x = self.widget.winfo_rootx() + 12
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
            tw = tk.Toplevel(self.widget)
            tw.wm_overrideredirect(True)
            tw.wm_geometry(f"+{x}+{y}")
            lbl = tk.Label(
                tw, text=text, justify="left", background="#ffffe0",
                relief="solid", borderwidth=1, wraplength=340,
                font=("TkDefaultFont", 8),
            )
            lbl.pack(ipadx=4, ipady=2)
            self.tipwindow = tw
        except Exception:
            self.tipwindow = None

    def _hide(self, _event=None):
        if self.tipwindow is not None:
            try:
                self.tipwindow.destroy()
            except Exception:
                pass
            self.tipwindow = None

    def update_text(self):
        self._hide()


class RapidGUI(tk.Tk):
    def __init__(self):
        super().__init__()

        try:
            self.log = setup_logging(clear=False)
        except NameError:
            self.log = setup_logging(clear=False)

        self.log_q: "queue.Queue[str]" = queue.Queue()
        self._gui_log_handler = _GuiQueueHandler(self.log_q)
        self._gui_log_handler.setFormatter(logging.Formatter("%(message)s"))
        self._gui_log_handler.setLevel(logging.DEBUG)
        self.log.addHandler(self._gui_log_handler)

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
        self._swap_icon = None
        self._refresh_window_icon()

        self.stop_event = threading.Event()
        self.download_thread: Optional[threading.Thread] = None
        self.state: Optional[DownloadState] = None
        self.info: Optional[ServerInfo] = None
        self.start_time = 0.0
        self.running = False
        self._closing = False

        self._rgb_hue: float = 200.0
        self._rgb_job: Optional[str] = None
        self._rgb_interval_ms: int = int(self.th.cycle_params.get("rgb", {}).get("cycle_interval_ms", 80))
        self._rgb_step_deg: float = float(self.th.cycle_params.get("rgb", {}).get("cycle_step_deg", 0.5))

        self._rgb_interval_ms = max(50, min(1000, self._rgb_interval_ms))
        self._rgb_step_deg = max(0.05, min(3.0, self._rgb_step_deg))

        self._log_buffer: "list[str]" = []
        self._fade_counter = 0

        self._build_ui()
        self._apply_theme()
        self.after(150, self._poll_log)
        self.after(200, self._poll_progress)
        self.after(90, self._drain_log_buffer)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    

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

        self.clock_var = tk.StringVar(value="")
        self.lbl_clock = ttk.Label(frm_lang, textvariable=self.clock_var, style="Muted.TLabel")
        self.lbl_clock.pack(side="right", padx=(6, 0))
        self._update_clock()

        self.frm_top = ttk.LabelFrame(self, text=self.tr.t("source_frame"))
        self.frm_top.pack(fill="x", **pad)

        self.lbl_url = ttk.Label(self.frm_top, text=self.tr.t("url_label"))
        self.lbl_url.grid(row=0, column=0, sticky="w", **pad)
        self.url_var = tk.StringVar()
        ttk.Entry(self.frm_top, textvariable=self.url_var, width=64).grid(row=0, column=1, columnspan=3, sticky="we", **pad)
        self._filename_user_edited = False
        self._suppress_filename_trace = False
        self._last_url_for_name = ""
        self.url_var.trace_add("write", self._on_url_changed)

        self.btn_verify = ttk.Button(self.frm_top, text=self.tr.t("verify_btn"), command=self._on_inspect)
        self.btn_verify.grid(row=0, column=4, **pad)

        self.lbl_save_as = ttk.Label(self.frm_top, text=self.tr.t("save_as_label"))
        self.lbl_save_as.grid(row=1, column=0, sticky="w", **pad)
        self.filename_var = tk.StringVar()
        self.filename_var.trace_add("write", self._on_filename_var_written)
        ttk.Entry(self.frm_top, textvariable=self.filename_var, width=48).grid(row=1, column=1, columnspan=2, sticky="we", **pad)
        self.btn_browse = ttk.Button(self.frm_top, text=self.tr.t("browse_btn"), command=self._on_browse)
        self.btn_browse.grid(row=1, column=3, **pad)

        self.lbl_workers = ttk.Label(self.frm_top, text=self.tr.t("workers_label"))
        self.lbl_workers.grid(row=2, column=0, sticky="w", **pad)
        self.workers_var = tk.IntVar(value=DEFAULT_WORKERS)
        self.spin_workers = ttk.Spinbox(self.frm_top, from_=1, to=MAX_WORKERS, textvariable=self.workers_var, width=6)
        self.spin_workers.grid(row=2, column=1, sticky="w", **pad)
        _Tooltip(self.spin_workers, lambda: self.tr.t("workers_help"))
        _Tooltip(self.lbl_workers, lambda: self.tr.t("workers_help"))

        self.lbl_parts = ttk.Label(self.frm_top, text=self.tr.t("parts_label"))
        self.lbl_parts.grid(row=3, column=0, sticky="w", **pad)
        self.parts_var = tk.IntVar(value=0)
        self.spin_parts = ttk.Spinbox(self.frm_top, from_=0, to=10000, textvariable=self.parts_var, width=6)
        self.spin_parts.grid(row=3, column=1, sticky="w", **pad)
        _Tooltip(self.spin_parts, lambda: self.tr.t("parts_help"))
        _Tooltip(self.lbl_parts, lambda: self.tr.t("parts_help"))

        self.parts_actual_var = tk.StringVar(value="")
        self.lbl_parts_actual = ttk.Label(self.frm_top, textvariable=self.parts_actual_var, style="Muted.TLabel")
        self.lbl_parts_actual.grid(row=3, column=2, sticky="w", **pad)

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

    def _update_clock(self):
        self.clock_var.set(time.strftime("%H:%M:%S"))
        self.after(1000, self._update_clock)

    def _font_ok(self, name: str) -> bool:
        try:
            import tkinter.font as tkfont
            return name in tkfont.families()
        except Exception:
            return False

    def _refresh_window_icon(self) -> None:
        try:
            try:
                fg = self.th.colors().get("accent_color", "#ff8a1f")
            except Exception:
                fg = "#ff8a1f"
            icon = self._build_swap_icon(size=64, color=fg)
            self.iconphoto(False, icon)
            self._swap_icon = icon
        except Exception:
            pass

    @staticmethod
    def _build_swap_icon(size: int = 64, color: str = "#ff8a1f") -> "tk.PhotoImage":
        img = tk.PhotoImage(width=size, height=size)
        y_top = int(size * 0.38)
        y_bot = int(size * 0.62)
        x_left = int(size * 0.14)
        x_right = int(size * 0.86)
        thick = max(4, size // 14)
        barb_len = int(size * 0.28)
        barb_drop = int(barb_len * 0.45)

        def dot(cx: int, cy: int, r: int) -> None:
            x0 = max(0, cx - r)
            y0 = max(0, cy - r)
            x1 = min(size - 1, cx + r)
            y1 = min(size - 1, cy + r)
            try:
                img.put(color, to=(x0, y0, x1 + 1, y1 + 1))
            except Exception:
                pass

        def line(x0: int, y0: int, x1: int, y1: int, w: int) -> None:
            dx = abs(x1 - x0)
            dy = abs(y1 - y0)
            steps = max(dx, dy) or 1
            r = max(0, w // 2)
            for i in range(steps + 1):
                t = i / steps
                dot(round(x0 + (x1 - x0) * t), round(y0 + (y1 - y0) * t), r)

        line(x_left, y_top, x_right, y_top, thick)
        line(x_right, y_top, x_right - barb_len, y_top - barb_drop, thick)
        line(x_left, y_bot, x_right, y_bot, thick)
        line(x_left, y_bot, x_left + barb_len, y_bot + barb_drop, thick)
        return img

    

    def _load_preferences(self) -> None:
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

    

    def _on_language_change(self, _event=None):
        name_to_code = {name: code for code, name in self._lang_codes}
        code = name_to_code.get(self.lang_var.get(), DEFAULT_LANG)
        self.tr.set_language(code)
        save_app_config(language=code, log=self.log)
        self._retranslate_static_ui()

    def _retranslate_static_ui(self):
        self.title(self.tr.t("window_title"))
        self.lbl_lang.configure(text=self.tr.t("language_label"))
        self.lbl_theme.configure(text=self.tr.t("theme_label"))
        self.frm_top.configure(text=self.tr.t("source_frame"))
        self.lbl_url.configure(text=self.tr.t("url_label"))
        self.btn_verify.configure(text=self.tr.t("verify_btn"))
        self.lbl_save_as.configure(text=self.tr.t("save_as_label"))
        self.btn_browse.configure(text=self.tr.t("browse_btn"))
        self.lbl_workers.configure(text=self.tr.t("workers_label"))
        self.lbl_parts.configure(text=self.tr.t("parts_label"))
        self.btn_start.configure(text=self.tr.t("download_btn"))
        self.btn_cancel.configure(text=self.tr.t("cancel_btn"))
        self.btn_open_log.configure(text=self.tr.t("open_log_btn"))
        self.frm_prog.configure(text=self.tr.t("progress_frame"))
        self.frm_log.configure(text=self.tr.t("log_frame"))
        if not self.running and self.state is None:
            self.status_var.set(self.tr.t("status_ready"))
        if self.info is None:
            self.info_var.set(self.tr.t("info_default"))

    

    def _on_theme_change(self, _event=None):
        name_to_code = {name: code for code, name in self._theme_codes}
        code = name_to_code.get(self.theme_var.get(), DEFAULT_THEME)
        self.th.set_theme(code)
        save_app_config(theme=code, log=self.log)
        self._committed_theme = code
        self._apply_theme()

    

    def _preview_theme(self, code: str) -> None:
        if code not in self.th.catalogs:
            return
        if code == self.th.current:
            return
        self.th.set_theme(code)
        self._apply_theme()

    def _get_theme_listbox(self):
        try:
            pop = self.tk.call("ttk::combobox::PopdownWindow", str(self.theme_combo))
            lb_path = f"{pop}.f.l"
            if str(self.tk.call("winfo", "exists", lb_path)) != "1":
                return None
            lb = tk.Listbox.__new__(tk.Listbox)  
            lb.master = self  
            lb._w = lb_path  
            lb.tk = self.tk  
            return lb
        except Exception:
            pass
        return None

    def _get_theme_popdown(self):
        try:
            pop = self.tk.call("ttk::combobox::PopdownWindow", str(self.theme_combo))
            if str(self.tk.call("winfo", "exists", pop)) != "1":
                return None
            w = tk.Toplevel.__new__(tk.Toplevel)  
            w.master = self  
            w._w = pop  
            w.tk = self.tk  
            return w
        except Exception:
            return None

    def _hook_theme_listbox(self) -> None:
        lb = self._get_theme_listbox()
        if lb is None:

            self.after(40, self._hook_theme_listbox)
            return
        lb_path = lb._w  

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
            self._theme_lb_hooked_paths = hooked  
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
        self._poll_theme_preview()

    def _poll_theme_preview(self) -> None:
        try:
            pop = self._get_theme_popdown()
            if pop is None or not pop.winfo_ismapped():  
                return
            lb = self._get_theme_listbox()
            if lb is None:
                return

            try:
                if lb.size() == 0:  
                    self.after(80, self._poll_theme_preview)
                    return

                idx = lb.index("active")  
                name = lb.get(idx)  
                name_to_code = {name: code for code, name in self._theme_codes}
                code = name_to_code.get(name)
                if code and code != self.th.current:
                    self._preview_theme(code)
            except Exception:
                pass

            if pop.winfo_ismapped():  
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
                lb_path = lb._w if lb is not None else ""  
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
        self._apply_style_colors(c)

        theme_lb = getattr(self, "_theme_listbox", None) or self._get_theme_listbox()
        if theme_lb is not None:
            try:
                theme_lb.configure(
                    background=c["input_background_color"],
                    foreground=c["input_text_color"],
                    selectbackground=c["accent_color"],
                    selectforeground=c["input_background_color"],
                )
            except Exception:
                pass

        self.option_add("*TCombobox*Listbox.background", c["input_background_color"])
        self.option_add("*TCombobox*Listbox.foreground", c["input_text_color"])
        self.option_add("*TCombobox*Listbox.selectBackground", c["accent_color"])
        self.option_add("*TCombobox*Listbox.selectForeground", c["input_background_color"])

        if self.th.current == "rgb":
            self._start_rgb_cycle()
        else:
            self._stop_rgb_cycle()

        self._refresh_window_icon()

    def _apply_style_colors(self, c: dict) -> None:
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

    

    @staticmethod
    def _hsv_to_hex(h: float, s: float, v: float) -> str:
        h = (h % 360) / 360.0
        r, g, b = colorsys.hsv_to_rgb(h, max(0, min(1, s)), max(0, min(1, v)))
        return f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"

    def _update_rgb_catalog(self) -> None:
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
        cfg = self.th.cycle_params.get("rgb", {})
        try:
            interval = int(float(cfg.get("cycle_interval_ms", self._rgb_interval_ms)))
        except Exception:
            interval = self._rgb_interval_ms
        try:
            step = float(cfg.get("cycle_step_deg", self._rgb_step_deg))
        except Exception:
            step = self._rgb_step_deg
        self._rgb_interval_ms = max(50, min(1000, interval))
        self._rgb_step_deg = max(0.05, min(3.0, step))

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

        c = self.th.catalogs["rgb"]
        self._apply_style_colors(c)

        self._rgb_job = self.after(self._rgb_interval_ms, self._rgb_tick)

    

    def _append_log(self, msg: str):
        c = self.th.colors()
        bg = c.get("log_background_color", "#000000")
        fg = c.get("log_text_color", "#dddddd")

        self.log_text.configure(state="normal")
        start_index = self.log_text.index("end-1c")
        self.log_text.insert("end", msg + "\n")
        
        
        try:
            line_count = int(self.log_text.index("end-1c").split(".")[0])
            if line_count > 3000:
                self.log_text.delete("1.0", f"{line_count - 2500}.0")
        except Exception:
            pass
        end_index = self.log_text.index("end-1c")

        tag = f"fade_{self._fade_counter}"
        self._fade_counter += 1
        self.log_text.tag_add(tag, start_index, end_index)
        self.log_text.tag_configure(tag, foreground=bg)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

        self._fade_step(tag, self._hex_to_rgb(bg), self._hex_to_rgb(fg), 0, 12)

    def _fade_step(self, tag, start_rgb, end_rgb, step, total):
        t = step / total
        cur = tuple(s + (e - s) * t for s, e in zip(start_rgb, end_rgb))
        try:
            self.log_text.tag_configure(tag, foreground=self._rgb_to_hex(cur))
        except tk.TclError:
            return
        if step < total:
            self.after(22, lambda: self._fade_step(tag, start_rgb, end_rgb, step + 1, total))

    @staticmethod
    def _hex_to_rgb(hexcolor: str):
        hexcolor = hexcolor.lstrip("#")
        return tuple(int(hexcolor[i:i + 2], 16) for i in (0, 2, 4))

    @staticmethod
    def _rgb_to_hex(rgb) -> str:
        return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(v)))) for v in rgb)

    def _queue_log(self, msg: str, level: int = logging.INFO) -> None:
        try:
            self.log.log(level, msg)
        except Exception:
            pass

    

    def _set_filename_auto(self, value: str) -> None:
        self._suppress_filename_trace = True
        try:
            self.filename_var.set(value)
        finally:
            self._suppress_filename_trace = False

    def _on_filename_var_written(self, *_args) -> None:
        if not self._suppress_filename_trace:
            self._filename_user_edited = True

    def _on_url_changed(self, *_args) -> None:
        url = self.url_var.get().strip()
        if url == self._last_url_for_name:
            return
        self._last_url_for_name = url
        if not self._filename_user_edited:
            self._set_filename_auto("")
            self.info = None
            self.info_var.set(self.tr.t("info_default"))

    def _on_browse(self):
        path = filedialog.asksaveasfilename(initialfile=self.filename_var.get() or "download")
        if path:
            self.filename_var.set(path)
            self._filename_user_edited = True

    def _on_inspect(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning(self.tr.t("app_title"), self.tr.t("warn_need_url_verify"))
            return
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            messagebox.showwarning(self.tr.t("app_title"), "Informe uma URL HTTP ou HTTPS válida.")
            return

        self.btn_verify.configure(state="disabled")

        def task():
            session = make_session()
            try:
                info = inspect_url(session, url, self.log)
            except Exception as e:
                self.log.error(self.tr.t("error_checking_url", error=_friendly_network_error(e, url)))
                self.after(0, lambda: self.btn_verify.configure(state="normal"))
                return
            finally:
                session.close()

            def update_ui():
                self.info = info
                if not self._filename_user_edited:
                    self._set_filename_auto(info.suggested_name)
                self.info_var.set(self.tr.t(
                    "info_format",
                    size=human_size(info.total_bytes),
                    range=self.tr.t("range_yes") if info.accepts_range else self.tr.t("range_no"),
                    type=info.content_type,
                ))
                self.log.info(self.tr.t(
                    "verified_msg", url=_safe_url_for_log(url), size=human_size(info.total_bytes),
                    range=self.tr.t("range_yes") if info.accepts_range else self.tr.t("range_no"),
                ))
                self.btn_verify.configure(state="normal")

            self.after(0, update_ui)

        threading.Thread(target=task, name="RAPID-inspect", daemon=True).start()

    def _on_start(self):
        if self.running:
            return
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning(self.tr.t("app_title"), self.tr.t("warn_need_url"))
            return
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            messagebox.showwarning(self.tr.t("app_title"), "Informe uma URL HTTP ou HTTPS válida.")
            return

        raw_filename = self.filename_var.get().strip()
        auto_name = not self._filename_user_edited
        file_name = raw_filename or _safe_filename(Path(unquote(parsed.path)).name) or "download"
        if auto_name:
            file_name = _safe_filename(file_name)
        file_name = str(Path(file_name).expanduser().resolve())
        target_path = Path(file_name)
        if target_path.exists() and target_path.is_dir():
            messagebox.showwarning(
                self.tr.t("app_title"),
                "O destino informado é uma pasta. Escolha um nome de arquivo para salvar o download.",
            )
            return
        if target_path.exists():
            saved_meta = load_state(file_name)
            saved_hash = saved_meta.get("url_sha256") if isinstance(saved_meta, dict) else None
            saved_url_match = (
                saved_hash == hashlib.sha256(url.encode("utf-8")).hexdigest()
                if saved_hash else (isinstance(saved_meta, dict) and saved_meta.get("url") == url)
            )
            if not saved_url_match:
                if auto_name:
                    stem, suffix = target_path.stem, target_path.suffix
                    index = 1
                    while True:
                        candidate = target_path.with_name(f"{stem} ({index}){suffix}")
                        if not candidate.exists() and not _state_path(str(candidate)).exists():
                            target_path = candidate
                            file_name = str(candidate)
                            self.log.info(f"Arquivo já existia; novo destino selecionado: {candidate.name}")
                            break
                        index += 1
                elif not messagebox.askyesno(
                    self.tr.t("app_title"),
                    f"O arquivo já existe e não há retomada válida para esta URL:\n{file_name}\n\nDeseja sobrescrevê-lo?",
                ):
                    return
        self._set_filename_auto(Path(file_name).name if auto_name else file_name)

        try:
            n_workers = max(1, min(int(self.workers_var.get()), MAX_WORKERS))
        except (ValueError, tk.TclError):
            n_workers = DEFAULT_WORKERS
            self.workers_var.set(n_workers)
        try:
            n_parts = max(0, min(int(self.parts_var.get()), 10000))
        except (ValueError, tk.TclError):
            n_parts = 0
            self.parts_var.set(0)

        self.stop_event = threading.Event()
        self.running = True
        self.btn_start.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        self.btn_verify.configure(state="disabled")
        self.progress.configure(value=0)
        self.status_var.set(self.tr.t("status_checking"))
        self.parts_actual_var.set("")
        self.stats_var.set("")

        prevent_sleep(self.log)
        self.download_thread = threading.Thread(
            target=self._run_download,
            args=(url, file_name, n_workers, n_parts, auto_name),
            name="RAPID-download",
            daemon=True,
        )
        self.download_thread.start()

    def _on_cancel(self):
        if self.running:
            self.stop_event.set()
            self.log.info(self.tr.t("cancel_requested"))

    def _on_close(self):
        if self.running:
            if not messagebox.askyesno(self.tr.t("confirm_close_title"), self.tr.t("confirm_close_msg")):
                return
            self._closing = True
            self.stop_event.set()
            self.btn_start.configure(state="disabled")
            self.btn_cancel.configure(state="disabled")
            self.btn_verify.configure(state="disabled")
            self._stop_rgb_cycle()
            self.after(100, self._close_when_worker_stops)
            return
        self._close_now()

    def _close_when_worker_stops(self):
        thread = self.download_thread
        if thread is not None and thread.is_alive():
            self.after(100, self._close_when_worker_stops)
            return
        self._close_now()

    def _close_now(self):
        try:
            self._stop_rgb_cycle()
            self.log.info("RAPID closed by user")
            for h in self.log.handlers:
                try:
                    h.flush()
                except Exception:
                    pass
        except Exception:
            pass
        try:
            allow_sleep(self.log)
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
                os.startfile(LOG_FILE)  
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(LOG_FILE)])
            else:
                subprocess.Popen(["xdg-open", str(LOG_FILE)])
        except Exception as e:
            messagebox.showinfo(self.tr.t("log_file_title"), f"{LOG_FILE}\n\n{e}")

    

    def _run_download(self, url: str, file_name: str, n_workers: int,
                      n_parts: int = 0, auto_name: bool = False):
        inspect_session = None
        self.start_time = time.monotonic()
        try:
            inspect_session = make_session()
            info = inspect_url(inspect_session, url, self.log)
            self.info = info
            self.log.info(self.tr.t(
                "size_range_type_msg",
                size=human_size(info.total_bytes),
                range=self.tr.t("range_yes") if info.accepts_range else self.tr.t("range_no"),
                type=info.content_type,
            ))

            if auto_name and info.suggested_name:
                parent = Path(file_name).parent
                suggested_path = parent / _safe_filename(info.suggested_name)
                if suggested_path.exists() and suggested_path.resolve() != Path(file_name).resolve():
                    existing_meta = load_state(str(suggested_path))
                    existing_hash = (existing_meta.get("url_sha256")
                                     if isinstance(existing_meta, dict) else None)
                    expected_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()
                    same_download = (
                        existing_hash == expected_hash if existing_hash
                        else (isinstance(existing_meta, dict) and existing_meta.get("url") == url)
                    )
                    if not same_download:
                        stem, suffix = suggested_path.stem, suggested_path.suffix
                        index = 1
                        while suggested_path.exists() or _state_path(str(suggested_path)).exists():
                            suggested_path = parent / f"{stem} ({index}){suffix}"
                            index += 1
                        self.log.info(
                            f"Nome sugerido já existe; destino alternativo: {suggested_path.name}"
                        )
                file_name = str(suggested_path.resolve())
                self.after(0, lambda fn=Path(file_name).name: self._set_filename_auto(fn))

            if info.total_bytes <= 0:
                raise RuntimeError("O servidor não informou um tamanho válido para o arquivo.")

            use_range = bool(info.accepts_range)
            if use_range:
                if n_parts > 0:
                    part_size_used = max(-(-info.total_bytes // n_parts), MIN_SPLIT_SIZE)
                else:
                    part_size_used = CHUNK_PART_SIZE
                fresh_chunks = []
                offset = 0
                while offset < info.total_bytes:
                    end = min(offset + part_size_used - 1, info.total_bytes - 1)
                    fresh_chunks.append(Chunk(index=len(fresh_chunks), start=offset, end=end))
                    offset = end + 1
            else:
                
                
                part_size_used = info.total_bytes
                fresh_chunks = [Chunk(index=0, start=0, end=info.total_bytes - 1)]
                n_workers = 1

            saved = load_state(file_name) if use_range else None
            resumed = False
            if use_range and _saved_state_matches(saved, fresh_chunks, url, info.total_bytes, file_name):
                try:
                    chunks = [
                        Chunk(
                            index=int(c["index"]), start=int(c["start"]), end=int(c["end"]),
                            done=bool(c["done"]), bytes_done=int(c.get("bytes_done", 0)),
                            sha256=str(c.get("sha256", "")),
                        )
                        for c in saved["chunks"]
                    ]
                    
                    
                    for resumed_chunk in chunks:
                        if resumed_chunk.done:
                            actual_digest = _hash_file_region(
                                file_name, resumed_chunk.start, resumed_chunk.size
                            )
                            if actual_digest != resumed_chunk.sha256:
                                self.log.warning(
                                    f"Parte {resumed_chunk.index} falhou na verificação SHA-256; será baixada novamente."
                                )
                                resumed_chunk.done = False
                                resumed_chunk.bytes_done = 0
                                resumed_chunk.sha256 = ""
                    resumed = True
                except Exception as resume_error:
                    self.log.warning(f"Estado de retomada inválido; reiniciando: {resume_error}")
                    chunks = fresh_chunks
            else:
                chunks = fresh_chunks

            self.state = DownloadState(
                url=url, file_name=file_name, total_bytes=info.total_bytes,
                n_workers=n_workers, chunks=chunks,
            )
            with self.state._lock:
                self.state._bytes_done = sum(c.bytes_done for c in chunks)

            self.after(0, lambda n=len(chunks): self.parts_actual_var.set(
                self.tr.t("parts_actual_format", n=n)
            ))

            if not resumed:
                clear_state(file_name)
                try:
                    target_parent = Path(file_name).parent
                    target_parent.mkdir(parents=True, exist_ok=True)
                    free_bytes = shutil.disk_usage(target_parent).free
                    if free_bytes < info.total_bytes:
                        raise OSError(
                            f"Espaço insuficiente: arquivo requer {human_size(info.total_bytes)}, "
                            f"mas há {human_size(free_bytes)} livres."
                        )
                    if use_range:
                        with open(file_name, "wb") as f:
                            f.truncate(info.total_bytes)
                    else:
                        with open(file_name, "wb"):
                            pass
                except OSError as e:
                    raise OSError(f"Não foi possível criar o arquivo de destino: {e}") from e
                if use_range:
                    save_state(self.state)
            else:
                save_state(self.state)
                done_count = sum(1 for c in chunks if c.done)
                self.log.info(
                    f"Retomando '{Path(file_name).name}': {done_count}/{len(chunks)} partes completas "
                    f"({human_size(self.state.bytes_downloaded)} de {human_size(info.total_bytes)})."
                )

            self.log.info(
                f"Iniciando '{Path(file_name).name}': {len(chunks)} partes, "
                f"{human_size(part_size_used)} por parte, {n_workers} worker(s), "
                f"Range={'sim' if use_range else 'não'}"
            )

            def run_workers(target_chunks: list[Chunk], workers: int, ranged: bool,
                            abort: threading.Event) -> None:
                chunk_queue: "queue.Queue[Chunk]" = queue.Queue()
                for item in target_chunks:
                    if not item.done:
                        chunk_queue.put(item)
                if chunk_queue.empty():
                    return
                count = max(1, min(workers, chunk_queue.qsize()))
                sessions = [make_session(log_backend=False) for _ in range(count)]
                shared_file_lock = threading.Lock()
                try:
                    with ThreadPoolExecutor(max_workers=count, thread_name_prefix="RAPID-worker") as executor:
                        futures = [
                            executor.submit(
                                worker, i, sessions[i], self.state, chunk_queue,
                                shared_file_lock, self.log, self.stop_event,
                                self.log_q, self.tr, None, abort, ranged,
                            )
                            for i in range(count)
                        ]
                        for future in as_completed(futures):
                            try:
                                future.result()
                            except Exception as e:
                                self.log.error(f"Worker terminou com erro inesperado: {_redact_error(e, url)}")
                                abort.set()
                finally:
                    for worker_session in sessions:
                        try:
                            worker_session.close()
                        except Exception:
                            pass

            abort_event = threading.Event()
            run_workers(chunks, n_workers, use_range, abort_event)

            if self.stop_event.is_set():
                if use_range:
                    save_state(self.state)
                else:
                    clear_state(file_name)
                self.log.info(self.tr.t("cancelled_by_user"))
                self._finish(success=False)
                return

            
            
            
            if self.state.range_error and use_range and not self.state.fatal_error:
                reason = self.state.range_error
                self.log.warning(f"Servidor não respeitou HTTP Range ({reason}); reiniciando sequencialmente.")
                clear_state(file_name)
                with open(file_name, "wb"):
                    pass
                single = Chunk(index=0, start=0, end=info.total_bytes - 1)
                self.state = DownloadState(
                    url=url, file_name=file_name, total_bytes=info.total_bytes,
                    n_workers=1, chunks=[single],
                )
                fallback_abort = threading.Event()
                run_workers([single], 1, False, fallback_abort)
                use_range = False

            if self.stop_event.is_set():
                if use_range:
                    save_state(self.state)
                else:
                    clear_state(file_name)
                self.log.info(self.tr.t("cancelled_by_user"))
                self._finish(success=False)
                return

            if self.state.fatal_error:
                if use_range:
                    save_state(self.state)
                self.log.error(f"Download interrompido por erro não recuperável: {self.state.fatal_error}")
                self._finish(success=False)
                return

            failed = [c.index for c in self.state.chunks if not c.done or c.bytes_done != c.size]
            if failed:
                if use_range:
                    save_state(self.state)
                else:
                    clear_state(file_name)
                self.log.error(f"Download incompleto: {len(failed)} parte(s) pendente(s): {failed[:10]}")
                self._finish(success=False)
                return

            final_path = Path(file_name)
            actual_size = final_path.stat().st_size
            if actual_size != info.total_bytes:
                raise IOError(
                    f"Tamanho final inválido: esperado {info.total_bytes} bytes, encontrado {actual_size}."
                )

            with final_path.open("rb+") as final_file:
                final_file.flush()
                os.fsync(final_file.fileno())

            clear_state(file_name)
            elapsed = time.monotonic() - self.start_time
            avg = info.total_bytes / elapsed / 1e6 if elapsed > 0 else 0
            self.log.info(self.tr.t(
                "download_complete",
                file=Path(file_name).name, size=human_size(info.total_bytes),
                elapsed=f"{elapsed:.1f}", speed=f"{avg:.2f}",
            ))
            self._finish(success=True)

        except Exception as e:
            
            
            self.log.error(f"Falha no download: {_friendly_network_error(e, url)}")
            if self.state is not None and self.state.file_name == file_name:
                if self.state.range_error is None and self.state.fatal_error is None:
                    save_state(self.state)
            self._finish(success=False)
        finally:
            if inspect_session is not None:
                try:
                    inspect_session.close()
                except Exception:
                    pass

    def _finish(self, success: bool):
        def update_ui():
            try:
                allow_sleep(self.log)
                self.running = False
                if self.winfo_exists():
                    self.btn_start.configure(state="normal")
                    self.btn_cancel.configure(state="disabled")
                    self.btn_verify.configure(state="normal")
                    self.status_var.set(self.tr.t("status_done") if success else self.tr.t("status_interrupted"))
            except tk.TclError:
                pass
        try:
            self.after(0, update_ui)
        except tk.TclError:
            allow_sleep(self.log)
            self.running = False

    

    def _poll_log(self):
        try:
            while True:
                msg = self.log_q.get_nowait()
                self._log_buffer.append(msg)
        except queue.Empty:
            pass
        self.after(150, self._poll_log)

    def _drain_log_buffer(self):
        if self._log_buffer:
            msg = self._log_buffer.pop(0)
            self._append_log(msg)
        delay = 90 if len(self._log_buffer) < 5 else 20
        self.after(delay, self._drain_log_buffer)

    def _poll_progress(self):
        if self.state is not None:
            n_chunks = len(self.state.chunks)
            self.parts_actual_var.set(self.tr.t("parts_actual_format", n=n_chunks))
        if self.state is not None and self.info is not None and self.info.total_bytes > 0:
            done = min(self.state.bytes_downloaded, self.info.total_bytes)
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
