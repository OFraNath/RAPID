"""
RAPID — Reliable Asynchronous Parallel Internet Downloader

Qt/Fluent UI edition. The download core (Chunk, DownloadState, ServerInfo,
Translator, ThemeManager file loading, validation, worker) is unchanged in
behavior; only the presentation layer moved from Tkinter to PySide6 +
QFluentWidgets (frameless window, custom title bar, QSS theming).

The script is identical on Windows and Linux: no platform branches, only
capability detection (e.g. "is this helper program available?").
"""

from __future__ import annotations

import colorsys
import configparser
import importlib
import importlib.util
import logging
import shutil
import subprocess
import sys
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "rapid.log"
if getattr(sys, "frozen", False):
    LANGUAGES_DIR = Path(sys._MEIPASS) / "languages"  # type: ignore[attr-defined]
    THEMES_DIR = Path(sys._MEIPASS) / "themes"  # type: ignore[attr-defined]
else:
    LANGUAGES_DIR = BASE_DIR / "languages"
    THEMES_DIR = BASE_DIR / "themes"
DEFAULT_LANG = "en"
DEFAULT_THEME = "ember"
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

# ─────────────────────────────────────────────────────────────────────────────

_THIRD_PARTY_PACKAGES = {
    "requests": "requests",
    "urllib3": "urllib3",
    "curl_cffi": "curl_cffi",
    "PySide6": "PySide6",
    "qfluentwidgets": "PySide6-Fluent-Widgets",
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


_ensure_dependencies()

import json
import locale
import queue
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import unquote, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ─────────────────────────────────────────────────────────────────────────────
# Qt imports (after _ensure_dependencies so auto-install applies to them too)
# ─────────────────────────────────────────────────────────────────────────────

from PySide6.QtCore import Qt, QTimer, Signal, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
    QSizePolicy, QTextEdit, QVBoxLayout, QWidget,
)

try:  # Fluent widgets — preferred presentation layer (GPLv3, same as RAPID)
    from qfluentwidgets import (
        CardWidget as _FluentCard,
        ComboBox as _FluentComboBox,
        LineEdit as _FluentLineEdit,
        MessageBox as _FluentMessageBox,
        PrimaryPushButton as _FluentPrimaryButton,
        ProgressBar as _FluentProgressBar,
        PushButton as _FluentPushButton,
        SpinBox as _FluentSpinBox,
        FluentWidget as _FluentBase,
        setTheme as _fluent_setTheme,
        setThemeColor as _fluent_setThemeColor,
        Theme as _FluentTheme,
    )
    _HAS_FLUENT = True
except Exception as _fluent_err:  # pragma: no cover — degraded fallback
    _EARLY_LOG.warning(f"qfluentwidgets unavailable ({_fluent_err}), using plain Qt widgets.")
    _HAS_FLUENT = False
    from PySide6.QtWidgets import (
        QComboBox as _FluentComboBox,
        QFrame as _FluentCard,
        QLineEdit as _FluentLineEdit,
        QProgressBar as _FluentProgressBar,
        QPushButton as _FluentPushButton,
        QPushButton as _FluentPrimaryButton,
        QSpinBox as _FluentSpinBox,
        QWidget as _FluentBase,
    )
    _FluentMessageBox = None  # type: ignore[assignment]
    _fluent_setTheme = None  # type: ignore[assignment]
    _fluent_setThemeColor = None  # type: ignore[assignment]
    _FluentTheme = None  # type: ignore[assignment]

# Public aliases used throughout the GUI code below.
CardWidget = _FluentCard
ComboBox = _FluentComboBox
LineEdit = _FluentLineEdit
FluentMessageBox = _FluentMessageBox
PrimaryPushButton = _FluentPrimaryButton
ProgressBar = _FluentProgressBar
PushButton = _FluentPushButton
SpinBox = _FluentSpinBox
FluentBaseWidget = _FluentBase

# ─────────────────────────────────────────────────────────────────────────────
# Sleep inhibition — universal, capability-based (no platform branches).
# Tries each OS mechanism only if its API/helper exists on this machine,
# so the exact same code runs on Windows and Linux.
# ─────────────────────────────────────────────────────────────────────────────

_caffeinate_proc = None  # type: ignore[assignment]


def prevent_sleep(log: "logging.Logger") -> None:
    """Ask the OS not to sleep while downloading (best effort, any OS)."""
    global _caffeinate_proc
    # Windows execution-state API — only if this interpreter exposes it.
    try:
        import ctypes

        windll = getattr(ctypes, "windll", None)
        kernel32 = getattr(windll, "kernel32", None) if windll is not None else None
        if kernel32 is not None and hasattr(kernel32, "SetThreadExecutionState"):
            ES_CONTINUOUS = 0x80000000
            ES_SYSTEM_REQUIRED = 0x00000001
            ES_DISPLAY_REQUIRED = 0x00000002
            kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
            )
            log.debug("prevent_sleep: Windows execution state set.")
            return
    except Exception as e:
        log.debug(f"prevent_sleep: Windows API not used ({e}).")
    # macOS caffeinate helper — only if present.
    try:
        if shutil.which("caffeinate"):
            if _caffeinate_proc is None or _caffeinate_proc.poll() is not None:
                _caffeinate_proc = subprocess.Popen(
                    ["caffeinate", "-i"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                log.debug("prevent_sleep: caffeinate started.")
                return
    except Exception as e:
        log.debug(f"prevent_sleep: caffeinate not used ({e}).")
    # Linux systemd inhibitor — only if present.
    try:
        if shutil.which("systemd-inhibit"):
            if _caffeinate_proc is None or _caffeinate_proc.poll() is not None:
                _caffeinate_proc = subprocess.Popen(
                    [
                        "systemd-inhibit", "--what=idle:sleep",
                        "--who=RAPID", "--why=Active download in progress",
                        "sleep", "infinity",
                    ],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                log.debug("prevent_sleep: systemd-inhibit started.")
                return
    except Exception as e:
        log.debug(f"prevent_sleep: systemd-inhibit not used ({e}).")
    log.debug("prevent_sleep: no inhibitor available, continuing without it.")


def allow_sleep(log: "logging.Logger") -> None:
    """Undo prevent_sleep() once a download finishes/cancels/fails."""
    global _caffeinate_proc
    try:
        import ctypes

        windll = getattr(ctypes, "windll", None)
        kernel32 = getattr(windll, "kernel32", None) if windll is not None else None
        if kernel32 is not None and hasattr(kernel32, "SetThreadExecutionState"):
            ES_CONTINUOUS = 0x80000000
            kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            log.debug("allow_sleep: Windows execution state cleared.")
    except Exception as e:
        log.debug(f"allow_sleep: Windows API not cleared ({e}).")
    try:
        if _caffeinate_proc is not None:
            try:
                _caffeinate_proc.terminate()
            except Exception:
                pass
            _caffeinate_proc = None
            log.debug("allow_sleep: helper inhibitor stopped.")
    except Exception as e:
        log.debug(f"allow_sleep: could not release helper inhibitor ({e}).")


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_WORKERS = 16
MAX_WORKERS     = 64
CHUNK_SIZE      = 1  * 1024 * 1024
CHUNK_PART_SIZE = 64 * 1024 * 1024
MIN_SPLIT_SIZE  = 4  * 1024 * 1024
MAX_RETRIES     = 3
CONNECT_TIMEOUT = 15
READ_TIMEOUT    = 45
WRITE_BUF_SIZE  = 4  * 1024 * 1024
SNIFF_SIZE       = 512
PROBE_RANGE_SIZE = 1023
PROBE_SMALL_SIZE = 1023
FLATLINE_MIN_BYTES = 10 * 1024
MAX_ROUNDS       = 3
UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
]
HTML_MARKERS = (b"<!doctype", b"<html", b"<head", b"<script")
WAF_BODY_MARKERS = (
    b"checking your browser", b"challenge-platform", b"captcha",
    b"cf-challenge", b"just a moment", b"attention required",
    b"access denied", b"cloudflare ray", b"verify you are human",
)
CHALLENGE_DOMAINS = (
    "challenges.cloudflare.com", "accounts.google.com",
    "www.google.com/recaptcha", "/cdn-cgi/challenge",
)
BINARY_EXTS = frozenset({
    ".mp4", ".mkv", ".avi", ".mov", ".iso", ".zip", ".rar", ".7z",
    ".exe", ".msi", ".mp3", ".flac", ".pdf", ".tar", ".gz", ".bin",
})

MAGIC_SIGNATURES: dict[str, list[tuple[int, bytes]]] = {
    "pdf":   [(0, b"%PDF-")],
    "rtf":   [(0, b"{\\rtf1")],
    "ole":   [(0, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")],
    "zip":   [(0, b"PK\x03\x04"), (0, b"PK\x05\x06"), (0, b"PK\x07\x08")],
    "rar":   [(0, b"Rar!\x1a\x07\x00"), (0, b"Rar!\x1a\x07\x01\x00")],
    "7z":    [(0, b"7z\xbc\xaf\x27\x1c")],
    "gzip":  [(0, b"\x1f\x8b")],
    "bzip2": [(0, b"BZh")],
    "xz":    [(0, b"\xfd7zXZ\x00")],
    "zstd":  [(0, b"\x28\xb5\x2f\xfd")],
    "tar":   [(257, b"ustar")],
    "iso":   [(0x8001, b"CD001"), (0x8801, b"CD001"), (0x9001, b"CD001")],
    "cab":   [(0, b"MSCF")],
    "exe":   [(0, b"MZ")],
    "elf":   [(0, b"\x7fELF")],
    "macho": [(0, b"\xfe\xed\xfa\xce"), (0, b"\xfe\xed\xfa\xcf"),
              (0, b"\xca\xfe\xba\xbe")],
    "mp3":   [(0, b"ID3"), (0, b"\xff\xfb"), (0, b"\xff\xf3"), (0, b"\xff\xf2")],
    "flac":  [(0, b"fLaC")],
    "wav":   [(0, b"RIFF")],
    "ogg":   [(0, b"OggS")],
    "midi":  [(0, b"MThd")],
    "ape":   [(0, b"MAC ")],
    "ftyp":  [(4, b"ftyp")],
    "mkv":   [(0, b"\x1a\x45\xdf\xa3")],
    "avi":   [(0, b"RIFF")],
    "wmv":   [(0, b"\x30\x26\xb2\x75\x8e\x66\xcf\x11")],
    "flv":   [(0, b"FLV\x01")],
    "png":   [(0, b"\x89PNG\r\n\x1a\n")],
    "jpg":   [(0, b"\xff\xd8\xff")],
    "gif":   [(0, b"GIF87a"), (0, b"GIF89a")],
    "bmp":   [(0, b"BM")],
    "webp":  [(0, b"RIFF")],
    "ico":   [(0, b"\x00\x00\x01\x00")],
    "heic":  [(4, b"ftypheic"), (4, b"ftypheix"), (4, b"ftypmif1")],
    "tiff":  [(0, b"II*\x00"), (0, b"MM\x00*")],
    "ttf":   [(0, b"\x00\x01\x00\x00"), (0, b"true")],
    "otf":   [(0, b"OTTO")],
    "woff":  [(0, b"wOFF")],
    "woff2": [(0, b"wOF2")],
}
_RIFF_SUBTYPES = {b"WAVE": "wav", b"AVI ": "avi", b"WEBP": "webp"}
_EXT_FAMILY = {
    ".pdf": {"pdf"}, ".rtf": {"rtf"},
    ".doc": {"ole"}, ".xls": {"ole"}, ".ppt": {"ole"}, ".msi": {"ole", "exe"},
    ".zip": {"zip"}, ".docx": {"zip"}, ".xlsx": {"zip"}, ".pptx": {"zip"},
    ".apk": {"zip"}, ".jar": {"zip"},
    ".rar": {"rar"}, ".7z": {"7z"}, ".gz": {"gzip"}, ".tgz": {"gzip"},
    ".bz2": {"bzip2"}, ".xz": {"xz"}, ".zst": {"zstd"}, ".tar": {"tar"},
    ".iso": {"iso"}, ".cab": {"cab"},
    ".exe": {"exe"}, ".dll": {"exe"}, ".elf": {"elf"}, ".bin": {"exe", "elf"},
    ".mp3": {"mp3"}, ".flac": {"flac"}, ".wav": {"wav"}, ".ogg": {"ogg"},
    ".mid": {"midi"}, ".ape": {"ape"},
    ".mp4": {"ftyp"}, ".m4a": {"ftyp"}, ".m4v": {"ftyp"}, ".mov": {"ftyp"},
    ".3gp": {"ftyp"}, ".heic": {"ftyp", "heic"},
    ".mkv": {"mkv"}, ".webm": {"mkv"}, ".avi": {"avi"},
    ".wmv": {"wmv"}, ".wma": {"wmv"}, ".flv": {"flv"},
    ".png": {"png"}, ".jpg": {"jpg"}, ".jpeg": {"jpg"}, ".gif": {"gif"},
    ".bmp": {"bmp"}, ".webp": {"webp"}, ".ico": {"ico"}, ".tif": {"tiff"},
    ".tiff": {"tiff"}, ".ttf": {"ttf"}, ".otf": {"otf"},
    ".woff": {"woff"}, ".woff2": {"woff2"},
}



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


def theme_luminance(colors: dict[str, str]) -> float:
    """Background luminance 0-255, used to pick Fluent light/dark mode."""
    hx = colors.get("background_color", "#808080").lstrip("#")
    try:
        r = int(hx[0:2], 16)
        g = int(hx[2:4], 16)
        b = int(hx[4:6], 16)
        return 0.299 * r + 0.587 * g + 0.114 * b
    except Exception:
        return 128.0


def build_qss(c: dict[str, str]) -> str:
    """Map a ThemeManager palette (the 11 *.theme keys) onto Qt/Fluent QSS.

    The .theme files stay the single source of truth; this is only the
    consumer that replaces the old ttk.Style mapping.
    """
    bg = c.get("background_color", "#2b2b2b")
    fg = c.get("text_color", "#ffffff")
    muted = c.get("secondary_text_color", "#aaaaaa")
    border = c.get("border_color", "#555555")
    btn_bg = c.get("button_background_color", "#3a3a3a")
    btn_fg = c.get("button_text_color", "#ffffff")
    in_bg = c.get("input_background_color", "#1e1e1e")
    in_fg = c.get("input_text_color", "#ffffff")
    accent = c.get("accent_color", "#3a7bd5")
    log_bg = c.get("log_background_color", "#111111")
    log_fg = c.get("log_text_color", "#dddddd")
    return f"""
* {{ font-family: "Segoe UI", "Inter", "Cantarell", sans-serif; }}
RapidWindow, QWidget#rapidRoot {{
    background-color: {bg};
    color: {fg};
}}
QLabel {{ background: transparent; color: {fg}; }}
QLabel#muted {{ color: {muted}; }}
QLabel#titleAccent {{
    color: {accent};
    font-weight: 700;
    font-size: 20px;
}}
QLabel#clock {{ color: {muted}; }}
CardWidget, QFrame#card {{
    background-color: {bg};
    border: 1px solid {border};
    border-radius: 12px;
}}
QGroupBox {{
    background-color: {bg};
    color: {fg};
    border: 1px solid {border};
    border-radius: 12px;
    margin-top: 12px;
    padding-top: 8px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
    color: {fg};
}}
PushButton, PrimaryPushButton, QPushButton {{
    background-color: {btn_bg};
    color: {btn_fg};
    border: 1px solid {border};
    border-radius: 8px;
    padding: 7px 16px;
}}
PushButton:hover, QPushButton:hover {{
    border: 1px solid {accent};
}}
PushButton:disabled, QPushButton:disabled {{
    color: {muted};
}}
PrimaryPushButton {{
    background-color: {accent};
    color: {in_bg};
    border: 1px solid {accent};
    font-weight: 600;
}}
LineEdit, QLineEdit, ComboBox, QComboBox, SpinBox, QSpinBox {{
    background-color: {in_bg};
    color: {in_fg};
    border: 1px solid {border};
    border-radius: 8px;
    padding: 6px 10px;
    selection-background-color: {accent};
}}
LineEdit:focus, QLineEdit:focus, ComboBox:focus, QComboBox:focus {{
    border: 1px solid {accent};
}}
QComboBox QAbstractItemView, QListView {{
    background-color: {in_bg};
    color: {in_fg};
    selection-background-color: {accent};
    selection-color: {in_bg};
    border: 1px solid {border};
    border-radius: 8px;
    outline: 0;
}}
ProgressBar, QProgressBar {{
    background-color: {btn_bg};
    border: 1px solid {border};
    border-radius: 8px;
    height: 14px;
    text-align: center;
    color: {fg};
}}
ProgressBar::chunk, QProgressBar::chunk {{
    background-color: {accent};
    border-radius: 6px;
}}
TextEdit, QTextEdit, QPlainTextEdit {{
    background-color: {log_bg};
    color: {log_fg};
    border: 1px solid {border};
    border-radius: 12px;
    padding: 6px;
}}
QToolTip {{
    background-color: {in_bg};
    color: {in_fg};
    border: 1px solid {accent};
    border-radius: 6px;
    padding: 4px 8px;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {border};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {accent}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""


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
    bytes_done: int = 0

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


# ─────────────────────────────────────────────────────────────────────────────
# Resume state persistence
# ─────────────────────────────────────────────────────────────────────────────

STATE_SUFFIX = ".rapidstate"


def _state_path(file_name: str) -> Path:
    return Path(str(file_name) + STATE_SUFFIX)


def save_state(state: "DownloadState") -> None:
    path = _state_path(state.file_name)
    data = {
        "url": state.url,
        "file_name": state.file_name,
        "total_bytes": state.total_bytes,
        "chunks": [
            {"index": c.index, "start": c.start, "end": c.end, "done": c.done, "bytes_done": c.bytes_done}
            for c in state.chunks
        ],
    }
    try:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def load_state(file_name: str) -> Optional[dict]:
    path = _state_path(file_name)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def clear_state(file_name: str) -> None:
    path = _state_path(file_name)
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


@dataclass
class ServerInfo:
    total_bytes:    int
    accepts_range:  bool
    suggested_name: str
    content_type:   str


# ─────────────────────────────────────────────────────────────────────────────
# Content validation + WAF/CDN block detection
# ─────────────────────────────────────────────────────────────────────────────

def looks_like_html(data: bytes) -> bool:
    try:
        head = data[:SNIFF_SIZE].lstrip()[:512].lower()
    except Exception:
        return False
    return any(m in head for m in HTML_MARKERS)


def _expected_binary(content_type: str, filename: str) -> bool:
    ct = (content_type or "").lower()
    if "html" in ct:
        return False
    ext = "." + (filename.rsplit(".", 1)[-1].lower() if "." in filename else "")
    if ext in BINARY_EXTS:
        return True
    if ct.startswith(("video/", "audio/")):
        return True
    if ct in ("application/octet-stream", "application/pdf",
              "application/zip", "application/x-iso9660-image",
              "application/x-msdownload"):
        return True
    if ct.startswith("application/") and "json" not in ct and "xml" not in ct and "text" not in ct:
        return True
    return False


def sniff_format(data: bytes) -> Optional[str]:
    """Best-effort magic-byte sniff. Returns a family name from
    MAGIC_SIGNATURES (e.g. 'zip', 'ftyp', 'png') or None if nothing matched.
    Never raises — a sniff failure just means 'unknown', not 'html'."""
    try:
        if not data:
            return None
        for family, sigs in MAGIC_SIGNATURES.items():
            for offset, sig in sigs:
                if len(data) >= offset + len(sig) and data[offset:offset + len(sig)] == sig:
                    if family in ("wav", "avi", "webp"):
                        sub = data[8:12]
                        resolved = _RIFF_SUBTYPES.get(sub)
                        if resolved:
                            return resolved
                        continue  # RIFF but unknown subtype: don't claim a false family
                    return family
        return None
    except Exception:
        return None


def magic_mismatch(data: bytes, filename: str, content_type: str) -> Optional[str]:
    """Phase 1.1 (extended) — compare sniffed magic bytes against what the
    extension/content-type promised. Returns a human-readable reason string
    if there's a confident contradiction, or None if OK / inconclusive.

    Deliberately conservative: only fires when we positively identified a
    *different* known binary family than the one implied by the filename —
    an unmatched/unknown sniff is never treated as a mismatch, since many
    legitimate files won't hit any signature in the table.
    """
    try:
        ext = "." + (filename.rsplit(".", 1)[-1].lower() if "." in filename else "")
        expected_families = _EXT_FAMILY.get(ext)
        if not expected_families:
            return None  # unknown extension: nothing to compare against
        detected = sniff_format(data)
        if detected is None:
            return None  # inconclusive: don't flag on lack of a match
        if detected in expected_families:
            return None
        return f"expected '{ext}' ({'/'.join(sorted(expected_families))}) but content sniffs as '{detected}'"
    except Exception:
        return None


def _body_indicates_block(peek: bytes) -> bool:
    try:
        low = peek[:4096].lower()
    except Exception:
        return False
    return any(m in low for m in WAF_BODY_MARKERS)


def is_block_response(resp, peek: bytes | None = None) -> tuple[bool, str]:
    """Classify a response as WAF/CDN challenge vs. plain HTTP. Returns (blocked, reason)."""
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
        return True, f"redirect to challenge ({final_url[:80]})"
    body = peek if peek is not None else b""
    if body and _body_indicates_block(body):
        return True, "challenge marker in body"
    return False, ""


def pick_ua(attempt: int, blocked: bool) -> str:
    if not blocked:
        return "Reliable Asynchronous Parallel Internet Downloader"
    try:
        return UA_POOL[(max(1, attempt) - 1) % len(UA_POOL)]
    except Exception:
        return UA_POOL[0]


def make_session():
    """Phase 4 — prefer curl_cffi (browser TLS fingerprint), fallback to requests.

    Keeps the requests-compatible surface used by worker()/inspect_url():
    .get/.head(stream/timeout/headers), .headers, resp.(status_code/headers/url/
    iter_content/raise_for_status).
    """
    try:
        if importlib.util.find_spec("curl_cffi") is not None:
            from curl_cffi import requests as curl_requests  # type: ignore
            try:
                session = curl_requests.Session(impersonate="chrome124")
            except TypeError:
                session = curl_requests.Session()
            try:
                session.headers.update({
                    "Accept-Encoding": "identity",
                    "Connection":      "keep-alive",
                })
            except Exception:
                pass
            try:
                _EARLY_LOG.info("HTTP backend: curl_cffi (chrome124 impersonation)")
            except Exception:
                pass
            return session
    except Exception as e:
        try:
            _EARLY_LOG.warning(f"curl_cffi unavailable, falling back to requests ({e})")
        except Exception:
            pass
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


def inspect_url(session, url: str, log: logging.Logger) -> ServerInfo:
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
        try:
            final_u = getattr(resp, "url", url)
        except Exception:
            final_u = url
        name = unquote(Path(urlparse(final_u).path).name) or "download"

    log.info(f"HEAD {url} -> {total/1e6:.2f} MB | Range={accepts} | type={ctype}")

    try:
        probe = session.get(
            url, headers={"Range": f"bytes=0-{PROBE_RANGE_SIZE}"},
            stream=False, timeout=(CONNECT_TIMEOUT, 15), allow_redirects=True,
        )
        try:
            peek = bytes(getattr(probe, "content", b"") or b"")[:4096]
        except Exception:
            peek = b""
        blocked, reason = is_block_response(probe, peek)
        if blocked:
            log.warning(f"inspect probe: block/challenge detected ({reason}) — not starting blind download")
            raise RuntimeError(
                "Server returned a block/challenge page instead of the file "
                f"(possible WAF/CDN protection: {reason}). "
                "Try a warmed session/referer page or retry later."
            )
        probe_ctype = ""
        try:
            probe_ctype = str(probe.headers.get("Content-Type", ctype))
        except Exception:
            probe_ctype = ctype
        final_name = name
        try:
            final_u2 = str(getattr(probe, "url", "") or "")
            if final_u2:
                cand = unquote(Path(urlparse(final_u2).path).name)
                if cand:
                    final_name = cand
        except Exception:
            pass
        if peek and looks_like_html(peek) and _expected_binary(probe_ctype, final_name):
            log.warning("inspect probe: first bytes look like HTML, not the file — aborting before big download")
            raise RuntimeError(
                "Server returned HTML instead of the file "
                "(possible block, captcha or expired link). "
                "Not starting the download."
            )
        if peek:
            mismatch = magic_mismatch(peek, final_name, probe_ctype)
            if mismatch:
                log.warning(f"inspect probe: magic-byte mismatch — {mismatch}")
                raise RuntimeError(
                    f"Server response does not match the expected file type ({mismatch}). "
                    "Not starting the download."
                )
    except RuntimeError:
        raise
    except Exception as e:
        log.debug(f"inspect probe skipped/failed ({e}), trusting HEAD")

    return ServerInfo(total_bytes=total, accepts_range=accepts,
                       suggested_name=name, content_type=ctype)


def _backoff(attempt: int, blocked: bool = False) -> None:
    base = min(2 ** (max(1, attempt) - 1), 30)
    if blocked:
        base = min(base * 1.5, 30)
    try:
        delay = base * (0.7 + random.random() * 0.6)
    except Exception:
        delay = float(base)
    time.sleep(delay)


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
    state_lock: Optional[threading.Lock] = None,
) -> None:
    while not stop_event.is_set():
        try:
            chunk = chunk_q.get_nowait()
        except queue.Empty:
            return

        success = False
        last_blocked = False
        last_html = False

        for attempt in range(1, MAX_RETRIES + 1):
            if stop_event.is_set():
                chunk_q.task_done()
                return

            start = chunk.start + chunk.bytes_done
            if start > chunk.end:
                chunk.done = True
                success = True
                break

            if attempt > 1 and (last_blocked or last_html):
                try:
                    probe_end = min(start + PROBE_SMALL_SIZE, chunk.end)
                    probe_resp = session.get(
                        state.url,
                        headers={
                            "Range": f"bytes={start}-{probe_end}",
                            "User-Agent": pick_ua(attempt, True),
                        },
                        stream=False,
                        timeout=(CONNECT_TIMEOUT, 15),
                        allow_redirects=True,
                    )
                    try:
                        probe_peek = bytes(getattr(probe_resp, "content", b"") or b"")[:4096]
                    except Exception:
                        probe_peek = b""
                    p_blocked, p_reason = is_block_response(probe_resp, probe_peek)
                    try:
                        probe_resp.close()
                    except Exception:
                        pass
                    if p_blocked:
                        log.warning(tr.t(
                            "chunk_blocked_detected",
                            worker_id=f"{worker_id:02d}", chunk=chunk.index,
                            status=getattr(probe_resp, "status_code", "?"), attempt=attempt,
                        ))
                        last_blocked = True
                        last_html = False
                        if attempt < MAX_RETRIES:
                            _backoff(attempt, blocked=True)
                        continue
                    try:
                        pct = str(probe_resp.headers.get("Content-Type", ""))
                    except Exception:
                        pct = ""
                    if probe_peek and looks_like_html(probe_peek) and _expected_binary(pct, state.file_name):
                        log.warning(tr.t(
                            "chunk_html_detected",
                            worker_id=f"{worker_id:02d}", chunk=chunk.index, attempt=attempt,
                        ))
                        last_blocked = False
                        last_html = True
                        if attempt < MAX_RETRIES:
                            _backoff(attempt, blocked=True)
                        continue
                    if probe_peek and chunk.start == 0:
                        mismatch0 = magic_mismatch(probe_peek, state.file_name, pct)
                        if mismatch0:
                            log.warning(f"[W{worker_id:02d}] chunk {chunk.index} magic-byte mismatch — {mismatch0}")
                            last_blocked = False
                            last_html = True
                            if attempt < MAX_RETRIES:
                                _backoff(attempt, blocked=True)
                            continue
                except Exception as e:
                    log.debug(f"[W{worker_id:02d}] chunk {chunk.index} small-probe failed ({e}), retrying full range")
                last_blocked = False
                last_html = False

            headers = {"Range": f"bytes={start}-{chunk.end}"}
            if last_blocked or (attempt > 1 and last_html):
                headers["User-Agent"] = pick_ua(attempt, True)
            try:
                resp = session.get(
                    state.url,
                    headers=headers,
                    stream=True,
                    timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                )
                resp.raise_for_status()

                if start > chunk.start and resp.status_code != 206:
                    log.warning(
                        f"[W{worker_id:02d}] chunk {chunk.index}: server did not honor Range "
                        f"(status {resp.status_code}), restarting chunk from the beginning"
                    )
                    start = chunk.start
                    chunk.bytes_done = 0

                try:
                    resp_ctype = str(resp.headers.get("Content-Type", ""))
                except Exception:
                    resp_ctype = ""
                write_pos = start
                buf = bytearray()
                interrupted = False
                poisoned = False
                first = True

                for data in resp.iter_content(chunk_size=CHUNK_SIZE):
                    if stop_event.is_set():
                        interrupted = True
                        break
                    if not data:
                        continue
                    if first:
                        first = False
                        try:
                            peek0 = bytes(data[:4096])
                        except Exception:
                            peek0 = b""
                        blocked0, reason0 = is_block_response(resp, peek0)
                        if blocked0:
                            log.warning(tr.t(
                                "chunk_blocked_detected",
                                worker_id=f"{worker_id:02d}", chunk=chunk.index,
                                status=getattr(resp, "status_code", "?"), attempt=attempt,
                            ))
                            last_blocked = True
                            last_html = False
                            poisoned = True
                            break
                        if peek0 and looks_like_html(peek0) and _expected_binary(resp_ctype, state.file_name):
                            log.warning(tr.t(
                                "chunk_html_detected",
                                worker_id=f"{worker_id:02d}", chunk=chunk.index, attempt=attempt,
                            ))
                            last_blocked = False
                            last_html = True
                            poisoned = True
                            break
                        if peek0 and chunk.start == 0:
                            mismatch1 = magic_mismatch(peek0, state.file_name, resp_ctype)
                            if mismatch1:
                                log.warning(f"[W{worker_id:02d}] chunk {chunk.index} magic-byte mismatch — {mismatch1}")
                                last_blocked = False
                                last_html = True
                                poisoned = True
                                break
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
                        chunk.bytes_done = write_pos - chunk.start
                        if state_lock is not None:
                            with state_lock:
                                save_state(state)

                try:
                    resp.close()
                except Exception:
                    pass

                if poisoned:
                    if attempt < MAX_RETRIES:
                        _backoff(attempt, blocked=True)
                    continue

                if buf:
                    with file_lock:
                        with open(state.file_name, "r+b") as f:
                            f.seek(write_pos)
                            f.write(bytes(buf))
                    write_pos += len(buf)
                    buf.clear()
                    chunk.bytes_done = write_pos - chunk.start
                    if state_lock is not None:
                        with state_lock:
                            save_state(state)

                if interrupted:
                    chunk_q.task_done()
                    return

                chunk.done = True
                chunk.bytes_done = chunk.size
                success = True
                last_blocked = False
                last_html = False
                log.info(tr.t("chunk_ok", worker_id=f"{worker_id:02d}", chunk=chunk.index, size=f"{chunk.size/1e6:.1f}"))
                if state_lock is not None:
                    with state_lock:
                        save_state(state)
                break

            except requests.exceptions.Timeout:
                log.warning(tr.t("chunk_timeout", worker_id=f"{worker_id:02d}", chunk=chunk.index, attempt=attempt))
                last_blocked = False
            except requests.exceptions.ChunkedEncodingError:
                log.warning(tr.t("chunk_stream_interrupted", worker_id=f"{worker_id:02d}", chunk=chunk.index, attempt=attempt))
                last_blocked = False
            except requests.exceptions.ConnectionError as e:
                log.warning(tr.t("chunk_connection_error", worker_id=f"{worker_id:02d}", chunk=chunk.index, error=e, attempt=attempt))
                last_blocked = False
            except requests.exceptions.HTTPError as e:
                eresp = getattr(e, "response", None)
                status = eresp.status_code if eresp is not None and hasattr(eresp, "status_code") else "?"
                peek_h = b""
                try:
                    if eresp is not None:
                        peek_h = bytes(getattr(eresp, "content", b"") or b"")[:4096]
                except Exception:
                    peek_h = b""
                blocked_h, _r = is_block_response(eresp, peek_h) if eresp is not None else (False, "")
                if blocked_h or (isinstance(status, int) and status in (403, 429) and peek_h and _body_indicates_block(peek_h)):
                    log.warning(tr.t("chunk_blocked_detected", worker_id=f"{worker_id:02d}", chunk=chunk.index, status=status, attempt=attempt))
                    last_blocked = True
                else:
                    log.warning(tr.t("chunk_http_error", worker_id=f"{worker_id:02d}", chunk=chunk.index, status=status, attempt=attempt))
                    last_blocked = False
            except OSError as e:
                log.error(tr.t("chunk_disk_error", worker_id=f"{worker_id:02d}", chunk=chunk.index, error=e))
                chunk_q.task_done()
                return
            except Exception as e:
                status_x = getattr(getattr(e, "response", None), "status_code", "?")
                log.warning(tr.t("chunk_connection_error", worker_id=f"{worker_id:02d}", chunk=chunk.index, error=e, attempt=attempt))
                last_blocked = ("block" in str(e).lower() or "challenge" in str(e).lower()
                                or "captcha" in str(e).lower())

            if attempt < MAX_RETRIES:
                _backoff(attempt, blocked=last_blocked)

        if not success:
            log.error(tr.t("chunk_failed", worker_id=f"{worker_id:02d}", chunk=chunk.index, retries=MAX_RETRIES))

        chunk_q.task_done()


# ─────────────────────────────────────────────────────────────────────────────
# GUI (PySide6 + QFluentWidgets)
# ─────────────────────────────────────────────────────────────────────────────

def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _show_warning(parent: QWidget, title: str, text: str) -> None:
    try:
        if FluentMessageBox is not None:
            box = FluentMessageBox(title, text, parent)
            box.exec()
            return
    except Exception:
        pass
    QMessageBox.warning(parent, title, text)


def _show_info(parent: QWidget, title: str, text: str) -> None:
    try:
        if FluentMessageBox is not None:
            box = FluentMessageBox(title, text, parent)
            box.exec()
            return
    except Exception:
        pass
    QMessageBox.information(parent, title, text)


def _ask_yes_no(parent: QWidget, title: str, text: str) -> bool:
    try:
        if FluentMessageBox is not None:
            box = FluentMessageBox(title, text, parent)
            return bool(box.exec())
    except Exception:
        pass
    return QMessageBox.question(parent, title, text) == QMessageBox.StandardButton.Yes


class RapidWindow(FluentBaseWidget):
    """Frameless Fluent window. Same download behavior as before — new look."""

    # Thread-safe UI updates (emitted from the download thread, applied on GUI thread)
    _sig_filename = Signal(str)
    _sig_info = Signal(str)
    _sig_parts = Signal(int)
    _sig_finish = Signal(bool)
    _sig_retranslate = Signal()

    def __init__(self):
        super().__init__()

        try:
            self.log = setup_logging(clear=True)
        except NameError:
            self.log = setup_logging(clear=True)

        self.log_q: "queue.Queue[str]" = queue.Queue()
        self._gui_log_handler = _GuiQueueHandler(self.log_q)
        self._gui_log_handler.setFormatter(logging.Formatter("%(message)s"))
        self._gui_log_handler.setLevel(logging.DEBUG)
        self.log.addHandler(self._gui_log_handler)

        self.log.info("GUI initialized (Qt/Fluent)")
        self.tr = Translator(LANGUAGES_DIR, self.log)
        self.th = ThemeManager(THEMES_DIR, self.log)
        self._load_preferences()

        self.setObjectName("rapidRoot")
        self.setWindowTitle(self.tr.t("window_title"))
        self.resize(680, 640)
        try:
            self.setMinimumSize(620, 560)
        except Exception:
            pass
        # Identical look on every OS: no mica/acrylic compositor blur, the
        # theme palette is drawn by Qt itself (see UPDATE.md — no pywinstyles).
        for _meth in ("setMicaEffectEnabled", "setAcrylicEnabled"):
            try:
                getattr(self, _meth)(False)
            except Exception:
                pass

        self.stop_event = threading.Event()
        self.download_thread: Optional[threading.Thread] = None
        self.state: Optional[DownloadState] = None
        self.info: Optional[ServerInfo] = None
        self.start_time = 0.0
        self.running = False

        self._rgb_hue: float = 200.0
        self._rgb_interval_ms: int = int(self.th.cycle_params.get("rgb", {}).get("cycle_interval_ms", 80))
        self._rgb_step_deg: float = float(self.th.cycle_params.get("rgb", {}).get("cycle_step_deg", 0.5))
        self._rgb_interval_ms = max(50, min(1000, self._rgb_interval_ms))
        self._rgb_step_deg = max(0.05, min(3.0, self._rgb_step_deg))
        self._rgb_timer = QTimer(self)
        self._rgb_timer.timeout.connect(self._rgb_tick)

        self._log_buffer: "list[str]" = []
        self._fade_counter = 0

        self._filename_user_edited = False
        self._suppress_filename_trace = False
        self._last_url_for_name = ""
        self._updating_url_programmatically = False

        self._build_ui()
        self._wire_signals()
        self._apply_theme()
        self._retranslate_static_ui()

        self._poll_log_timer = QTimer(self)
        self._poll_log_timer.timeout.connect(self._poll_log)
        self._poll_log_timer.start(150)

        self._drain_timer = QTimer(self)
        self._drain_timer.timeout.connect(self._drain_log_buffer)
        self._drain_timer.start(90)

        self._poll_progress_timer = QTimer(self)
        self._poll_progress_timer.timeout.connect(self._poll_progress)
        self._poll_progress_timer.start(300)

        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1000)
        self._update_clock()

    def showEvent(self, event):
        super().showEvent(event)
        # Now that the frameless title bar has been laid out, its height is
        # reliable — reserve the exact space instead of the fixed fallback
        # used in _build_ui(). Fixes content overlapping the title bar.
        try:
            th = int(self.titleBar.height())
            if th > 0:
                lay = self.layout()
                if lay is not None:
                    lay.setContentsMargins(12, th + 4, 12, 12)
        except Exception:
            pass

    # ── UI construction ──

    def _build_ui(self):
        root = QVBoxLayout(self)
        # Leave room for the Fluent title bar (custom, frameless). Its real
        # height isn't reliable yet here (layout hasn't been activated), so
        # use a safe fixed fallback; showEvent() below corrects it once the
        # title bar has its real, laid-out height.
        root.setContentsMargins(12, 44, 12, 12)
        root.setSpacing(8)

        hero = QHBoxLayout()
        hero.setSpacing(8)
        self.lbl_hero = QLabel("⚡ RAPID")
        self.lbl_hero.setObjectName("titleAccent")
        hero.addWidget(self.lbl_hero)
        hero.addStretch(1)
        self.lbl_clock = QLabel("")
        self.lbl_clock.setObjectName("clock")
        hero.addWidget(self.lbl_clock)
        root.addLayout(hero)

        top_card = CardWidget(self)
        top_bar = QHBoxLayout(top_card)
        top_bar.setContentsMargins(12, 10, 12, 10)
        top_bar.setSpacing(8)

        self.lbl_lang = QLabel(self.tr.t("language_label"))
        top_bar.addWidget(self.lbl_lang)
        self._lang_codes = self.tr.available()
        self.lang_combo = ComboBox(top_card)
        try:
            self.lang_combo.addItems([name for _, name in self._lang_codes])
            self.lang_combo.setCurrentText(dict(self._lang_codes).get(self.tr.current, "English"))
        except Exception:
            pass
        self.lang_combo.setMinimumWidth(170)
        top_bar.addWidget(self.lang_combo)

        self.lbl_theme = QLabel(self.tr.t("theme_label"))
        top_bar.addWidget(self.lbl_theme)
        self._theme_codes = self.th.available()
        self.theme_combo = ComboBox(top_card)
        try:
            self.theme_combo.addItems([name for _, name in self._theme_codes])
            self.theme_combo.setCurrentText(dict(self._theme_codes).get(self.th.current, "Light"))
        except Exception:
            pass
        self.theme_combo.setMinimumWidth(150)
        top_bar.addWidget(self.theme_combo)
        top_bar.addStretch(1)
        self._committed_theme = self.th.current
        root.addWidget(top_card)

        src_card = CardWidget(self)
        src_layout = QVBoxLayout(src_card)
        src_layout.setContentsMargins(12, 10, 12, 10)
        src_layout.setSpacing(8)
        self.frm_top_title = QLabel(self.tr.t("source_frame"))
        self.frm_top_title.setObjectName("muted")
        src_layout.addWidget(self.frm_top_title)

        url_row = QGridLayout()
        url_row.setColumnStretch(1, 1)
        url_row.setVerticalSpacing(10)
        self.lbl_url = QLabel(self.tr.t("url_label"))
        url_row.addWidget(self.lbl_url, 0, 0)
        self.url_edit = LineEdit(src_card)
        self.url_edit.setPlaceholderText("https://…")
        self.url_edit.setClearButtonEnabled(True)
        url_row.addWidget(self.url_edit, 0, 1)
        self.btn_verify = PushButton(self.tr.t("verify_btn"), src_card)
        url_row.addWidget(self.btn_verify, 0, 2)

        self.lbl_save_as = QLabel(self.tr.t("save_as_label"))
        url_row.addWidget(self.lbl_save_as, 1, 0)
        self.filename_edit = LineEdit(src_card)
        url_row.addWidget(self.filename_edit, 1, 1)
        self.btn_browse = PushButton(self.tr.t("browse_btn"), src_card)
        url_row.addWidget(self.btn_browse, 1, 2)

        self.lbl_workers = QLabel(self.tr.t("workers_label"))
        url_row.addWidget(self.lbl_workers, 2, 0)
        self.spin_workers = SpinBox(src_card)
        try:
            self.spin_workers.setRange(1, MAX_WORKERS)
            self.spin_workers.setValue(DEFAULT_WORKERS)
        except Exception:
            pass
        # Fixed (not just capped) width — sizeHint alone gave Workers and
        # Parts different natural widths even though both just show 1-5
        # digit numbers. Same literal pixel size on both, no ambiguity.
        self.spin_workers.setFixedWidth(150)
        url_row.addWidget(self.spin_workers, 2, 1, Qt.AlignLeft)

        self.lbl_parts = QLabel(self.tr.t("parts_label"))
        url_row.addWidget(self.lbl_parts, 3, 0)
        parts_box = QHBoxLayout()
        parts_box.setContentsMargins(0, 0, 0, 0)
        parts_box.setSpacing(8)
        self.spin_parts = SpinBox(src_card)
        try:
            self.spin_parts.setRange(0, 10000)
            self.spin_parts.setValue(0)
        except Exception:
            pass
        self.spin_parts.setFixedWidth(150)
        parts_box.addWidget(self.spin_parts)
        self.lbl_parts_actual = QLabel("")
        self.lbl_parts_actual.setObjectName("muted")
        parts_box.addWidget(self.lbl_parts_actual)
        parts_box.addStretch(1)
        parts_wrap = QWidget(src_card)
        parts_wrap.setLayout(parts_box)
        # Without this, a bare QWidget doesn't stretch to fill the grid cell
        # like QSpinBox/QLineEdit do above it — it hugs its content's minimum
        # width instead, breaking the row-to-row alignment.
        parts_wrap.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        url_row.addWidget(parts_wrap, 3, 1, 1, 2)

        self.lbl_info = QLabel(self.tr.t("info_default"))
        self.lbl_info.setObjectName("muted")
        self.lbl_info.setWordWrap(True)
        url_row.addWidget(self.lbl_info, 4, 0, 1, 3)
        src_layout.addLayout(url_row)
        root.addWidget(src_card)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.btn_start = PrimaryPushButton(self.tr.t("download_btn"))
        actions.addWidget(self.btn_start)
        self.btn_cancel = PushButton(self.tr.t("cancel_btn"))
        try:
            self.btn_cancel.setEnabled(False)
        except Exception:
            pass
        actions.addWidget(self.btn_cancel)
        actions.addStretch(1)
        self.btn_open_log = PushButton(self.tr.t("open_log_btn"))
        actions.addWidget(self.btn_open_log)
        root.addLayout(actions)

        prog_card = CardWidget(self)
        prog_layout = QVBoxLayout(prog_card)
        prog_layout.setContentsMargins(12, 10, 12, 10)
        prog_layout.setSpacing(6)
        self.frm_prog_title = QLabel(self.tr.t("progress_frame"))
        self.frm_prog_title.setObjectName("muted")
        prog_layout.addWidget(self.frm_prog_title)
        self.progress = ProgressBar(prog_card)
        try:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
        except Exception:
            pass
        prog_layout.addWidget(self.progress)
        self.lbl_status = QLabel(self.tr.t("status_ready"))
        prog_layout.addWidget(self.lbl_status)
        root.addWidget(prog_card)

        log_card = CardWidget(self)
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(12, 10, 12, 10)
        log_layout.setSpacing(6)
        self.frm_log_title = QLabel(self.tr.t("log_frame"))
        self.frm_log_title.setObjectName("muted")
        log_layout.addWidget(self.frm_log_title)
        self.log_text = QTextEdit(log_card)
        self.log_text.setReadOnly(True)
        try:
            mono = QFont("Consolas", 9)
            mono.setStyleHint(QFont.StyleHint.Monospace)
            self.log_text.setFont(mono)
        except Exception:
            pass
        log_layout.addWidget(self.log_text, 1)
        root.addWidget(log_card, 1)

        self.lbl_stats = QLabel("")
        self.lbl_stats.setObjectName("muted")
        root.addWidget(self.lbl_stats)

    def _wire_signals(self):
        try:
            self.btn_start.clicked.connect(self._on_start)
            self.btn_cancel.clicked.connect(self._on_cancel)
            self.btn_verify.clicked.connect(self._on_inspect)
            self.btn_browse.clicked.connect(self._on_browse)
            self.btn_open_log.clicked.connect(self._open_log)
            self.lang_combo.currentTextChanged.connect(self._on_language_change)
            self.theme_combo.currentTextChanged.connect(self._on_theme_highlight)
            self.theme_combo.activated.connect(self._on_theme_commit)
            try:
                self.theme_combo.popupHidden.connect(self._on_theme_popup_hidden)
            except Exception:
                pass
            try:
                self.theme_combo.highlighted.connect(self._on_theme_highlighted_index)
            except Exception:
                pass
            self.url_edit.textChanged.connect(self._on_url_changed)
            self.filename_edit.textChanged.connect(self._on_filename_edited)
        except Exception as e:
            self.log.warning(f"Signal wiring incomplete: {e}")
        try:
            self._sig_filename.connect(self._set_filename_auto)
            self._sig_info.connect(self.lbl_info.setText)
            self._sig_parts.connect(self._set_parts_actual)
            self._sig_finish.connect(self._finish_on_gui)
        except Exception as e:
            self.log.warning(f"Internal signal wiring failed: {e}")

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

    def _on_language_change(self, _text=None):
        name_to_code = {name: code for code, name in self._lang_codes}
        try:
            current_name = self.lang_combo.currentText()
        except Exception:
            current_name = ""
        code = name_to_code.get(current_name, DEFAULT_LANG)
        self.tr.set_language(code)
        save_app_config(language=code, log=self.log)
        self._retranslate_static_ui()

    def _retranslate_static_ui(self):
        """Updates all static widget texts after a language change (dynamic
        log/status text keeps whatever language it was generated in)."""
        try:
            self.setWindowTitle(self.tr.t("window_title"))
            self.lbl_lang.setText(self.tr.t("language_label"))
            self.lbl_theme.setText(self.tr.t("theme_label"))
            self.frm_top_title.setText(self.tr.t("source_frame"))
            self.lbl_url.setText(self.tr.t("url_label"))
            self.btn_verify.setText(self.tr.t("verify_btn"))
            self.lbl_save_as.setText(self.tr.t("save_as_label"))
            self.btn_browse.setText(self.tr.t("browse_btn"))
            self.lbl_workers.setText(self.tr.t("workers_label"))
            self.lbl_parts.setText(self.tr.t("parts_label"))
            try:
                self.spin_workers.setToolTip(self.tr.t("workers_help"))
                self.lbl_workers.setToolTip(self.tr.t("workers_help"))
                self.spin_parts.setToolTip(self.tr.t("parts_help"))
                self.lbl_parts.setToolTip(self.tr.t("parts_help"))
            except Exception:
                pass
            self.btn_start.setText(self.tr.t("download_btn"))
            self.btn_cancel.setText(self.tr.t("cancel_btn"))
            self.btn_open_log.setText(self.tr.t("open_log_btn"))
            self.frm_prog_title.setText(self.tr.t("progress_frame"))
            self.frm_log_title.setText(self.tr.t("log_frame"))
            if not self.running and self.state is None:
                self.lbl_status.setText(self.tr.t("status_ready"))
            if self.info is None:
                self.lbl_info.setText(self.tr.t("info_default"))
        except Exception as e:
            self.log.debug(f"Retranslate skipped ({e}).")

    # ── Theming (ThemeManager stays the source of truth; QSS is the consumer) ──

    def _on_theme_highlight(self, _text=None):
        # Live preview while navigating the combo (arrows/hover), no persistence.
        self._preview_current_combo_theme()

    def _on_theme_highlighted_index(self, _index=None):
        self._preview_current_combo_theme()

    def _preview_current_combo_theme(self) -> None:
        try:
            name = self.theme_combo.currentText()
        except Exception:
            return
        name_to_code = {name: code for code, name in self._theme_codes}
        code = name_to_code.get(name)
        if code:
            self._preview_theme(code)

    def _on_theme_commit(self, _index=None):
        name_to_code = {name: code for code, name in self._theme_codes}
        try:
            name = self.theme_combo.currentText()
        except Exception:
            name = ""
        code = name_to_code.get(name, DEFAULT_THEME)
        self.th.set_theme(code)
        save_app_config(theme=code, log=self.log)
        self._committed_theme = code
        self._apply_theme()

    def _on_theme_popup_hidden(self) -> None:
        # If the popup closed without committing, revert the preview.
        try:
            name = self.theme_combo.currentText()
        except Exception:
            return
        name_to_code = {name: code for code, name in self._theme_codes}
        code = name_to_code.get(name)
        if code is None or code == self._committed_theme:
            if self.th.current != self._committed_theme:
                self.th.set_theme(self._committed_theme)
                self._apply_theme()
        # If the highlighted item differs from committed, Qt already keeps the
        # combo text — treat a popup close as cancel unless activated fired.
        if code != self._committed_theme and self.th.current != self._committed_theme:
            try:
                committed_name = dict(self._theme_codes).get(self._committed_theme, self._committed_theme)
                self.theme_combo.blockSignals(True)
                self.theme_combo.setCurrentText(committed_name)
            except Exception:
                pass
            finally:
                try:
                    self.theme_combo.blockSignals(False)
                except Exception:
                    pass
            self.th.set_theme(self._committed_theme)
            self._apply_theme()

    def _preview_theme(self, code: str) -> None:
        """Apply theme immediately without persisting to config.cfg (preview)."""
        if code not in self.th.catalogs:
            return
        if code == self.th.current:
            return
        self.th.set_theme(code)
        self._apply_theme()

    def _apply_theme(self):
        c = self.th.colors()
        try:
            self.setStyleSheet(build_qss(c))
        except Exception as e:
            self.log.debug(f"QSS apply skipped ({e}).")
        # Keep Fluent internals (popups, scrollbars) in matching light/dark mode
        # and tint them with the theme accent.
        try:
            if _HAS_FLUENT and _fluent_setTheme is not None and _FluentTheme is not None:
                _fluent_setTheme(
                    _FluentTheme.DARK if theme_luminance(c) < 128 else _FluentTheme.LIGHT
                )
            if _HAS_FLUENT and _fluent_setThemeColor is not None:
                _fluent_setThemeColor(QColor(c.get("accent_color", "#3a7bd5")))
        except Exception as e:
            self.log.debug(f"Fluent theme sync skipped ({e}).")
        # Title-bar tint comes straight from the theme (configurable from Python).
        try:
            bar = getattr(self, "titleBar", None)
            if bar is not None:
                # Plain QWidget subclasses (which TitleBar is) ignore the
                # stylesheet's background-color unless this is set — without
                # it Qt silently keeps painting the default system color.
                bar.setAttribute(Qt.WA_StyledBackground, True)
                # No border-bottom here: `bar` only covers the icon+title
                # area, not the full width up to the min/max/close buttons,
                # so a border on it reads as a stray underline instead of a
                # clean divider. Background/text tint only.
                bar.setStyleSheet(
                    f"background-color: {c.get('background_color', '#2b2b2b')};"
                    f"color: {c.get('text_color', '#ffffff')};"
                )
                title_lbl = getattr(bar, "titleLabel", None)
                if title_lbl is not None:
                    title_lbl.setStyleSheet(
                        "background: transparent; border: none; text-decoration: none;"
                    )
        except Exception:
            pass

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
        self._rgb_interval_ms = max(50, min(1000, interval))
        self._rgb_step_deg = max(0.05, min(3.0, step))

    def _start_rgb_cycle(self) -> None:
        if self._rgb_timer.isActive():
            return
        self._refresh_rgb_params()
        self._update_rgb_catalog()
        self._apply_rgb_colors()
        self._rgb_timer.start(self._rgb_interval_ms)

    def _stop_rgb_cycle(self) -> None:
        try:
            self._rgb_timer.stop()
        except Exception:
            pass

    def _rgb_tick(self) -> None:
        if self.th.current != "rgb":
            try:
                self._rgb_timer.stop()
            except Exception:
                pass
            return
        self._refresh_rgb_params()
        try:
            if self._rgb_timer.interval() != self._rgb_interval_ms:
                self._rgb_timer.setInterval(self._rgb_interval_ms)
        except Exception:
            pass
        self._rgb_hue = (self._rgb_hue + self._rgb_step_deg) % 360
        self._update_rgb_catalog()
        self._apply_rgb_colors()

    def _apply_rgb_colors(self) -> None:
        c = self.th.catalogs.get("rgb", self.th.colors())
        try:
            self.setStyleSheet(build_qss(c))
        except Exception:
            pass
        try:
            if _HAS_FLUENT and _fluent_setThemeColor is not None:
                _fluent_setThemeColor(QColor(c.get("accent_color", "#00e5ff")))
        except Exception:
            pass

    # ── Log view with fade-in ──

    @staticmethod
    def _hex_to_rgb(hexcolor: str):
        hexcolor = hexcolor.lstrip("#")
        return tuple(int(hexcolor[i:i + 2], 16) for i in (0, 2, 4))

    @staticmethod
    def _rgb_to_hex(rgb) -> str:
        return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(v)))) for v in rgb)

    def _append_log(self, msg: str):
        c = self.th.colors()
        bg = c.get("log_background_color", "#000000")
        fg = c.get("log_text_color", "#dddddd")
        try:
            cursor = self.log_text.textCursor()
            cursor.movePosition(QTextCursor.MoveMode.End)
            block_start = cursor.position()
            cursor.insertText(msg + "\n")
            self._fade_step(block_start, cursor.position(), self._hex_to_rgb(bg), self._hex_to_rgb(fg), 0, 12)
            try:
                sb = self.log_text.verticalScrollBar()
                sb.setValue(sb.maximum())
            except Exception:
                pass
        except Exception:
            try:
                self.log_text.append(msg)
            except Exception:
                pass

    def _fade_step(self, start_pos: int, end_pos: int, start_rgb, end_rgb, step: int, total: int):
        try:
            t = step / total
            cur = tuple(s + (e - s) * t for s, e in zip(start_rgb, end_rgb))
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(self._rgb_to_hex(cur)))
            cursor = self.log_text.textCursor()
            cursor.setPosition(start_pos)
            cursor.setPosition(end_pos, QTextCursor.MoveMode.KeepAnchor)
            cursor.mergeCharFormat(fmt)
        except Exception:
            return
        if step < total:
            QTimer.singleShot(22, lambda: self._fade_step(start_pos, end_pos, start_rgb, end_rgb, step + 1, total))

    def _queue_log(self, msg: str, level: int = logging.INFO) -> None:
        """Log a message — it reaches the GUI automatically via the logger's GUI handler."""
        try:
            self.log.log(level, msg)
        except Exception:
            pass

    # ── "Save as" auto-naming vs. user's own choice ──

    def _set_filename_auto(self, value: str) -> None:
        """Programmatic filename update (derived from the URL/server) — never
        counts as the user having picked their own name."""
        self._suppress_filename_trace = True
        try:
            if self._updating_url_programmatically:
                return
            try:
                self.filename_edit.blockSignals(True)
            except Exception:
                pass
            try:
                self.filename_edit.setText(value)
            finally:
                try:
                    self.filename_edit.blockSignals(False)
                except Exception:
                    pass
        finally:
            self._suppress_filename_trace = False

    def _on_filename_edited(self, *_args) -> None:
        if not self._suppress_filename_trace:
            self._filename_user_edited = True

    def _on_url_changed(self, *_args) -> None:
        try:
            url = self.url_edit.text().strip()
        except Exception:
            return
        if url == self._last_url_for_name:
            return
        self._last_url_for_name = url
        if not self._filename_user_edited:
            self._set_filename_auto("")
            self.info = None
            try:
                self.lbl_info.setText(self.tr.t("info_default"))
            except Exception:
                pass

    def _update_clock(self):
        try:
            self.lbl_clock.setText(time.strftime("%H:%M:%S"))
        except Exception:
            pass

    # ── Actions ──

    def _on_browse(self):
        try:
            current = self.filename_edit.text() or "download"
        except Exception:
            current = "download"
        path, _ = QFileDialog.getSaveFileName(self, self.tr.t("save_as_label"), current)
        if path:
            try:
                self.filename_edit.blockSignals(True)
                self.filename_edit.setText(path)
            except Exception:
                pass
            finally:
                try:
                    self.filename_edit.blockSignals(False)
                except Exception:
                    pass
            self._filename_user_edited = True

    def _on_inspect(self):
        try:
            url = self.url_edit.text().strip()
        except Exception:
            url = ""
        if not url:
            _show_warning(self, self.tr.t("app_title"), self.tr.t("warn_need_url_verify"))
            return

        def task():
            try:
                info = inspect_url(make_session(), url, self.log)
            except Exception as e:
                self.log.error(self.tr.t("error_checking_url", error=e))
                return
            self.info = info
            if not self._filename_user_edited:
                try:
                    self._sig_filename.emit(info.suggested_name)
                except Exception:
                    pass
            try:
                self._sig_info.emit(self.tr.t(
                    "info_format",
                    size=human_size(info.total_bytes),
                    range=self.tr.t("range_yes") if info.accepts_range else self.tr.t("range_no"),
                    type=info.content_type,
                ))
            except Exception:
                pass
            self.log.info(self.tr.t(
                "verified_msg", url=url, size=human_size(info.total_bytes), range=info.accepts_range,
            ))

        threading.Thread(target=task, daemon=True).start()

    def _on_start(self):
        if self.running:
            return
        try:
            url = self.url_edit.text().strip()
        except Exception:
            url = ""
        if not url:
            _show_warning(self, self.tr.t("app_title"), self.tr.t("warn_need_url"))
            return

        try:
            raw_filename = self.filename_edit.text().strip()
        except Exception:
            raw_filename = ""
        auto_name = not self._filename_user_edited
        file_name = raw_filename or unquote(Path(urlparse(url).path).name) or "download"
        self._set_filename_auto(file_name)
        try:
            n_workers = max(1, min(int(self.spin_workers.value()), MAX_WORKERS))
        except Exception:
            n_workers = DEFAULT_WORKERS
        try:
            n_parts = max(0, int(self.spin_parts.value()))
        except Exception:
            n_parts = 0

        self.stop_event = threading.Event()
        self.running = True
        try:
            self.btn_start.setEnabled(False)
            self.btn_cancel.setEnabled(True)
            self.progress.setValue(0)
            self.lbl_status.setText(self.tr.t("status_checking"))
            self.lbl_parts_actual.setText("")
        except Exception:
            pass

        prevent_sleep(self.log)

        self.download_thread = threading.Thread(
            target=self._run_download, args=(url, file_name, n_workers, n_parts, auto_name), daemon=True
        )
        self.download_thread.start()

    def _on_cancel(self):
        if self.running:
            self.stop_event.set()
            self.log.info(self.tr.t("cancel_requested"))

    def closeEvent(self, event):
        try:
            self._stop_rgb_cycle()
        except Exception:
            pass
        if self.running:
            if not _ask_yes_no(self, self.tr.t("confirm_close_title"), self.tr.t("confirm_close_msg")):
                try:
                    event.ignore()
                except Exception:
                    pass
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
        try:
            event.accept()
        except Exception:
            pass

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
        # Universal Qt opener — same call on every OS.
        try:
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(LOG_FILE))):
                raise RuntimeError("no handler")
        except Exception as e:
            _show_info(self, self.tr.t("log_file_title"), f"{LOG_FILE}\n\n{e}")

    # ── Download core (unchanged behavior; UI updates via signals) ──

    def _run_download(self, url: str, file_name: str, n_workers: int, n_parts: int = 0, auto_name: bool = False):
        session = make_session()
        self.start_time = time.monotonic()

        try:
            info = inspect_url(session, url, self.log)
        except Exception as e:
            self.log.error(self.tr.t("error_checking_url", error=e))
            try:
                self._sig_finish.emit(False)
            except Exception:
                self._finish_on_gui(False)
            return

        self.info = info
        self.log.info(self.tr.t(
            "size_range_type_msg",
            size=human_size(info.total_bytes),
            range=self.tr.t("range_yes") if info.accepts_range else self.tr.t("range_no"),
            type=info.content_type,
        ))

        if auto_name and info.suggested_name and info.suggested_name != file_name:
            file_name = info.suggested_name
            try:
                self._sig_filename.emit(file_name)
            except Exception:
                pass

        if not info.accepts_range or info.total_bytes < MIN_SPLIT_SIZE:
            fresh_chunks = [Chunk(index=0, start=0, end=max(info.total_bytes - 1, 0))]
            part_size_used = max(info.total_bytes, 1)
        else:
            if n_parts and n_parts > 0:
                part_size_used = max(-(-info.total_bytes // n_parts), MIN_SPLIT_SIZE)  # ceil division
            else:
                part_size_used = CHUNK_PART_SIZE

            fresh_chunks, offset, idx = [], 0, 0
            while offset < info.total_bytes:
                end = min(offset + part_size_used - 1, info.total_bytes - 1)
                fresh_chunks.append(Chunk(index=idx, start=offset, end=end))
                offset = end + 1
                idx += 1

        saved = load_state(file_name)
        resumed = bool(
            saved
            and saved.get("url") == url
            and int(saved.get("total_bytes", -1)) == info.total_bytes
            and Path(file_name).exists()
            and Path(file_name).stat().st_size == info.total_bytes
            and (len(saved.get("chunks", [])) <= 1 or info.accepts_range)
        )

        if resumed:
            chunks = [
                Chunk(
                    index=c["index"], start=c["start"], end=c["end"],
                    done=bool(c["done"]), bytes_done=int(c.get("bytes_done", 0)),
                )
                for c in saved["chunks"]
            ]
        else:
            chunks = fresh_chunks
            clear_state(file_name)

        self.state = DownloadState(
            url=url, file_name=file_name, total_bytes=info.total_bytes,
            n_workers=n_workers, chunks=chunks,
        )
        try:
            self._sig_parts.emit(len(chunks))
        except Exception:
            pass

        already_done = sum(c.bytes_done for c in chunks)
        if already_done:
            self.state._bytes_done = already_done

        if resumed:
            done_count = sum(1 for c in chunks if c.done)
            self.log.info(
                f"Resuming '{file_name}': {done_count}/{len(chunks)} chunks already complete "
                f"({human_size(already_done)} of {human_size(info.total_bytes)})."
            )
        else:
            try:
                with open(file_name, "wb") as f:
                    if info.total_bytes > 0:
                        f.seek(info.total_bytes - 1)
                        f.write(b"\x00")
            except OSError as e:
                self.log.error(self.tr.t("error_create_file", error=e))
                try:
                    self._sig_finish.emit(False)
                except Exception:
                    self._finish_on_gui(False)
                return
            save_state(self.state)

        self.log.info(self.tr.t(
            "starting_download",
            file=file_name, chunks=len(chunks), mb=part_size_used // 1024 // 1024, workers=n_workers,
        ))

        file_lock = threading.Lock()
        state_lock = threading.Lock()

        round_no = 0
        while round_no < MAX_ROUNDS:
            round_no += 1
            pending = [c for c in self.state.chunks if not c.done]
            if not pending:
                break
            if round_no > 1:
                self.log.warning(self.tr.t(
                    "retry_round", round=round_no, count=len(pending),
                ))
                try:
                    session.headers.update({"User-Agent": pick_ua(round_no, True)})
                except Exception:
                    pass
            chunk_q: "queue.Queue[Chunk]" = queue.Queue()
            for c in pending:
                chunk_q.put(c)
            n_active = max(1, min(n_workers, len(pending)))
            with ThreadPoolExecutor(max_workers=n_active) as executor:
                futures = [
                    executor.submit(
                        worker, i, session, self.state, chunk_q, file_lock, self.log, self.stop_event,
                        self.log_q, self.tr, state_lock,
                    )
                    for i in range(n_active)
                ]
                for f in as_completed(futures):
                    try:
                        f.result()
                    except Exception as e:
                        self.log.error(self.tr.t("worker_finished_error", error=e))
            if self.stop_event.is_set():
                break
            remaining = [c for c in self.state.chunks if not c.done]
            if not remaining:
                break
            if round_no >= MAX_ROUNDS:
                break

        if self.stop_event.is_set():
            save_state(self.state)
            self.log.info(self.tr.t("cancelled_by_user"))
            try:
                self._sig_finish.emit(False)
            except Exception:
                self._finish_on_gui(False)
            return

        failed = [c.index for c in self.state.chunks if not c.done]
        if failed:
            save_state(self.state)
            self.log.warning(self.tr.t("chunks_incomplete", count=len(failed), list=failed[:10]))
            try:
                self._sig_finish.emit(False)
            except Exception:
                self._finish_on_gui(False)
            return

        try:
            if _expected_binary(info.content_type, file_name):
                with open(file_name, "rb") as f:
                    head_final = f.read(SNIFF_SIZE)
                suspicious_size = (
                    info.total_bytes > 0
                    and Path(file_name).stat().st_size < FLATLINE_MIN_BYTES
                    and info.total_bytes > 1024 * 1024
                )
                mismatch_final = magic_mismatch(head_final, file_name, info.content_type) if head_final else None
                if (head_final and looks_like_html(head_final)) or suspicious_size or mismatch_final:
                    if mismatch_final:
                        self.log.error(f"flatline check: {mismatch_final}")
                    self.log.error(self.tr.t("flatline_suspect"))
                    save_state(self.state)
                    try:
                        self._sig_finish.emit(False)
                    except Exception:
                        self._finish_on_gui(False)
                    return
        except Exception as e:
            self.log.debug(f"flatline check skipped ({e})")

        clear_state(file_name)
        elapsed = time.monotonic() - self.start_time
        avg = info.total_bytes / elapsed / 1e6 if elapsed > 0 else 0
        self.log.info(self.tr.t(
            "download_complete",
            file=file_name, size=human_size(info.total_bytes), elapsed=f"{elapsed:.1f}", speed=f"{avg:.2f}",
        ))
        try:
            self._sig_finish.emit(True)
        except Exception:
            self._finish_on_gui(True)

    def _set_parts_actual(self, n: int):
        try:
            self.lbl_parts_actual.setText(self.tr.t("parts_actual_format", n=n))
        except Exception:
            pass

    def _finish_on_gui(self, success: bool):
        allow_sleep(self.log)
        self.running = False
        try:
            self.btn_start.setEnabled(True)
            self.btn_cancel.setEnabled(False)
            self.lbl_status.setText(self.tr.t("status_done") if success else self.tr.t("status_interrupted"))
        except Exception:
            pass

    # ── Pollers (QTimer replaces tk .after) ──

    def _poll_log(self):
        try:
            while True:
                msg = self.log_q.get_nowait()
                self._log_buffer.append(msg)
        except queue.Empty:
            pass

    def _drain_log_buffer(self):
        if self._log_buffer:
            msg = self._log_buffer.pop(0)
            self._append_log(msg)
        try:
            delay = 90 if len(self._log_buffer) < 5 else 20
            if self._drain_timer.interval() != delay:
                self._drain_timer.setInterval(delay)
        except Exception:
            pass

    def _poll_progress(self):
        if self.state is not None:
            n_chunks = len(self.state.chunks)
            try:
                self.lbl_parts_actual.setText(self.tr.t("parts_actual_format", n=n_chunks))
            except Exception:
                pass
        if self.state is not None and self.info is not None and self.info.total_bytes > 0:
            done = self.state.bytes_downloaded
            total = self.info.total_bytes
            pct = min(100.0, done / total * 100)
            try:
                self.progress.setValue(int(pct))
            except Exception:
                pass

            elapsed = time.monotonic() - self.start_time if self.start_time else 0
            speed = done / elapsed / 1e6 if elapsed > 0 else 0
            remaining = (total - done) / (speed * 1e6) if speed > 0 else 0

            try:
                self.lbl_status.setText(self.tr.t(
                    "status_progress", done=human_size(done), total=human_size(total), pct=f"{pct:.1f}",
                ))
                self.lbl_stats.setText(self.tr.t(
                    "stats_line", speed=f"{speed:.2f}", elapsed=f"{elapsed:.0f}", remaining=f"{remaining:.0f}",
                ))
            except Exception:
                pass


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    try:
        app.setOrganizationName("RAPID")
        app.setApplicationName("RAPID")
    except Exception:
        pass
    win = RapidWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
