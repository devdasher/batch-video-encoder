#!/usr/bin/env python3
"""
Batch Video Encoder using HandBrakeCLI
GUI built with PySide6
"""

from __future__ import annotations

import sys
import os
import re
import shlex
import copy
import time
import threading
import subprocess
import json
import logging
import logging.handlers
import queue as queue_module
import uuid
import shutil
import tempfile
import sqlite3
import base64
from functools import partial
from pathlib import Path
from datetime import datetime, timedelta
from typing import (
    List, Dict, Optional, Any, Union, Tuple, Set, Callable, TypeVar, Generic,
    Iterable, Iterator, NamedTuple, Final, Literal, Protocol, overload
)
from dataclasses import dataclass, field, asdict
from enum import Enum, auto, IntEnum
from collections import deque
from functools import lru_cache
import traceback
import pprint
import types

# PySide6
from PySide6.QtCore import (
    Qt, QThread, Signal, QTimer, QSize, QModelIndex,
    QAbstractTableModel, QSortFilterProxyModel, QItemSelectionModel,
    QItemSelection, QObject, QMutex, QWaitCondition, Slot, QCoreApplication,
    QVersionNumber, 
)
from PySide6.QtGui import (
    QAction, QActionGroup, QTextCursor, QPalette, QColor, QBrush,
    QFont, QTextOption, QKeySequence, QFontMetrics, QKeyEvent, QShortcut
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QFormLayout, QLineEdit, QCheckBox, QPushButton,
    QFileDialog, QTableWidget, QTableWidgetItem, QHeaderView,
    QTextEdit, QLabel, QProgressBar, QStatusBar, QSplitter,
    QMessageBox, QStyle, QScrollArea, QDialog, QDialogButtonBox,
    QAbstractItemView, QMenu, QSizePolicy, QComboBox, QTableView,
    QStyledItemDelegate, QStyleOptionProgressBar, QApplication as QtApp,
    QPlainTextEdit, QFrame
)

# Try to import natsort for natural sorting
try:
    from natsort import natsorted
except ImportError:
    # Fallback simple sorting
    def natsorted(iterable):
        return sorted(iterable)

# =============================================================================
# Constants & Configuration
# =============================================================================

APP_NAME: Final[str] = "Batch Video Encoder"
APP_SUBTITLE: Final[str] = "Powered by HandBrake"
APP_VERSION: Final[str] = "0.1.9"
APP_STATUS: Final[str] = "Beta"
APP_AUTHOR: Final[str] = "devdasher"
APP_DESCRIPTION: Final[str] = (
    "A dynamic GUI for HandBrakeCLI with per‑file settings, "
    "live progress, drag & drop, and clean‑code design."
)
APP_COPYRIGHT: Final[str] = f"© 2026 {APP_AUTHOR}"
GITHUB_LINK: Final[str] = "https://github.com/devdasher/batch-video-encoder"
SETTINGS_VERSION: Final[int] = 1

# Logging defaults
LOG_DIR: Final[Path] = Path("logs")
LOG_MAX_BYTES: Final[int] = 10 * 1024 * 1024  # 10 MB
LOG_BACKUP_COUNT: Final[int] = 5
LOG_RETENTION_DAYS: Final[int] = 30
LOG_FLUSH_INTERVAL_MS: Final[int] = 50

# Queue defaults
PROGRESS_UPDATE_THRESHOLD: Final[float] = 1.0  # percent change
PROGRESS_UPDATE_MIN_INTERVAL: Final[float] = 0.3  # seconds

# UI defaults
DEFAULT_COLUMN_WIDTHS: Final[Dict[int, int]] = {0: 350, 1: 120, 2: 80, 3: 150}

# Supported extensions
SUPPORTED_EXTENSIONS: Final[Set[str]] = {
    '.mkv', '.mp4', '.avi', '.mov', '.wmv', '.m4v', '.m2ts', '.ts',
    '.webm', '.flv', '.vob', '.ogv', '.mpg', '.mpeg', '.m2t', '.mts',
    '.divx', '.xvid', '.asf', '.3gp', '.3g2', '.f4v', '.dv', '.evo'
}

# Resolution mapping
RESOLUTION_MAP: Final[Dict[str, Tuple[int, int]]] = {
    "480p": (720, 480),
    "576p": (720, 576),
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "2160p (4K)": (3840, 2160),
    "4320p (8K)": (7680, 4320),
}

# =============================================================================
# Enums
# =============================================================================

class Theme(Enum):
    LIGHT = "light"
    DARK = "dark"

class LogLevel(Enum):
    DEBUG = logging.DEBUG
    INFO = logging.INFO
    WARNING = logging.WARNING
    ERROR = logging.ERROR
    CRITICAL = logging.CRITICAL

class QueueStatus(Enum):
    PENDING = "Pending"
    ENCODING = "Encoding"
    SUCCESS = "Success"
    FAILED = "Failed"
    SKIPPED = "Skipped"
    TERMINATED = "Terminated"

    @property
    def emoji(self) -> str:
        return {
            QueueStatus.PENDING: "⏳",
            QueueStatus.ENCODING: "🎬",
            QueueStatus.SUCCESS: "✅",
            QueueStatus.FAILED: "❌",
            QueueStatus.SKIPPED: "⚠️",
            QueueStatus.TERMINATED: "⛔",
        }[self]

    @property
    def display_name(self) -> str:
        return f"{self.emoji} {self.value}"

class WidgetType(Enum):
    TEXT = auto()
    BOOL = auto()
    FILE = auto()
    DIRECTORY = auto()
    COMBO = auto()
    MULTILINE = auto()

class LayoutType(Enum):
    FORM = auto()
    VERTICAL = auto()

class SettingGroup(Enum):
    DIMENSIONS = "Dimensions"
    VIDEO = "Video"
    AUDIO = "Audio"
    SUBTITLE = "Subtitle"
    CHAPTERS = "Chapters"
    FILTERS = "Filters"
    OTHER = "Other Settings"
    OUTPUT = "Output"

class DuplicateAction(Enum):
    SKIP = "skip"
    ADD = "add"
    CANCEL = "cancel"

# =============================================================================
# Dataclasses
# =============================================================================

@dataclass(frozen=True)
class GroupDefinition:
    name: str
    layout: LayoutType

GROUP_DEFS: Final[Dict[SettingGroup, GroupDefinition]] = {
    SettingGroup.DIMENSIONS: GroupDefinition("Dimensions", LayoutType.FORM),
    SettingGroup.VIDEO: GroupDefinition("Video", LayoutType.FORM),
    SettingGroup.AUDIO: GroupDefinition("Audio", LayoutType.FORM),
    SettingGroup.SUBTITLE: GroupDefinition("Subtitle", LayoutType.FORM),
    SettingGroup.CHAPTERS: GroupDefinition("Chapters", LayoutType.VERTICAL),
    SettingGroup.FILTERS: GroupDefinition("Filters", LayoutType.VERTICAL),
    SettingGroup.OTHER: GroupDefinition("Other Settings", LayoutType.FORM),
    SettingGroup.OUTPUT: GroupDefinition("Output", LayoutType.FORM),
}

@dataclass(frozen=True)
class SettingDefinition:
    key: str
    label: str
    placeholder: str = ""
    default: Any = ""
    widget_type: WidgetType = WidgetType.TEXT
    group: SettingGroup = SettingGroup.OTHER
    cli_arg: Optional[str] = None
    browse_filter: str = "All files (*)"
    tooltip: str = ""
    choices: Optional[List[str]] = None
    validator: Optional[Any] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    visible: bool = True
    dependencies: List[str] = field(default_factory=list)
    category: str = "encoding"

    def __post_init__(self):
        if self.widget_type == WidgetType.BOOL and not isinstance(self.default, bool):
            object.__setattr__(self, 'default', bool(self.default))

# All encoding settings definitions – single source of truth
ENCODING_SETTINGS: Final[List[SettingDefinition]] = [
    # ===== OUTPUT =====
    SettingDefinition(
        "output_folder", "Output Folder",
        placeholder="Leave empty for auto-generated",
        default="",
        widget_type=WidgetType.DIRECTORY,
        group=SettingGroup.OUTPUT,
        cli_arg=None,
        tooltip="Custom output folder. If empty, auto-generated based on resolution and encoder.",
    ),
    SettingDefinition(
        "output_filename", "Output Filename",
        placeholder="Leave empty to use input filename",
        default="",
        widget_type=WidgetType.TEXT,
        group=SettingGroup.OUTPUT,
        cli_arg=None,
        tooltip="Base filename without extension. Extension will be added automatically.",
    ),
    # ===== DIMENSIONS =====
    SettingDefinition(
        "resolution", "Resolution Limit",
        choices=["480p", "576p", "720p", "1080p", "2160p (4K)", "4320p (8K)"],
        default="1080p",
        widget_type=WidgetType.COMBO,
        group=SettingGroup.DIMENSIONS,
        cli_arg=None,
    ),
    SettingDefinition(
        "crop_mode", "Crop Mode",
        placeholder="auto, strict, none",
        default="auto",
        group=SettingGroup.DIMENSIONS,
        cli_arg="--crop-mode",
    ),
    # ===== FILTERS =====
    SettingDefinition(
        "nograyscale", "No Grayscale",
        default=True,
        widget_type=WidgetType.BOOL,
        group=SettingGroup.FILTERS,
        cli_arg="--no-grayscale",
    ),
    SettingDefinition(
        "nonanamorphic", "Non-Anamorphic",
        default=True,
        widget_type=WidgetType.BOOL,
        group=SettingGroup.FILTERS,
        cli_arg="--non-anamorphic",
    ),
    SettingDefinition(
        "keepaspect", "Keep Display Aspect",
        default=True,
        widget_type=WidgetType.BOOL,
        group=SettingGroup.FILTERS,
        cli_arg="--keep-display-aspect",
    ),
    SettingDefinition(
        "nodetelecine", "No Detelecine",
        default=True,
        widget_type=WidgetType.BOOL,
        group=SettingGroup.FILTERS,
        cli_arg="--no-detelecine",
    ),
    SettingDefinition(
        "nodecomb", "No Decomb",
        default=True,
        widget_type=WidgetType.BOOL,
        group=SettingGroup.FILTERS,
        cli_arg="--no-decomb",
    ),
    SettingDefinition(
        "nocombdetect", "No Comb Detect",
        default=True,
        widget_type=WidgetType.BOOL,
        group=SettingGroup.FILTERS,
        cli_arg="--no-comb-detect",
    ),
    SettingDefinition(
        "nohqdn3d", "No HQDN3D",
        default=True,
        widget_type=WidgetType.BOOL,
        group=SettingGroup.FILTERS,
        cli_arg="--no-hqdn3d",
    ),
    # ===== VIDEO =====
    SettingDefinition(
        "vb", "Video Bitrate",
        placeholder="e.g., 3500",
        default="1100",
        group=SettingGroup.VIDEO,
        cli_arg="--vb",
        validator=int,
    ),
    SettingDefinition(
        "encoder_tune", "Encoder Tune",
        choices=["none", "film", "animation", "grain", "still-image", "psnr", "ssim", "zero-latency"],
        default="film",
        widget_type=WidgetType.COMBO,
        group=SettingGroup.VIDEO,
        cli_arg="--encoder-tune",
    ),
    SettingDefinition(
        "encoder", "Video Encoder",
        choices=[
            "svt_av1", "svt_av1_10bit", "nvenc_av1", "nvenc_av1_10bit",
            "ffv1", "x264", "x264_10bit", "qsv_h264", "nvenc_h264",
            "x265", "x265_10bit", "x265_12bit", "qsv_h265", "qsv_h265_10bit",
            "nvenc_h265", "nvenc_h265_10bit", "mpeg4", "mpeg2",
            "VP8", "VP9", "VP9_10bit", "theora"
        ],
        default="x264",
        widget_type=WidgetType.COMBO,
        group=SettingGroup.VIDEO,
        cli_arg="--encoder",
    ),
    SettingDefinition(
        "preset", "Encoder Preset",
        choices=["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow", "placebo"],
        default="fast",
        widget_type=WidgetType.COMBO,
        group=SettingGroup.VIDEO,
        cli_arg="--encoder-preset",
    ),
    SettingDefinition(
        "profile", "Profile",
        choices=["auto", "baseline", "main", "high", "high422", "high444"],
        default="high",
        widget_type=WidgetType.COMBO,
        group=SettingGroup.VIDEO,
        cli_arg="--encoder-profile",
    ),
    SettingDefinition(
        "level", "Encoder Level",
        choices=["1.0", "1b", "1.1", "1.2", "1.3", "2.0", "2.1", "2.2",
                 "3.0", "3.1", "3.2", "4.0", "4.1", "4.2", "5.0", "5.1",
                 "5.2", "6.0", "6.1", "6.2"],
        default="4.0",
        widget_type=WidgetType.COMBO,
        group=SettingGroup.VIDEO,
        cli_arg="--encoder-level",
    ),
    SettingDefinition(
        "rate", "Frame Rate",
        choices=["5", "10", "12", "15", "20", "23.976", "24", "25", "29.97",
                 "30", "48", "50", "59.94", "60", "72", "75", "90", "100", "120"],
        default="30",
        widget_type=WidgetType.COMBO,
        group=SettingGroup.VIDEO,
        cli_arg="--rate",
    ),
    SettingDefinition(
        "noturbo", "No Turbo",
        default=True,
        widget_type=WidgetType.BOOL,
        group=SettingGroup.VIDEO,
        cli_arg="--no-turbo",
    ),
    SettingDefinition(
        "pfr", "PFR",
        default=True,
        widget_type=WidgetType.BOOL,
        group=SettingGroup.VIDEO,
        cli_arg="--pfr",
    ),
    SettingDefinition(
        "multipass", "Multi-pass",
        default=True,
        widget_type=WidgetType.BOOL,
        group=SettingGroup.VIDEO,
        cli_arg="--multi-pass",
    ),
    # ===== AUDIO =====
    SettingDefinition(
        "audio", "Audio Tracks",
        placeholder="1,2, ...",
        default="1",
        group=SettingGroup.AUDIO,
        cli_arg="--audio",
    ),
    SettingDefinition(
        "aencoder", "Audio Encoder",
        placeholder="aac, av_aac, ...",
        default="aac",
        group=SettingGroup.AUDIO,
        cli_arg="--aencoder",
    ),
    SettingDefinition(
        "ab", "Audio Bitrate",
        placeholder="128, auto, ...",
        default="128",
        group=SettingGroup.AUDIO,
        cli_arg="--ab",
    ),
    SettingDefinition(
        "mixdown", "Mixdown",
        placeholder="auto, stereo, mono...",
        default="stereo",
        group=SettingGroup.AUDIO,
        cli_arg="--mixdown",
    ),
    SettingDefinition(
        "arate", "Audio Rate",
        placeholder="auto, 44.1, 48...",
        default="auto",
        group=SettingGroup.AUDIO,
        cli_arg="--arate",
    ),
    # ===== SUBTITLE =====
    SettingDefinition(
        "subtitle", "Subtitle",
        placeholder="none, scan, 1,2,3",
        default="none",
        group=SettingGroup.SUBTITLE,
        cli_arg="--subtitle",
    ),
    SettingDefinition(
        "subtitle_burned", "Subtitle Burned",
        placeholder="none, 1,2,3",
        default="none",
        group=SettingGroup.SUBTITLE,
        cli_arg="--subtitle-burned",
    ),
    SettingDefinition(
        "ssa_file", "SSA/ASS File",
        placeholder="Path to .ssa/.ass file",
        default="",
        widget_type=WidgetType.FILE,
        group=SettingGroup.SUBTITLE,
        cli_arg="--ssa-file",
        browse_filter="Subtitle files (*.ssa *.ass)",
    ),
    SettingDefinition(
        "ssa_lang", "SSA Language",
        placeholder="eng, fre, jpn...",
        default="eng",
        group=SettingGroup.SUBTITLE,
        cli_arg="--ssa-lang",
    ),
    SettingDefinition(
        "ssaburn", "SSA Burn",
        default=True,
        widget_type=WidgetType.BOOL,
        group=SettingGroup.SUBTITLE,
        cli_arg="--ssa-burn",
    ),
    # ===== CHAPTERS =====
    SettingDefinition(
        "markers", "Markers",
        default=True,
        widget_type=WidgetType.BOOL,
        group=SettingGroup.CHAPTERS,
        cli_arg="--markers",
    ),
    # ===== OTHER SETTINGS =====
    SettingDefinition(
        "overwrite", "Overwrite existing output",
        default=True,
        widget_type=WidgetType.BOOL,
        group=SettingGroup.OTHER,
        cli_arg=None,
    ),
    SettingDefinition(
        "extra_args", "Extra Arguments",
        placeholder="e.g. --deblock --some-option value",
        default="",
        widget_type=WidgetType.MULTILINE,
        group=SettingGroup.OTHER,
        cli_arg=None,
    ),
]

SETTING_KEYS: Final[List[str]] = [s.key for s in ENCODING_SETTINGS]
DEFAULT_VALUES: Final[Dict[str, Any]] = {s.key: s.default for s in ENCODING_SETTINGS}

# =============================================================================
# EncodingSettings
# =============================================================================

@dataclass
class EncodingSettings:
    data: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        for s in ENCODING_SETTINGS:
            if s.key not in self.data:
                self.data[s.key] = s.default

    def __getitem__(self, key: str) -> Any:
        return self.data.get(key, DEFAULT_VALUES.get(key))

    def __setitem__(self, key: str, value: Any) -> None:
        self.data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def to_dict(self) -> Dict[str, Any]:
        return self.data.copy()

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> EncodingSettings:
        return cls(data=d)

    def update(self, other: Dict[str, Any]) -> None:
        self.data.update(other)

    def copy(self) -> EncodingSettings:
        return EncodingSettings.from_dict(copy.deepcopy(self.data))

    def __getattr__(self, name: str) -> Any:
        if name in self.data:
            return self.data[name]
        raise AttributeError(f"'EncodingSettings' has no attribute '{name}'")

# =============================================================================
# QueueItem with UUID
# =============================================================================

@dataclass
class QueueItem:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    input_path: str = ""
    output_path: str = ""
    settings: EncodingSettings = field(default_factory=EncodingSettings)
    status: QueueStatus = QueueStatus.PENDING
    progress: int = 0
    date_added: datetime = field(default_factory=datetime.now)

    def copy(self) -> QueueItem:
        return QueueItem(
            id=self.id,
            input_path=self.input_path,
            output_path=self.output_path,
            settings=self.settings.copy(),
            status=self.status,
            progress=self.progress,
            date_added=self.date_added,
        )

# =============================================================================
# LogEntry
# =============================================================================

@dataclass
class LogEntry:
    text: str
    role: str

# =============================================================================
# ApplicationSettings (for in-memory use)
# =============================================================================

@dataclass
class ApplicationSettings:
    handbrake_path: str = ""
    theme: str = "light"
    settings_version: int = SETTINGS_VERSION
    column_state: Optional[bytes] = None
    window_geometry: Optional[bytes] = None
    window_state: Optional[bytes] = None
    duplicate_dialog_size: Optional[QSize] = None

# =============================================================================
# Services
# =============================================================================

# ---- Logging Service ----

class LoggingService:
    _instance: Optional[LoggingService] = None

    def __new__(cls) -> LoggingService:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        self._initialized = True
        self._log_queue = queue_module.Queue()
        self._setup_logging()

    def _setup_logging(self) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self._logger = logging.getLogger('BatchEncoder')
        self._logger.setLevel(logging.DEBUG)
        self._logger.handlers.clear()

        queue_handler = logging.handlers.QueueHandler(self._log_queue)
        self._logger.addHandler(queue_handler)

        formatter = logging.Formatter(
            '%(asctime)s %(levelname)s [%(name)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        log_file = LOG_DIR / 'batch_encoder.log'
        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        self._logger.addHandler(file_handler)

        timed_handler = logging.handlers.TimedRotatingFileHandler(
            LOG_DIR / 'batch_encoder_daily.log', when='midnight', interval=1, backupCount=30
        )
        timed_handler.setLevel(logging.INFO)
        timed_handler.setFormatter(formatter)
        self._logger.addHandler(timed_handler)

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        self._logger.addHandler(console_handler)

        self._listener = logging.handlers.QueueListener(
            self._log_queue, file_handler, timed_handler, console_handler
        )
        self._listener.start()

        self._cleanup_old_logs()

    def _cleanup_old_logs(self) -> None:
        cutoff = datetime.now() - timedelta(days=LOG_RETENTION_DAYS)
        for log_file in LOG_DIR.glob('batch_encoder*.log*'):
            try:
                if log_file.stat().st_mtime < cutoff.timestamp():
                    log_file.unlink()
            except Exception:
                pass

    def get_logger(self) -> logging.Logger:
        return self._logger

    def shutdown(self) -> None:
        if hasattr(self, '_listener'):
            self._listener.stop()

# ---- Settings Service (SQLite) - optimized ----

class SettingsService:
    DB_FILE: Final[Path] = Path("settings.db")
    SCHEMA_VERSION: Final[int] = 1

    def __init__(self):
        self._logger = logging.getLogger('BatchEncoder.SettingsService')
        self._app_settings: Optional[ApplicationSettings] = None
        self._default_encoding: Optional[EncodingSettings] = None
        self._cache_valid = False
        self._conn: Optional[sqlite3.Connection] = None
        self._schema_verified = False
        self._lock = threading.RLock()
        self._ensure_connection_and_schema()

    # ------------------------------------------------------------------
    # Private connection & schema management
    # ------------------------------------------------------------------
    def _ensure_connection_and_schema(self) -> None:
        """Ensure a valid connection and schema. Called under self._lock."""
        with self._lock:
            if self._conn is None:
                self._connect()
            if not self._schema_verified:
                self._verify_schema()
                self._schema_verified = True

    def _connect(self) -> None:
        """Open a new connection."""
        try:
            self._conn = sqlite3.connect(str(self.DB_FILE), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        except sqlite3.Error as e:
            self._logger.error(f"Failed to connect to database: {e}")
            raise

    def _close_connection(self) -> None:
        """Close the current connection if open."""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
            self._schema_verified = False

    def _verify_schema(self) -> None:
        """Check schema version and required tables."""
        if self._conn is None:
            raise sqlite3.OperationalError("No connection")

        cursor = self._conn.cursor()

        # Check schema_version table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'")
        if not cursor.fetchone():
            self._create_full_schema()
            return

        # Check version
        cursor.execute("SELECT version FROM schema_version")
        row = cursor.fetchone()
        if row is None:
            self._create_full_schema()
            return

        version = row[0]
        if version != self.SCHEMA_VERSION:
            self._migrate(version, self.SCHEMA_VERSION)

        # Ensure ui_state exists (for older DBs)
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ui_state'")
        if not cursor.fetchone():
            cursor.execute("""
                CREATE TABLE ui_state (
                    key TEXT PRIMARY KEY,
                    value BLOB
                )
            """)
            self._conn.commit()
            self._logger.info("Added missing table 'ui_state'")

        # Ensure indexes exist (for recent_paths)
        cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_recent_paths_category'")
        if not cursor.fetchone():
            cursor.execute("CREATE INDEX idx_recent_paths_category ON recent_paths(category)")
            cursor.execute("CREATE INDEX idx_recent_paths_timestamp ON recent_paths(timestamp)")
            self._conn.commit()

    def _create_full_schema(self) -> None:
        """Drop and recreate all tables."""
        self._logger.info("Creating full database schema.")
        cursor = self._conn.cursor()
        for table in ['schema_version', 'application_settings', 'default_encoding_settings',
                      'window_state', 'column_state', 'recent_paths', 'ui_state']:
            cursor.execute(f"DROP TABLE IF EXISTS {table}")

        cursor.execute("""
            CREATE TABLE schema_version (version INTEGER NOT NULL)
        """)
        cursor.execute("INSERT INTO schema_version (version) VALUES (?)", (self.SCHEMA_VERSION,))

        cursor.execute("""
            CREATE TABLE application_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE default_encoding_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE window_state (
                key TEXT PRIMARY KEY,
                value BLOB
            )
        """)

        cursor.execute("""
            CREATE TABLE column_state (
                key TEXT PRIMARY KEY,
                value BLOB
            )
        """)

        cursor.execute("""
            CREATE TABLE recent_paths (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                path TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE ui_state (
                key TEXT PRIMARY KEY,
                value BLOB
            )
        """)

        cursor.execute("CREATE INDEX idx_recent_paths_category ON recent_paths(category)")
        cursor.execute("CREATE INDEX idx_recent_paths_timestamp ON recent_paths(timestamp)")

        self._conn.commit()
        self._logger.info("Database schema created successfully.")

    def _migrate(self, old_version: int, new_version: int) -> None:
        """Migrate schema from old_version to new_version."""
        self._logger.info(f"Migrating database from version {old_version} to {new_version}")
        cursor = self._conn.cursor()
        if old_version < 1:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ui_state'")
            if not cursor.fetchone():
                cursor.execute("""
                    CREATE TABLE ui_state (
                        key TEXT PRIMARY KEY,
                        value BLOB
                    )
                """)
        cursor.execute("UPDATE schema_version SET version = ?", (new_version,))
        self._conn.commit()

    def _recover_database(self) -> None:
        """Recover from a corrupted or missing database."""
        with self._lock:
            self._close_connection()
            if self.DB_FILE.exists():
                try:
                    sqlite3.connect(str(self.DB_FILE)).close()
                    # file exists and is valid, but schema broken -> recreate schema
                    self._connect()
                    self._create_full_schema()
                    self._schema_verified = True
                    return
                except sqlite3.DatabaseError:
                    # Corrupted: rename and create new
                    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
                    corrupt_name = self.DB_FILE.parent / f"settings-corrupted-{timestamp}.db"
                    try:
                        shutil.move(str(self.DB_FILE), str(corrupt_name))
                        self._logger.warning(f"Corrupted database moved to {corrupt_name}")
                    except Exception as e:
                        self._logger.error(f"Failed to rename corrupted database: {e}")
                    # Create fresh
                    self._connect()
                    self._create_full_schema()
                    self._schema_verified = True
                    return
            # File doesn't exist or empty
            self._connect()
            self._create_full_schema()
            self._schema_verified = True

    # ------------------------------------------------------------------
    # Helper for executing queries with automatic recovery
    # ------------------------------------------------------------------
    def _execute(self, query: str, params: tuple = ()) -> None:
        """Execute a query without returning results. Retries once on recoverable error."""
        with self._lock:
            try:
                self._ensure_connection_and_schema()
                self._conn.execute(query, params)
            except sqlite3.Error as e:
                self._logger.error(f"SQL execute error: {e}")
                self._recover_database()
                self._ensure_connection_and_schema()
                self._conn.execute(query, params)

    def _execute_fetchone(self, query: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        with self._lock:
            try:
                self._ensure_connection_and_schema()
                return self._conn.execute(query, params).fetchone()
            except sqlite3.Error as e:
                self._logger.error(f"SQL fetchone error: {e}")
                self._recover_database()
                self._ensure_connection_and_schema()
                return self._conn.execute(query, params).fetchone()

    def _execute_fetchall(self, query: str, params: tuple = ()) -> List[sqlite3.Row]:
        with self._lock:
            try:
                self._ensure_connection_and_schema()
                return self._conn.execute(query, params).fetchall()
            except sqlite3.Error as e:
                self._logger.error(f"SQL fetchall error: {e}")
                self._recover_database()
                self._ensure_connection_and_schema()
                return self._conn.execute(query, params).fetchall()

    def _execute_commit(self, query: str, params: tuple = ()) -> None:
        with self._lock:
            try:
                self._ensure_connection_and_schema()
                self._conn.execute(query, params)
                self._conn.commit()
            except sqlite3.Error as e:
                self._logger.error(f"SQL execute/commit error: {e}")
                self._recover_database()
                self._ensure_connection_and_schema()
                self._conn.execute(query, params)
                self._conn.commit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def load_all(self) -> None:
        """Load all settings from database into cache."""
        with self._lock:
            try:
                self._ensure_connection_and_schema()
                app_settings = ApplicationSettings()

                # Load application settings
                rows = self._execute_fetchall("SELECT key, value FROM application_settings")
                for row in rows:
                    key, value = row['key'], row['value']
                    if key == 'handbrake_path':
                        app_settings.handbrake_path = value
                    elif key == 'theme':
                        app_settings.theme = value
                    elif key == 'settings_version':
                        try:
                            app_settings.settings_version = int(value)
                        except ValueError:
                            pass

                # Load column state
                row = self._execute_fetchone("SELECT value FROM column_state WHERE key='column_state'")
                if row and row['value']:
                    try:
                        app_settings.column_state = row['value']
                    except Exception:
                        app_settings.column_state = None

                # Load window state
                rows = self._execute_fetchall("SELECT key, value FROM window_state")
                for row in rows:
                    key, value = row['key'], row['value']
                    if key == 'window_geometry':
                        app_settings.window_geometry = value
                    elif key == 'window_state':
                        app_settings.window_state = value

                # Load UI state (dialog sizes)
                rows = self._execute_fetchall("SELECT key, value FROM ui_state")
                for row in rows:
                    key, value = row['key'], row['value']
                    if key == 'duplicate_dialog_size' and value:
                        try:
                            import struct
                            w, h = struct.unpack('ii', value)
                            app_settings.duplicate_dialog_size = QSize(w, h)
                        except Exception:
                            app_settings.duplicate_dialog_size = None

                # Load default encoding settings
                default = {}
                rows = self._execute_fetchall("SELECT key, value FROM default_encoding_settings")
                for row in rows:
                    default[row['key']] = row['value']
                self._default_encoding = EncodingSettings.from_dict(default)

                self._app_settings = app_settings
                self._cache_valid = True
            except sqlite3.Error as e:
                self._logger.error(f"Error loading settings: {e}")
                self._app_settings = ApplicationSettings()
                self._default_encoding = EncodingSettings()

    def save_all(self) -> None:
        """Save all cached settings to database using a single transaction."""
        with self._lock:
            if not self._cache_valid:
                return
            try:
                self._ensure_connection_and_schema()
                # Begin transaction
                self._conn.execute("BEGIN TRANSACTION")

                # Save application settings with UPSERT
                if self._app_settings:
                    # handbrake_path
                    self._conn.execute(
                        "INSERT OR REPLACE INTO application_settings (key, value) VALUES (?, ?)",
                        ('handbrake_path', self._app_settings.handbrake_path)
                    )
                    # theme
                    self._conn.execute(
                        "INSERT OR REPLACE INTO application_settings (key, value) VALUES (?, ?)",
                        ('theme', self._app_settings.theme)
                    )
                    # settings_version
                    self._conn.execute(
                        "INSERT OR REPLACE INTO application_settings (key, value) VALUES (?, ?)",
                        ('settings_version', str(self._app_settings.settings_version))
                    )

                # Column state
                if self._app_settings and self._app_settings.column_state is not None:
                    self._conn.execute(
                        "INSERT OR REPLACE INTO column_state (key, value) VALUES (?, ?)",
                        ('column_state', self._app_settings.column_state)
                    )
                else:
                    self._conn.execute("DELETE FROM column_state WHERE key='column_state'")

                # Window state
                if self._app_settings:
                    if self._app_settings.window_geometry is not None:
                        self._conn.execute(
                            "INSERT OR REPLACE INTO window_state (key, value) VALUES (?, ?)",
                            ('window_geometry', self._app_settings.window_geometry)
                        )
                    else:
                        self._conn.execute("DELETE FROM window_state WHERE key='window_geometry'")

                    if self._app_settings.window_state is not None:
                        self._conn.execute(
                            "INSERT OR REPLACE INTO window_state (key, value) VALUES (?, ?)",
                            ('window_state', self._app_settings.window_state)
                        )
                    else:
                        self._conn.execute("DELETE FROM window_state WHERE key='window_state'")

                # UI state
                if self._app_settings and self._app_settings.duplicate_dialog_size is not None:
                    import struct
                    sz = self._app_settings.duplicate_dialog_size
                    data = struct.pack('ii', sz.width(), sz.height())
                    self._conn.execute(
                        "INSERT OR REPLACE INTO ui_state (key, value) VALUES (?, ?)",
                        ('duplicate_dialog_size', data)
                    )
                else:
                    self._conn.execute("DELETE FROM ui_state WHERE key='duplicate_dialog_size'")

                # Default encoding settings (upsert all keys)
                # Clear old ones and insert new ones
                self._conn.execute("DELETE FROM default_encoding_settings")
                if self._default_encoding:
                    for key, value in self._default_encoding.to_dict().items():
                        self._conn.execute(
                            "INSERT INTO default_encoding_settings (key, value) VALUES (?, ?)",
                            (key, str(value))
                        )

                # Commit transaction
                self._conn.commit()
            except sqlite3.Error as e:
                self._logger.error(f"Error saving settings: {e}")
                if self._conn:
                    self._conn.rollback()
                self._recover_database()

    # ------------------------------------------------------------------
    # Individual setters with change detection
    # ------------------------------------------------------------------
    def get_app_settings(self) -> ApplicationSettings:
        if not self._cache_valid:
            self.load_all()
        return self._app_settings or ApplicationSettings()

    def set_app_settings(self, settings: ApplicationSettings) -> None:
        # Only save if different
        if self._app_settings != settings:
            self._app_settings = settings
            self.save_all()

    def get_default_encoding(self) -> EncodingSettings:
        if not self._cache_valid:
            self.load_all()
        return self._default_encoding or EncodingSettings()

    def set_default_encoding(self, settings: EncodingSettings) -> None:
        if self._default_encoding != settings:
            self._default_encoding = settings
            self.save_all()

    def get_column_state(self) -> Optional[bytes]:
        return self.get_app_settings().column_state

    def set_column_state(self, state: bytes) -> None:
        settings = self.get_app_settings()
        if settings.column_state != state:
            settings.column_state = state
            self.set_app_settings(settings)

    def get_window_geometry(self) -> Optional[bytes]:
        return self.get_app_settings().window_geometry

    def set_window_geometry(self, geometry: bytes) -> None:
        settings = self.get_app_settings()
        if settings.window_geometry != geometry:
            settings.window_geometry = geometry
            self.set_app_settings(settings)

    def get_window_state(self) -> Optional[bytes]:
        return self.get_app_settings().window_state

    def set_window_state(self, state: bytes) -> None:
        settings = self.get_app_settings()
        if settings.window_state != state:
            settings.window_state = state
            self.set_app_settings(settings)

    def get_handbrake_path(self) -> str:
        return self.get_app_settings().handbrake_path

    def set_handbrake_path(self, path: str) -> None:
        settings = self.get_app_settings()
        if settings.handbrake_path != path:
            settings.handbrake_path = path
            # Save only this value to avoid full rewrite
            with self._lock:
                try:
                    self._ensure_connection_and_schema()
                    self._conn.execute(
                        "INSERT OR REPLACE INTO application_settings (key, value) VALUES (?, ?)",
                        ('handbrake_path', path)
                    )
                    self._conn.commit()
                except sqlite3.Error as e:
                    self._logger.error(f"Error updating handbrake_path: {e}")
                    self._recover_database()

    def get_theme(self) -> str:
        return self.get_app_settings().theme

    def set_theme(self, theme: str) -> None:
        settings = self.get_app_settings()
        if settings.theme != theme:
            settings.theme = theme
            with self._lock:
                try:
                    self._ensure_connection_and_schema()
                    self._conn.execute(
                        "INSERT OR REPLACE INTO application_settings (key, value) VALUES (?, ?)",
                        ('theme', theme)
                    )
                    self._conn.commit()
                except sqlite3.Error as e:
                    self._logger.error(f"Error updating theme: {e}")
                    self._recover_database()

    def get_duplicate_dialog_size(self) -> Optional[QSize]:
        return self.get_app_settings().duplicate_dialog_size

    def set_duplicate_dialog_size(self, size: QSize) -> None:
        settings = self.get_app_settings()
        if settings.duplicate_dialog_size != size:
            settings.duplicate_dialog_size = size
            with self._lock:
                try:
                    self._ensure_connection_and_schema()
                    import struct
                    data = struct.pack('ii', size.width(), size.height())
                    self._conn.execute(
                        "INSERT OR REPLACE INTO ui_state (key, value) VALUES (?, ?)",
                        ('duplicate_dialog_size', data)
                    )
                    self._conn.commit()
                except sqlite3.Error as e:
                    self._logger.error(f"Error updating duplicate_dialog_size: {e}")
                    self._recover_database()

    def close(self) -> None:
        """Close the database connection cleanly."""
        with self._lock:
            self._close_connection()

# ---- Validation Service ----

class ValidationService:
    @staticmethod
    def validate_handbrake_path(path: str) -> Tuple[bool, str]:
        """Validate that the selected executable is HandBrakeCLI."""

        if not path:
            return False, "No file selected."

        p = Path(path)

        if not p.exists():
            return False, f"File does not exist:\n{p}"

        if not p.is_file():
            return False, f"Selected path is not a file:\n{p}"

        if sys.platform == "win32":
            if p.suffix.lower() != ".exe":
                return False, "Please select a valid executable (*.exe)."

            # Prevent launching arbitrary GUI applications.
            if p.name.lower() != "handbrakecli.exe":
                return (
                    False,
                    "Please select the official HandBrakeCLI.exe executable."
                )

        # Quick checks passed; now we need to do the actual version check
        # but that will be done in the background worker.
        # This method now only does quick validation.
        return True, "Quick validation passed."

    @staticmethod
    def validate_output_folder(folder: str) -> Tuple[bool, str]:
        if not folder:
            return True, ""
        p = Path(folder)
        try:
            p.mkdir(parents=True, exist_ok=True)
            return True, ""
        except Exception as e:
            return False, f"Cannot create folder: {e}"

    @staticmethod
    def validate_output_filename(filename: str) -> Tuple[bool, str]:
        if not filename:
            return True, ""
        if any(c in filename for c in ('/', '\\', ':', '*', '?', '"', '<', '>', '|')):
            return False, "Filename contains invalid characters."
        return True, ""

    @staticmethod
    def validate_input_file(path: str) -> Tuple[bool, str]:
        p = Path(path)
        if not p.exists():
            return False, f"File does not exist: {path}"
        if not p.is_file():
            return False, f"Not a file: {path}"
        suffix = p.suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            return False, f"Unsupported file extension: {suffix}"
        return True, ""

# ---- Output Naming Service ----

class OutputNamingService:
    @staticmethod
    def generate_output_path(input_path: str, settings: EncodingSettings) -> str:
        input_p = Path(input_path)
        input_stem = input_p.stem
        input_ext = input_p.suffix

        output_folder = settings.get("output_folder", "").strip()
        if output_folder:
            folder = Path(output_folder)
        else:
            folder = OutputNamingService._generate_default_folder(settings)
            folder = input_p.parent / folder

        output_filename = settings.get("output_filename", "").strip()
        if output_filename:
            base = output_filename
        else:
            base = input_stem

        folder.mkdir(parents=True, exist_ok=True)
        return str(folder / f"{base}{input_ext}")

    @staticmethod
    def _generate_default_folder(settings: EncodingSettings) -> Path:
        resolution = settings.get("resolution", "1080p")
        encoder = settings.get("encoder", "x264")
        res_key = resolution.split()[0] if " " in resolution else resolution
        folder_name = f"encoded-files-{res_key}"
        if encoder.lower() != "x264":
            folder_name += f"-{encoder}"
        return Path(folder_name)

# ---- Queue Repository ----

class QueueRepository(QObject):
    item_added = Signal(QueueItem)
    item_removed = Signal(str)
    item_updated = Signal(QueueItem)
    items_cleared = Signal()
    items_added = Signal(list)
    items_removed = Signal(list)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._items: Dict[str, QueueItem] = {}
        self._logger = logging.getLogger('BatchEncoder.QueueRepository')

    def add_item(self, item: QueueItem) -> None:
        if item.id in self._items:
            self._logger.warning(f"Item with id {item.id} already exists, overwriting.")
        self._items[item.id] = item
        self.item_added.emit(item)

    def add_items(self, items: List[QueueItem]) -> None:
        for item in items:
            self._items[item.id] = item
        self.items_added.emit(items)

    def remove_item(self, item_id: str) -> Optional[QueueItem]:
        item = self._items.pop(item_id, None)
        if item:
            self.item_removed.emit(item_id)
        return item

    def remove_items(self, item_ids: List[str]) -> List[QueueItem]:
        removed = []
        for id_ in item_ids:
            item = self._items.pop(id_, None)
            if item:
                removed.append(item)
        if removed:
            self.items_removed.emit([item.id for item in removed])
        return removed

    def clear(self) -> None:
        self._items.clear()
        self.items_cleared.emit()

    def get_item(self, item_id: str) -> Optional[QueueItem]:
        return self._items.get(item_id)

    def get_all_items(self) -> List[QueueItem]:
        return list(self._items.values())

    def get_items_by_ids(self, ids: List[str]) -> List[QueueItem]:
        return [self._items[id_] for id_ in ids if id_ in self._items]

    def get_all_input_paths(self) -> Set[str]:
        return {item.input_path for item in self._items.values()}

    def get_items_by_status(self, status: QueueStatus) -> List[QueueItem]:
        return [item for item in self._items.values() if item.status == status]

    def get_pending_items(self) -> List[QueueItem]:
        return [item for item in self._items.values() if item.status in (QueueStatus.PENDING, QueueStatus.TERMINATED)]

    def update_item(self, item: QueueItem) -> None:
        if item.id in self._items:
            self._items[item.id] = item
            self.item_updated.emit(item)

    def count_by_status(self, status: QueueStatus) -> int:
        return sum(1 for item in self._items.values() if item.status == status)

    def total_count(self) -> int:
        return len(self._items)

# ---- Encoding Service ----

class EncodingService:
    @staticmethod
    def build_command(settings: EncodingSettings, handbrake_path: str,
                      input_file: str, output_file: str) -> List[str]:
        cmd = [handbrake_path, '-i', input_file, '-o', output_file]
        cmd.extend(EncodingService._build_settings_args(settings))
        return cmd

    @staticmethod
    def _build_settings_args(settings: EncodingSettings) -> List[str]:
        args = []
        resolution = settings.get("resolution")
        if resolution in RESOLUTION_MAP:
            w, h = RESOLUTION_MAP[resolution]
            args.extend(["--maxWidth", str(w), "--maxHeight", str(h)])

        for s in ENCODING_SETTINGS:
            if s.cli_arg is None:
                continue
            if s.key == "resolution":
                continue
            val = settings.get(s.key)
            if val is None or val == "":
                continue
            if s.widget_type == WidgetType.BOOL:
                if val:
                    args.append(s.cli_arg)
            else:
                args.extend([s.cli_arg, str(val)])
        extra = settings.get("extra_args", "")
        if extra:
            try:
                args.extend(shlex.split(extra))
            except ValueError:
                args.append(extra)
        return args

# ---- Undo Manager with Redo ----

@dataclass
class UndoOperation:
    items_with_indices: List[Tuple[int, QueueItem]]  # (row_index, item)

class UndoManager:
    def __init__(self):
        self._undo_stack: List[UndoOperation] = []
        self._redo_stack: List[UndoOperation] = []
        self._max_size = 50

    def push_removal(self, items_with_indices: List[Tuple[int, QueueItem]]) -> None:
        """Store a removal operation with the original indices."""
        if items_with_indices:
            self._undo_stack.append(UndoOperation(items_with_indices))
            self._redo_stack.clear()  # New operation clears redo
            if len(self._undo_stack) > self._max_size:
                self._undo_stack.pop(0)

    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    def undo(self) -> Optional[UndoOperation]:
        if not self._undo_stack:
            return None
        op = self._undo_stack.pop()
        self._redo_stack.append(op)
        return op

    def redo(self) -> Optional[UndoOperation]:
        if not self._redo_stack:
            return None
        op = self._redo_stack.pop()
        self._undo_stack.append(op)
        return op

    def clear(self) -> None:
        self._undo_stack.clear()
        self._redo_stack.clear()

# =============================================================================
# UI Helpers and Delegates
# =============================================================================

def light_palette() -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(240, 240, 240))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(0, 0, 0))
    palette.setColor(QPalette.ColorRole.Base, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(233, 233, 233))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(255, 255, 220))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(0, 0, 0))
    palette.setColor(QPalette.ColorRole.Text, QColor(0, 0, 0))
    palette.setColor(QPalette.ColorRole.Button, QColor(240, 240, 240))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(0, 0, 0))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 0, 0))
    palette.setColor(QPalette.ColorRole.Link, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    return palette

def dark_palette() -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(53, 53, 53))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.Base, QColor(35, 35, 35))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(53, 53, 53))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(25, 25, 25))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.Text, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.Button, QColor(53, 53, 53))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 0, 0))
    palette.setColor(QPalette.ColorRole.Link, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    return palette

def get_app_full_version() -> str:
    if APP_STATUS.lower() in ("stable", "release"):
        return APP_VERSION
    return f"{APP_VERSION} ({APP_STATUS})"

def format_elapsed(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d}"

class ProgressBarDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        if index.column() == 2:
            progress = index.data(Qt.ItemDataRole.UserRole)
            if progress is None or progress < 0:
                progress = 0
            opt = QStyleOptionProgressBar()
            opt.rect = option.rect
            opt.minimum = 0
            opt.maximum = 100
            opt.progress = progress
            opt.text = f"{progress}%"
            opt.textVisible = True
            QApplication.style().drawControl(QStyle.ControlElement.CE_ProgressBar, opt, painter)
        else:
            super().paint(painter, option, index)

    def sizeHint(self, option, index):
        if index.column() == 2:
            return QSize(80, 20)
        return super().sizeHint(option, index)

class DropTableView(QTableView):
    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragEnabled(False)
        self.setDragDropMode(QAbstractItemView.DropOnly)
        self.setDropIndicatorShown(False)
        self.setWordWrap(True)
        self.setTextElideMode(Qt.TextElideMode.ElideNone)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.setStyleSheet("")
        event.accept()

    def dropEvent(self, event):
        self.setStyleSheet("")
        files = []
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if os.path.isfile(path) and Path(path).suffix.lower() in SUPPORTED_EXTENSIONS:
                files.append(path)
        if files:
            self.files_dropped.emit(files)
            event.acceptProposedAction()
        else:
            event.ignore()

    def keyPressEvent(self, event: QKeyEvent):
        # Allow Ctrl+Z and Ctrl+Y to be handled by the main window's shortcuts
        # If we don't override, the table's default behavior might interfere.
        # We'll let the event propagate.
        super().keyPressEvent(event)

# =============================================================================
# UI Settings Builder and Binder
# =============================================================================

class SettingsUIBuilder:
    def __init__(self, definitions: List[SettingDefinition], parent: QWidget = None):
        self.definitions = definitions
        self._widgets: Dict[str, QWidget] = {}
        self._bool_widgets: Dict[str, QCheckBox] = {}
        self._file_edit_widgets: Dict[str, QLineEdit] = {}
        self._directory_edit_widgets: Dict[str, QLineEdit] = {}
        self._combo_widgets: Dict[str, QComboBox] = {}
        self._multiline_widgets: Dict[str, QPlainTextEdit] = {}
        self._container = QWidget(parent)
        self._main_layout = QVBoxLayout(self._container)
        self._main_layout.setContentsMargins(5, 5, 5, 5)

    def build(self) -> QWidget:
        groups = {g: [] for g in SettingGroup}
        for s in self.definitions:
            groups[s.group].append(s)

        for group_enum, group_def in GROUP_DEFS.items():
            group_settings = groups.get(group_enum, [])
            if not group_settings:
                continue

            group_box = QGroupBox(group_def.name)
            if group_def.layout == LayoutType.FORM:
                layout = QFormLayout()
            else:
                layout = QVBoxLayout()
            group_box.setLayout(layout)

            for s in group_settings:
                if s.widget_type == WidgetType.BOOL:
                    cb = QCheckBox(s.label)
                    cb.setChecked(s.default if isinstance(s.default, bool) else False)
                    if s.tooltip:
                        cb.setToolTip(s.tooltip)
                    self._bool_widgets[s.key] = cb
                    self._widgets[s.key] = cb
                    layout.addWidget(cb)
                elif s.widget_type == WidgetType.FILE:
                    edit = QLineEdit()
                    edit.setPlaceholderText(s.placeholder)
                    if s.tooltip:
                        edit.setToolTip(s.tooltip)
                    btn = QPushButton("Browse...")
                    btn.clicked.connect(partial(self._browse_file, s.key))
                    self._file_edit_widgets[s.key] = edit
                    self._widgets[s.key] = edit
                    h_layout = QHBoxLayout()
                    h_layout.addWidget(edit)
                    h_layout.addWidget(btn)
                    if isinstance(layout, QFormLayout):
                        layout.addRow(s.label, h_layout)
                    else:
                        layout.addLayout(h_layout)
                elif s.widget_type == WidgetType.DIRECTORY:
                    edit = QLineEdit()
                    edit.setPlaceholderText(s.placeholder)
                    if s.tooltip:
                        edit.setToolTip(s.tooltip)
                    btn = QPushButton("Browse...")
                    btn.clicked.connect(partial(self._browse_directory, s.key))
                    self._directory_edit_widgets[s.key] = edit
                    self._widgets[s.key] = edit
                    h_layout = QHBoxLayout()
                    h_layout.addWidget(edit)
                    h_layout.addWidget(btn)
                    if isinstance(layout, QFormLayout):
                        layout.addRow(s.label, h_layout)
                    else:
                        layout.addLayout(h_layout)
                elif s.widget_type == WidgetType.COMBO:
                    combo = QComboBox()
                    if s.choices:
                        combo.addItems(s.choices)
                        if s.default in s.choices:
                            combo.setCurrentText(s.default)
                    if s.tooltip:
                        combo.setToolTip(s.tooltip)
                    self._combo_widgets[s.key] = combo
                    self._widgets[s.key] = combo
                    if isinstance(layout, QFormLayout):
                        layout.addRow(s.label, combo)
                    else:
                        layout.addWidget(combo)
                elif s.widget_type == WidgetType.MULTILINE:
                    text_edit = QPlainTextEdit()
                    text_edit.setPlaceholderText(s.placeholder)
                    text_edit.setMaximumHeight(60)
                    if s.tooltip:
                        text_edit.setToolTip(s.tooltip)
                    self._multiline_widgets[s.key] = text_edit
                    self._widgets[s.key] = text_edit
                    if isinstance(layout, QFormLayout):
                        layout.addRow(s.label, text_edit)
                    else:
                        layout.addWidget(text_edit)
                else:  # TEXT
                    edit = QLineEdit()
                    edit.setPlaceholderText(s.placeholder)
                    if s.tooltip:
                        edit.setToolTip(s.tooltip)
                    self._widgets[s.key] = edit
                    if isinstance(layout, QFormLayout):
                        layout.addRow(s.label, edit)
                    else:
                        layout.addWidget(edit)

            self._main_layout.addWidget(group_box)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self._main_layout.addWidget(spacer)

        return self._container

    def _browse_file(self, key: str) -> None:
        edit = self._file_edit_widgets.get(key)
        if not edit:
            return
        for s in self.definitions:
            if s.key == key:
                filter_str = s.browse_filter if s.browse_filter else "All files (*)"
                break
        else:
            filter_str = "All files (*)"
        path, _ = QFileDialog.getOpenFileName(self._container, "Select File", "", filter_str)
        if path:
            edit.setText(path)

    def _browse_directory(self, key: str) -> None:
        edit = self._directory_edit_widgets.get(key)
        if not edit:
            return
        path = QFileDialog.getExistingDirectory(self._container, "Select Directory")
        if path:
            edit.setText(path)

    @property
    def widgets(self) -> Dict[str, QWidget]:
        return self._widgets

class SettingsBinder:
    def __init__(self, widget_map: Dict[str, QWidget]):
        self._widget_map = widget_map

    def load_values(self, values: Union[EncodingSettings, Dict[str, Any]]) -> None:
        if isinstance(values, EncodingSettings):
            values = values.to_dict()
        for key, widget in self._widget_map.items():
            if key in values:
                val = values[key]
                if isinstance(widget, QCheckBox):
                    widget.setChecked(bool(val))
                elif isinstance(widget, QLineEdit):
                    widget.setText(str(val))
                elif isinstance(widget, QComboBox):
                    if val in [widget.itemText(i) for i in range(widget.count())]:
                        widget.setCurrentText(str(val))
                    elif widget.count() > 0:
                        widget.setCurrentIndex(0)
                elif isinstance(widget, QPlainTextEdit):
                    widget.setPlainText(str(val))

    def get_values(self) -> Dict[str, Any]:
        result = {}
        for key, widget in self._widget_map.items():
            if isinstance(widget, QCheckBox):
                result[key] = widget.isChecked()
            elif isinstance(widget, QLineEdit):
                result[key] = widget.text().strip()
            elif isinstance(widget, QComboBox):
                result[key] = widget.currentText()
            elif isinstance(widget, QPlainTextEdit):
                result[key] = widget.toPlainText()
            else:
                result[key] = ""
        return result

    def get_encoding_settings(self) -> EncodingSettings:
        return EncodingSettings.from_dict(self.get_values())

# =============================================================================
# Dialogs
# =============================================================================

class SettingsDialog(QDialog):
    def __init__(self, defaults: Union[EncodingSettings, Dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Encoding Settings for New Files")
        self.resize(600, 700)

        builder = SettingsUIBuilder(ENCODING_SETTINGS, self)
        settings_widget = builder.build()

        scroll = QScrollArea(self)
        scroll.setWidget(settings_widget)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(scroll)
        layout.addWidget(button_box)

        self._binder = SettingsBinder(builder.widgets)
        self._binder.load_values(defaults)

    def get_settings(self) -> EncodingSettings:
        return self._binder.get_encoding_settings()

class DuplicateDialog(QDialog):
    def __init__(self, duplicates: List[str], parent=None):
        super().__init__(parent)

        self.setWindowTitle("Duplicate Files Detected")

        # Default size (user can resize)
        self.resize(520, 280)
        self.setMinimumSize(420, 220)

        self._action = DuplicateAction.CANCEL

        try:
            size = settings_service.get_duplicate_dialog_size()
            if size:
                self.resize(size.width(), size.height())
            else:
                self.resize(520, 280)
        except Exception:
            self.resize(520, 280)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        header = QLabel("The following files already exist in the queue:")
        header.setWordWrap(False)
        header.setSizePolicy(QSizePolicy.Policy.Preferred,
                             QSizePolicy.Policy.Fixed)
        layout.addWidget(header)

        list_widget = QTextEdit()
        list_widget.setReadOnly(True)
        list_widget.setPlainText("\n".join(duplicates))

        # This is the only widget that should grow
        list_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding
        )

        layout.addWidget(list_widget)

        button_box = QDialogButtonBox()

        skip_btn = QPushButton("Skip Duplicates")
        add_btn = QPushButton("Add Anyway")
        cancel_btn = QPushButton("Cancel")

        button_box.addButton(skip_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        button_box.addButton(add_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        button_box.addButton(cancel_btn, QDialogButtonBox.ButtonRole.RejectRole)

        skip_btn.clicked.connect(lambda: self._set_action(DuplicateAction.SKIP))
        add_btn.clicked.connect(lambda: self._set_action(DuplicateAction.ADD))
        cancel_btn.clicked.connect(lambda: self._set_action(DuplicateAction.CANCEL))

        layout.addWidget(button_box)

    def _set_action(self, action: DuplicateAction):
        self._action = action
        self.accept()

    def get_action(self) -> DuplicateAction:
        return self._action

    def closeEvent(self, event):
        try:
            settings_service.set_duplicate_dialog_size(self.size())
        except Exception:
            pass
        super().closeEvent(event)

class CommandPreviewDialog(QDialog):
    def __init__(self, commands: List[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Command Preview")
        self.resize(700, 500)

        layout = QVBoxLayout(self)

        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setFont(QFont("Courier New", 9))
        self.text_edit.setPlainText("\n\n".join(commands))
        layout.addWidget(self.text_edit)

        button_layout = QHBoxLayout()
        copy_selected_btn = QPushButton("Copy Selected")
        copy_all_btn = QPushButton("Copy All")
        close_btn = QPushButton("Close")

        copy_selected_btn.clicked.connect(self._copy_selected)
        copy_all_btn.clicked.connect(self._copy_all)
        close_btn.clicked.connect(self.accept)

        button_layout.addWidget(copy_selected_btn)
        button_layout.addWidget(copy_all_btn)
        button_layout.addStretch()
        button_layout.addWidget(close_btn)
        layout.addLayout(button_layout)

    def _copy_selected(self):
        cursor = self.text_edit.textCursor()
        if cursor.hasSelection():
            text = cursor.selectedText()
            QApplication.clipboard().setText(text)

    def _copy_all(self):
        text = self.text_edit.toPlainText()
        QApplication.clipboard().setText(text)

class HandBrakeHelpDialog(QDialog):
    def __init__(self, help_text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("HandBrakeCLI Help")
        self.resize(800, 600)

        layout = QVBoxLayout(self)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setFont(QFont("Courier New", 9))
        self.text_edit.setPlainText(help_text)
        layout.addWidget(self.text_edit)

        btn_layout = QHBoxLayout()
        copy_selected_btn = QPushButton("Copy Selected")
        copy_all_btn = QPushButton("Copy All")
        close_btn = QPushButton("Close")
        copy_selected_btn.clicked.connect(self._copy_selected)
        copy_all_btn.clicked.connect(self._copy_all)
        close_btn.clicked.connect(self.accept)

        btn_layout.addWidget(copy_selected_btn)
        btn_layout.addWidget(copy_all_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

    def _copy_selected(self):
        cursor = self.text_edit.textCursor()
        if cursor.hasSelection():
            text = cursor.selectedText()
            QApplication.clipboard().setText(text)

    def _copy_all(self):
        text = self.text_edit.toPlainText()
        QApplication.clipboard().setText(text)

class UnexpectedErrorDialog(QDialog):
    def __init__(self, error_text: str, traceback_text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Unexpected Error")
        self.setMinimumSize(600, 400)

        layout = QVBoxLayout(self)

        label = QLabel(
            "An unexpected error occurred. A detailed log has been written.\n"
            "Please include the log file when reporting this issue."
        )
        label.setWordWrap(True)
        layout.addWidget(label)

        details = QTextEdit()
        details.setPlainText(f"Error: {error_text}\n\nTraceback:\n{traceback_text}")
        details.setReadOnly(True)
        layout.addWidget(details)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        button_box.accepted.connect(self.accept)
        layout.addWidget(button_box)

# =============================================================================
# Queue Table Model (wraps repository)
# =============================================================================

class QueueTableModel(QAbstractTableModel):
    def __init__(self, repository: QueueRepository, parent=None):
        super().__init__(parent)
        self._repo = repository
        self._columns = ["File", "Status", "Progress", "Date Added"] + [s.label for s in ENCODING_SETTINGS]
        self._column_keys = [None, None, None, None] + [s.key for s in ENCODING_SETTINGS]
        self._boolean_keys = {s.key for s in ENCODING_SETTINGS if s.widget_type == WidgetType.BOOL}
        self._combo_keys = {s.key for s in ENCODING_SETTINGS if s.widget_type == WidgetType.COMBO}

        self._repo.item_added.connect(self._on_item_added)
        self._repo.items_added.connect(self._on_items_added)
        self._repo.item_removed.connect(self._on_item_removed)
        self._repo.items_removed.connect(self._on_items_removed)
        self._repo.items_cleared.connect(self._on_items_cleared)
        self._repo.item_updated.connect(self._on_item_updated)

        self._items_cache: List[QueueItem] = self._repo.get_all_items()

    def _refresh_cache(self):
        self._items_cache = self._repo.get_all_items()

    def _on_item_added(self, item: QueueItem):
        self._refresh_cache()
        row = len(self._items_cache) - 1
        self.beginInsertRows(QModelIndex(), row, row)
        self.endInsertRows()

    def _on_items_added(self, items: List[QueueItem]):
        self._refresh_cache()
        start = len(self._items_cache) - len(items)
        self.beginInsertRows(QModelIndex(), start, start + len(items) - 1)
        self.endInsertRows()

    def _on_item_removed(self, item_id: str):
        old_cache = self._items_cache
        self._refresh_cache()
        for i, item in enumerate(old_cache):
            if item.id == item_id:
                self.beginRemoveRows(QModelIndex(), i, i)
                self.endRemoveRows()
                break

    def _on_items_removed(self, item_ids: List[str]):
        old_cache = self._items_cache
        self._refresh_cache()
        if len(item_ids) > 5:
            self.beginResetModel()
            self.endResetModel()
        else:
            indices = []
            for id_ in item_ids:
                for i, item in enumerate(old_cache):
                    if item.id == id_:
                        indices.append(i)
                        break
            if indices:
                indices.sort(reverse=True)
                for idx in indices:
                    self.beginRemoveRows(QModelIndex(), idx, idx)
                    self.endRemoveRows()

    def _on_items_cleared(self):
        self.beginResetModel()
        self._refresh_cache()
        self.endResetModel()

    def _on_item_updated(self, item: QueueItem):
        self._refresh_cache()
        for i, it in enumerate(self._items_cache):
            if it.id == item.id:
                self.dataChanged.emit(self.index(i, 0), self.index(i, self.columnCount()-1))
                break

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._items_cache)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self._columns)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row = index.row()
        col = index.column()
        if row >= len(self._items_cache):
            return None
        item = self._items_cache[row]
        if role == Qt.DisplayRole:
            if col == 0:
                return item.input_path
            elif col == 1:
                return item.status.display_name
            elif col == 2:
                return f"{item.progress}%" if item.progress > 0 else ""
            elif col == 3:
                return item.date_added.strftime("%Y-%m-%d %H:%M")
            else:
                key = self._column_keys[col]
                val = item.settings.get(key)
                if key == "extra_args" and isinstance(val, str):
                    val = val.replace("\n", "|").replace("\r", "")
                if isinstance(val, bool):
                    return "✅" if val else "❌"
                return str(val)
        elif role == Qt.ItemDataRole.TextAlignmentRole:
            if col in (1, 2, 3):
                return Qt.AlignmentFlag.AlignCenter
            if col >= 4:
                key = self._column_keys[col]
                if key in self._boolean_keys or key in self._combo_keys:
                    return Qt.AlignmentFlag.AlignCenter
            return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        elif role == Qt.ItemDataRole.UserRole:
            if col == 2:
                return item.progress
        return None

    def setData(self, index: QModelIndex, value: Any, role=Qt.ItemDataRole.EditRole) -> bool:
        return False

    def headerData(self, section: int, orientation: Qt.Orientation, role=Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation == Qt.Orientation.Vertical and role == Qt.ItemDataRole.DisplayRole:
            return section + 1
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if section < len(self._columns):
                return self._columns[section]
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled

    def get_item_by_row(self, row: int) -> Optional[QueueItem]:
        if 0 <= row < len(self._items_cache):
            return self._items_cache[row]
        return None

    def get_id_by_row(self, row: int) -> Optional[str]:
        item = self.get_item_by_row(row)
        return item.id if item else None

    def get_row_by_id(self, item_id: str) -> Optional[int]:
        for i, item in enumerate(self._items_cache):
            if item.id == item_id:
                return i
        return None

    def count_by_status(self, status: QueueStatus) -> int:
        return self._repo.count_by_status(status)

    def total_count(self) -> int:
        return self._repo.total_count()

    def get_all_ids(self) -> List[str]:
        return [item.id for item in self._items_cache]

    def get_items_by_ids(self, ids: List[str]) -> List[QueueItem]:
        return self._repo.get_items_by_ids(ids)

    # ---- Insertion for undo/redo ----
    def insert_item_at(self, item: QueueItem, row: int) -> None:
        """Insert an item at a specific row in the cache and update repository."""
        if item.id in self._repo._items:
            return  # already exists
        if row < 0 or row > len(self._items_cache):
            row = len(self._items_cache)
        self.beginInsertRows(QModelIndex(), row, row)
        self._items_cache.insert(row, item)
        self._repo._items[item.id] = item  # add to repository
        self.endInsertRows()
        # Do NOT emit item_added – we manually inserted into the model

# =============================================================================
# Encoding Worker (QObject, thread-safe)
# =============================================================================

class EncodingWorker(QObject):
    progress_signal = Signal(str, int)        # item_id, percent
    status_signal = Signal(str, QueueStatus)
    file_started = Signal(str, str)
    file_finished = Signal(str, bool, int)
    log_signal = Signal(str, str)
    log_progress_signal = Signal(str)

    _progress_re = re.compile(r"Encoding: task \d+ of \d+, ([\d.]+) %")

    def __init__(self, handbrake_path: str, parent=None):
        super().__init__(parent)
        self.handbrake_path = handbrake_path
        self._stop_event = threading.Event()
        self._current_process = None
        self._lock = threading.Lock()
        self._last_progress_time = 0.0
        self._last_progress_value = -1
        self._termination_requested = False
        self._logger = logging.getLogger('BatchEncoder.EncodingWorker')
        self._current_item_id: Optional[str] = None

    @Slot(str, QueueItem)
    def process_item(self, item_id: str, item: QueueItem):
        self._stop_event.clear()
        self._termination_requested = False
        self._current_item_id = item_id
        input_file = item.input_path
        output_file = item.output_path
        settings = item.settings
        overwrite = settings.get("overwrite", False)

        if not overwrite and os.path.exists(output_file):
            self.log_signal.emit(f"Skipping (output exists): {output_file}", "warning")
            self.status_signal.emit(item_id, QueueStatus.SKIPPED)
            self.file_finished.emit(item_id, False, -1)
            return

        cmd = EncodingService.build_command(settings, self.handbrake_path,
                                            input_file, output_file)
        self.log_signal.emit(f"Encoding: {input_file}", "normal")
        self.log_signal.emit(f"Command: {' '.join(cmd)}", "command")
        self.file_started.emit(item_id, output_file)

        start_time = time.time()
        try:
            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NO_WINDOW
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                bufsize=1,
                universal_newlines=True,
                creationflags=creationflags
            )
            self._current_process = proc

            for line in proc.stdout:
                if self._stop_event.is_set():
                    proc.terminate()
                    break
                line = line.rstrip()
                if not line:
                    continue
                match = self._progress_re.search(line)
                if match:
                    percent = float(match.group(1))
                    now = time.time()
                    if (abs(percent - self._last_progress_value) >= PROGRESS_UPDATE_THRESHOLD or
                        now - self._last_progress_time >= PROGRESS_UPDATE_MIN_INTERVAL):
                        self.progress_signal.emit(item_id, int(percent))
                        self.log_progress_signal.emit(line)
                        self._last_progress_value = percent
                        self._last_progress_time = now
                else:
                    if "error" in line.lower():
                        self.log_signal.emit(line, "error")
                    elif "warning" in line.lower():
                        self.log_signal.emit(line, "warning")
                    else:
                        self.log_signal.emit(line, "normal")
            proc.wait()
            exit_code = proc.returncode
            success = exit_code == 0
            elapsed = time.time() - start_time

            if self._termination_requested or self._stop_event.is_set():
                status = QueueStatus.TERMINATED
                self.log_signal.emit(f"Terminated by user after {elapsed:.1f}s", "warning")
            elif success:
                self.log_signal.emit(f"Finished successfully in {elapsed:.1f}s", "success")
                status = QueueStatus.SUCCESS
            else:
                self.log_signal.emit(f"Failed with exit code {exit_code} in {elapsed:.1f}s", "error")
                status = QueueStatus.FAILED

            self.status_signal.emit(item_id, status)
            self.file_finished.emit(item_id, success, exit_code)
            if status == QueueStatus.SUCCESS:
                self.progress_signal.emit(item_id, 100)
        except Exception as e:
            self.log_signal.emit(f"Error: {str(e)}", "error")
            self.status_signal.emit(item_id, QueueStatus.FAILED)
            self.file_finished.emit(item_id, False, -1)
        finally:
            self._current_process = None
            self._current_item_id = None

    @Slot()
    def terminate(self):
        self._stop_event.set()
        self._termination_requested = True
        with self._lock:
            if self._current_process and self._current_process.poll() is None:
                try:
                    self._current_process.terminate()
                except Exception:
                    pass

# =============================================================================
# Validation Worker (asynchronous)
# =============================================================================

class ValidationWorker(QObject):
    finished = Signal(bool, str)  # success, message (version or error)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._logger = logging.getLogger('BatchEncoder.ValidationWorker')
        self._process = None

    @Slot(str)
    def validate(self, path: str):
        """Run validation in background thread."""
        self._logger.info("HandBrakeCLI validation started.")
        try:
            # Prepare startup info for Windows to hide window
            startupinfo = None
            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NO_WINDOW
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE

            # Execute with timeout
            proc = subprocess.Popen(
                [path, "--version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
                startupinfo=startupinfo,
                creationflags=creationflags,
            )
            self._process = proc

            # Use a timer to kill if timeout
            timeout = 10  # seconds
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                self._logger.warning("Validation timed out.")
                self.finished.emit(False, f"Validation timed out after {timeout} seconds.")
                return

            if proc.returncode != 0:
                self._logger.warning(f"Validation failed with exit code {proc.returncode}.")
                self.finished.emit(False, f"HandBrakeCLI exited with code {proc.returncode}.")
                return

            output = (stdout + stderr).strip()
            if "HandBrake" not in output:
                self._logger.warning("Executable is not HandBrakeCLI.")
                self.finished.emit(False, "The selected executable does not appear to be HandBrakeCLI.")
                return

            version = output.splitlines()[0].strip() if output else "HandBrakeCLI"
            self._logger.info(f"HandBrakeCLI validated successfully: {version}")
            self.finished.emit(True, version)

        except Exception as e:
            self._logger.error(f"Validation error: {e}")
            self.finished.emit(False, f"Unable to execute HandBrakeCLI: {str(e)}")
        finally:
            self._process = None

# =============================================================================
# Help Worker (asynchronous)
# =============================================================================

class HelpWorker(QObject):
    finished = Signal(str)  # help text or error

    def __init__(self, parent=None):
        super().__init__(parent)
        self._logger = logging.getLogger('BatchEncoder.HelpWorker')
        self._process = None

    @Slot(str)
    def fetch_help(self, path: str):
        """Fetch --help output in background."""
        self._logger.info("Fetching HandBrakeCLI help.")
        try:
            startupinfo = None
            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NO_WINDOW
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE

            proc = subprocess.Popen(
                [path, "--help"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
                startupinfo=startupinfo,
                creationflags=creationflags,
            )
            self._process = proc

            timeout = 30
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                self._logger.warning("Help fetch timed out.")
                self.finished.emit("Error: Fetching help timed out.")
                return

            if proc.returncode != 0:
                self._logger.warning(f"Help fetch failed with exit code {proc.returncode}.")
                self.finished.emit(f"Error: HandBrakeCLI exited with code {proc.returncode}.")
                return

            output = stdout or stderr or "(No output)"
            self._logger.info("Help fetched successfully.")
            self.finished.emit(output)

        except Exception as e:
            self._logger.error(f"Help fetch error: {e}")
            self.finished.emit(f"Error: {str(e)}")
        finally:
            self._process = None

# =============================================================================
# Main Window
# =============================================================================

class MainWindow(QMainWindow):
    # Signal to start processing an item in the worker thread
    process_item_signal = Signal(str, QueueItem)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{get_app_full_version()}")
        self.setMinimumSize(900, 650)
        self.resize(1100, 700)

        self._logger = logging.getLogger('BatchEncoder.MainWindow')

        # Services
        self._settings_service = SettingsService()
        self._validation_service = ValidationService()
        self._output_naming = OutputNamingService()
        self._app_settings = self._settings_service.get_app_settings()
        self._default_encoding_settings = self._load_default_encoding_settings()

        # Repository & Undo
        self._repository = QueueRepository(self)
        self._undo_manager = UndoManager()

        # Model
        self._queue_model = QueueTableModel(self._repository, self)

        # Worker
        self._worker = None
        self._worker_thread = None

        # Encoding state
        self._encoding_active = False
        self._stop_after_current = False
        self._start_time = None
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.timeout.connect(self._update_elapsed)

        # Log buffer for UI
        self._log_entries: List[LogEntry] = []
        self._log_buffer: List[LogEntry] = []
        self._log_flush_timer = QTimer(self)
        self._log_flush_timer.setInterval(LOG_FLUSH_INTERVAL_MS)
        self._log_flush_timer.timeout.connect(self._flush_logs)
        self._log_flush_timer.start()

        # Shortcuts for undo/redo (must exist before connecting signals)
        self.undo_shortcut = QShortcut(QKeySequence.StandardKey.Undo, self)
        self.undo_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        self.redo_shortcut = QShortcut(QKeySequence.StandardKey.Redo, self)
        self.redo_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        # We'll set the shortcut to be enabled only when the table has focus
        # by connecting to the table's focus events? Or we can check in the slot.
        # Easier: connect to the shortcut, and in the slot check if the table has focus.

        # UI
        self._create_actions()
        self._create_menu()
        self._create_statusbar()
        self._create_central_widget()
        self._connect_signals()
        self._restore_theme()
        self._load_handbrake_path()
        self._load_defaults_into_panel()
        self._restore_column_state()
        self._restore_window_state()

        # Help cache
        self._cached_help_text: Optional[str] = None

        self.redo_shortcut = QShortcut(QKeySequence.StandardKey.Redo, self)
        self.redo_shortcut.activated.connect(self._redo_remove)
        self.redo_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)

        self.queue_table.setFocus()

    # ------------------------------------------------------------------
    # Settings helpers
    # ------------------------------------------------------------------
    def _load_default_encoding_settings(self) -> EncodingSettings:
        return self._settings_service.get_default_encoding()

    def _save_default_encoding_settings(self, settings: EncodingSettings) -> None:
        self._settings_service.set_default_encoding(settings)

    def _load_handbrake_path(self):
        path = self._settings_service.get_handbrake_path()
        if path:
            self.handbrake_edit.setText(path)
            return

        # Auto-detect fallback
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))

        if sys.platform == "win32":
            default_path = os.path.join(base_dir, "tools", "HandBrakeCLI.exe")
        else:
            default_path = os.path.join(base_dir, "tools", "HandBrakeCLI")

        if not os.path.isfile(default_path):
            if sys.platform == "win32":
                fallback = os.path.join(base_dir, "HandBrakeCLI.exe")
            else:
                fallback = os.path.join(base_dir, "HandBrakeCLI")
            if os.path.isfile(fallback):
                default_path = fallback

        if os.path.isfile(default_path):
            self._settings_service.set_handbrake_path(default_path)
            self.handbrake_edit.setText(default_path)

    def _save_app_settings(self):
        path = self.handbrake_edit.text().strip()
        if path != self._settings_service.get_handbrake_path():
            self._settings_service.set_handbrake_path(path)

    # ------------------------------------------------------------------
    # UI creation
    # ------------------------------------------------------------------
    def _create_actions(self):
        self.start_action = QAction("Start Encoding", self)
        self.start_action.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.stop_action = QAction("Stop After Current", self)
        self.stop_action.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self.stop_action.setEnabled(False)
        self.terminate_action = QAction("Terminate Immediately", self)
        self.terminate_action.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self.terminate_action.setEnabled(False)
        self.add_action = QAction("Add Files...", self)
        self.add_action.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon))
        self.exit_action = QAction("Exit", self)
        self.about_action = QAction("About", self)
        self.undo_action = QAction("Undo Remove", self)
        self.undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self.undo_action.setEnabled(True)
        self.undo_action.setToolTip("Undo the last removal from the queue table (Ctrl+Z)")
        self.undo_action.setStatusTip("Restore the last removed row(s) to the queue table")
        self.redo_action = QAction("Redo Remove", self)
        self.redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        self.redo_action.setEnabled(True)
        self.redo_action.setToolTip("Redo a previously undone removal (Ctrl+Y)")
        self.redo_action.setStatusTip("Re-remove rows that were restored via Undo")

        self.theme_action_light = QAction("Light", self, checkable=True)
        self.theme_action_dark = QAction("Dark", self, checkable=True)
        self.theme_group = QActionGroup(self)
        self.theme_group.addAction(self.theme_action_light)
        self.theme_group.addAction(self.theme_action_dark)
        self.theme_action_light.setChecked(True)

    def _create_menu(self):
        menu_bar = self.menuBar()

        # File menu
        file_menu = menu_bar.addMenu("&File")
        file_menu.addAction(self.add_action)
        file_menu.addSeparator()
        file_menu.addAction(self.exit_action)

        # Edit menu – for queue table operations
        edit_menu = menu_bar.addMenu("&Edit")
        edit_menu.addAction(self.undo_action)
        edit_menu.addAction(self.redo_action)

        # View menu
        view_menu = menu_bar.addMenu("&View")
        # Theme submenu
        theme_menu = view_menu.addMenu("Theme")
        theme_menu.addAction(self.theme_action_light)
        theme_menu.addAction(self.theme_action_dark)

        # Help menu
        help_menu = menu_bar.addMenu("&Help")
        help_menu.addAction(self.about_action)

    def _create_statusbar(self):
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")

    def _create_central_widget(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        main_splitter = QSplitter(Qt.Horizontal)

        self.config_panel = self._create_config_panel()
        main_splitter.addWidget(self.config_panel)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        queue_ctrl = QHBoxLayout()
        self.add_files_btn = QPushButton("Add Files...")
        self.remove_sel_btn = QPushButton("Remove Selected")
        self.remove_all_btn = QPushButton("Remove All")
        self.show_cmd_btn = QPushButton("Show Command(s)")
        # Removed undo_btn from here
        queue_ctrl.addWidget(self.add_files_btn)
        queue_ctrl.addWidget(self.remove_sel_btn)
        queue_ctrl.addWidget(self.remove_all_btn)
        queue_ctrl.addWidget(self.show_cmd_btn)
        queue_ctrl.addStretch()
        right_layout.addLayout(queue_ctrl)

        self.queue_table = DropTableView()
        self.queue_table.setModel(self._queue_model)
        self.queue_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.queue_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.queue_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.queue_table.files_dropped.connect(self._on_files_dropped)
        self.queue_table.verticalHeader().setVisible(True)
        self.queue_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)

        header = self.queue_table.horizontalHeader()
        header.setMaximumSectionSize(16777215)
        for col in range(self._queue_model.columnCount()):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        self.queue_table.setColumnWidth(0, 350)
        self.queue_table.setColumnWidth(1, 120)
        self.queue_table.setColumnWidth(2, 80)
        self.queue_table.setColumnWidth(3, 150)

        self.progress_delegate = ProgressBarDelegate(self.queue_table)
        self.queue_table.setItemDelegateForColumn(2, self.progress_delegate)

        right_splitter = QSplitter(Qt.Orientation.Vertical)

        queue_container = QWidget()
        queue_container_layout = QVBoxLayout(queue_container)
        queue_container_layout.setContentsMargins(0, 0, 0, 0)
        queue_container_layout.addWidget(self.queue_table)
        right_splitter.addWidget(queue_container)

        log_container = QWidget()
        log_layout = QVBoxLayout(log_container)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.addWidget(QLabel("General Log"))
        self.log_widget = QTextEdit()
        self.log_widget.setReadOnly(True)
        self.log_widget.setAcceptRichText(True)
        log_layout.addWidget(self.log_widget)
        right_splitter.addWidget(log_container)

        bottom_container = QWidget()
        bottom_layout = QVBoxLayout(bottom_container)
        bottom_layout.setContentsMargins(0, 0, 0, 0)

        bottom_layout.addWidget(QLabel("Live Status"))
        self.live_status = QLabel("Idle")
        self.live_status.setFrameStyle(QLabel.Shape.StyledPanel)
        bottom_layout.addWidget(self.live_status)

        summary_group = QGroupBox("Summary")
        summary_layout = QHBoxLayout(summary_group)
        self.summary_total = QLabel("Total: 0")
        self.summary_success = QLabel("Successful: 0")
        self.summary_failed = QLabel("Failed: 0")
        self.summary_skipped = QLabel("Skipped: 0")
        self.summary_terminated = QLabel("Terminated: 0")
        self.summary_time = QLabel("Elapsed: 00:00:00")
        summary_layout.addWidget(self.summary_total)
        summary_layout.addWidget(self.summary_success)
        summary_layout.addWidget(self.summary_failed)
        summary_layout.addWidget(self.summary_skipped)
        summary_layout.addWidget(self.summary_terminated)
        summary_layout.addWidget(self.summary_time)
        bottom_layout.addWidget(summary_group)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        bottom_layout.addWidget(self.progress_bar)

        btn_layout = QHBoxLayout()
        self.start_btn = QPushButton("Start Encoding")
        self.stop_btn = QPushButton("Stop After Current")
        self.terminate_btn = QPushButton("Terminate Immediately")
        self.stop_btn.setEnabled(False)
        self.terminate_btn.setEnabled(False)
        btn_layout.addWidget(self.start_btn)
        btn_layout.addWidget(self.stop_btn)
        btn_layout.addWidget(self.terminate_btn)
        btn_layout.addStretch()
        bottom_layout.addLayout(btn_layout)

        right_splitter.addWidget(bottom_container)
        right_splitter.setSizes([400, 200, 200])

        right_layout.addWidget(right_splitter)

        main_splitter.addWidget(right_widget)
        main_splitter.setSizes([450, 950])
        main_layout.addWidget(main_splitter)

    def _create_config_panel(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(5, 5, 5, 5)

        general_group = QGroupBox("General")
        general_form = QFormLayout(general_group)

        self.handbrake_edit = QLineEdit()
        self.handbrake_edit.setPlaceholderText("Path to HandBrakeCLI executable")
        self.handbrake_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.handbrake_edit.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        general_form.addRow("HandBrakeCLI:", self.handbrake_edit)

        btn_hb = QPushButton("Browse...")
        btn_hb.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        btn_hb.clicked.connect(self._browse_handbrake)

        hb_help_btn = QPushButton("Show Help")
        hb_help_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        hb_help_btn.clicked.connect(self._show_handbrake_help)

        hb_btn_layout = QHBoxLayout()
        hb_btn_layout.setContentsMargins(0, 0, 0, 0)
        hb_btn_layout.addStretch()
        hb_btn_layout.addWidget(btn_hb)
        hb_btn_layout.addWidget(hb_help_btn)

        general_form.addRow("", hb_btn_layout)

        layout.addWidget(general_group)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        settings_container = QWidget()
        settings_layout = QVBoxLayout(settings_container)
        settings_layout.setContentsMargins(0, 0, 0, 0)

        builder = SettingsUIBuilder(ENCODING_SETTINGS, settings_container)
        settings_widget = builder.build()
        settings_layout.addWidget(settings_widget)

        scroll.setWidget(settings_container)
        layout.addWidget(scroll, 1)

        btn_layout = QHBoxLayout()
        self.apply_settings_btn = QPushButton("Apply Settings")
        self.save_default_btn = QPushButton("Save Current as Default")
        btn_layout.addWidget(self.apply_settings_btn)
        btn_layout.addWidget(self.save_default_btn)
        layout.addLayout(btn_layout)

        self._settings_binder = SettingsBinder(builder.widgets)

        return container

    # ------------------------------------------------------------------
    # Signal / Slot connections
    # ------------------------------------------------------------------
    def _connect_signals(self):
        self.add_files_btn.clicked.connect(self._add_files)
        self.remove_sel_btn.clicked.connect(self._remove_selected)
        self.remove_all_btn.clicked.connect(self._remove_all)
        self.show_cmd_btn.clicked.connect(self._show_commands)
        self.start_btn.clicked.connect(self.start_encoding)
        self.stop_btn.clicked.connect(self.stop_encoding)
        self.terminate_btn.clicked.connect(self.terminate_encoding)
        self.start_action.triggered.connect(self.start_encoding)
        self.stop_action.triggered.connect(self.stop_encoding)
        self.terminate_action.triggered.connect(self.terminate_encoding)
        self.add_action.triggered.connect(self._add_files)
        self.exit_action.triggered.connect(self.close)
        self.about_action.triggered.connect(self._show_about)
        self.undo_action.triggered.connect(self._undo_remove)
        self.redo_action.triggered.connect(self._redo_remove)

        self.theme_action_light.triggered.connect(lambda: self._set_theme(Theme.LIGHT))
        self.theme_action_dark.triggered.connect(lambda: self._set_theme(Theme.DARK))

        selection_model = self.queue_table.selectionModel()
        selection_model.selectionChanged.connect(self._on_queue_selection_changed)

        self.apply_settings_btn.clicked.connect(self._on_apply_settings)
        self.save_default_btn.clicked.connect(self._on_save_default)

        self._queue_model.rowsInserted.connect(self._update_summary)
        self._queue_model.rowsRemoved.connect(self._update_summary)
        self._queue_model.dataChanged.connect(self._update_summary)

        self._repository.item_removed.connect(self._on_item_removed)
        self._repository.items_removed.connect(self._on_items_removed)

        # Shortcuts: check focus before undo/redo
        self.undo_shortcut.activated.connect(self._undo_remove)
        self.redo_shortcut.activated.connect(self._redo_remove)

    # ------------------------------------------------------------------
    # HandBrakeCLI handling (validation and help)
    # ------------------------------------------------------------------
    def _browse_handbrake(self):
        file_filter = "Executable (*)" if sys.platform != "win32" else "Executable (*.exe)"
        path, _ = QFileDialog.getOpenFileName(self, "Select HandBrakeCLI", "", file_filter)
        if path:
            self.status_bar.showMessage("Validating HandBrakeCLI...")
            # Stage 1: quick validation (UI thread)
            valid, msg = ValidationService.validate_handbrake_path(path)
            if not valid:
                QMessageBox.critical(self, "Invalid HandBrakeCLI", msg)
                self.status_bar.showMessage("HandBrakeCLI validation failed (quick).")
                return
            # Stage 2: background validation
            self._validate_handbrake_background(path)

    def _validate_handbrake_background(self, path: str):
        """Start background validation worker."""
        self.status_bar.showMessage("Validating HandBrakeCLI in background...")
        self._validation_thread = QThread()
        self._validation_worker = ValidationWorker()
        self._validation_worker.moveToThread(self._validation_thread)

        self._validation_worker.finished.connect(
            lambda success, msg: self._on_validation_done(success, msg, path, self._validation_thread)
        )
        self._validation_thread.started.connect(lambda: self._validation_worker.validate(path))
        self._validation_thread.start()

    def _on_validation_done(self, success: bool, msg: str, path: str, thread: QThread):
        if success:
            self.handbrake_edit.setText(path)
            self._settings_service.set_handbrake_path(path)
            self.status_bar.showMessage("HandBrakeCLI validated successfully.")
            self._cached_help_text = None  # Clear cache because path might have changed
        else:
            QMessageBox.critical(self, "Invalid HandBrakeCLI", f"Validation failed:\n\n{msg}")
            self.status_bar.showMessage("HandBrakeCLI validation failed.")
        thread.quit()
        thread.wait()

    def _show_handbrake_help(self):
        path = self.handbrake_edit.text().strip()
        if not path or not os.path.isfile(path):
            QMessageBox.warning(self, "Error", "Please set a valid HandBrakeCLI executable path.")
            return

        # If cached, show immediately
        if self._cached_help_text is not None:
            dlg = HandBrakeHelpDialog(self._cached_help_text, self)
            dlg.exec()
            return

        self.status_bar.showMessage("Fetching HandBrakeCLI help...")
        # Start background worker
        self._help_thread = QThread()
        self._help_worker = HelpWorker()
        self._help_worker.moveToThread(self._help_thread)

        self._help_worker.finished.connect(
            lambda text: self._on_help_fetched(text, self._help_thread)
        )
        self._help_thread.started.connect(lambda: self._help_worker.fetch_help(path))
        self._help_thread.start()

    def _on_help_fetched(self, text: str, thread: QThread):
        self._cached_help_text = text
        dlg = HandBrakeHelpDialog(text, self)
        dlg.exec()
        self.status_bar.showMessage("HandBrakeCLI help displayed.")
        thread.quit()
        thread.wait()

    # ------------------------------------------------------------------
    # Queue management
    # ------------------------------------------------------------------
    def _add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select Video Files", "",
            "Video Files (*.mkv *.mp4 *.avi *.mov *.wmv *.m4v *.m2ts *.ts *.webm *.flv *.vob *.ogv *.mpg *.mpeg *.m2t *.mts *.divx *.xvid *.asf *.3gp *.3g2 *.f4v *.dv *.evo)"
        )
        if files:
            self._prompt_settings_and_add(files)

    def _on_files_dropped(self, files):
        self._prompt_settings_and_add(files)

    def _prompt_settings_and_add(self, file_paths):
        existing_paths = self._repository.get_all_input_paths()
        new_paths = []
        duplicates = []
        for path in file_paths:
            if path in existing_paths:
                duplicates.append(path)
            else:
                new_paths.append(path)

        if duplicates:
            dlg = DuplicateDialog(duplicates, self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                action = dlg.get_action()
                if action == DuplicateAction.SKIP:
                    pass
                elif action == DuplicateAction.ADD:
                    new_paths = file_paths
                else:
                    return
            else:
                return

        if not new_paths:
            return

        dlg = SettingsDialog(self._default_encoding_settings, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            settings = dlg.get_settings()
            sorted_new_paths = natsorted(new_paths, key=lambda p: os.path.basename(p))
            items = []
            for path in sorted_new_paths:
                item_settings = settings.copy()
                output_path = self._output_naming.generate_output_path(path, item_settings)
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                item = QueueItem(
                    input_path=path,
                    output_path=output_path,
                    settings=item_settings
                )
                items.append(item)
            self._repository.add_items(items)
            self._update_summary()

    def _update_summary(self):
        total = self._repository.total_count()
        success = self._repository.count_by_status(QueueStatus.SUCCESS)
        failed = self._repository.count_by_status(QueueStatus.FAILED)
        skipped = self._repository.count_by_status(QueueStatus.SKIPPED)
        terminated = self._repository.count_by_status(QueueStatus.TERMINATED)
        self.summary_total.setText(f"Total: {total}")
        self.summary_success.setText(f"Successful: {success}")
        self.summary_failed.setText(f"Failed: {failed}")
        self.summary_skipped.setText(f"Skipped: {skipped}")
        self.summary_terminated.setText(f"Terminated: {terminated}")

    def _remove_selected(self):
        selected_rows = [idx.row() for idx in self.queue_table.selectionModel().selectedRows()]
        if not selected_rows:
            QMessageBox.information(self, "No Selection", "No rows selected to remove.")
            return

        # Gather items and their current row indices before removal
        items_with_indices = []
        ids = []
        for row in selected_rows:
            item = self._queue_model.get_item_by_row(row)
            if item:
                items_with_indices.append((row, item))
                ids.append(item.id)

        encoding_items = self._repository.get_items_by_status(QueueStatus.ENCODING)
        encoding_ids = {item.id for item in encoding_items}
        selected_ids_set = set(ids)
        if selected_ids_set & encoding_ids:
            QMessageBox.warning(self, "Cannot Remove", "Cannot remove rows that are currently encoding.")
            return

        # Remove items
        removed_items = self._repository.remove_items(ids)
        if removed_items:
            # Store operation with indices
            # Note: the indices are the original positions before removal
            # We'll store them as (row, item) where row is the original index.
            self._undo_manager.push_removal(items_with_indices)
            self._update_undo_redo_actions()

        self._update_summary()

    def _remove_all(self):
        encoding_ids = {item.id for item in self._repository.get_items_by_status(QueueStatus.ENCODING)}
        all_items = self._repository.get_all_items()
        to_remove = []
        items_with_indices = []
        for i, item in enumerate(all_items):
            if item.id not in encoding_ids:
                to_remove.append(item.id)
                items_with_indices.append((i, item))
        if not to_remove:
            QMessageBox.information(self, "No Removable Rows", "No non-encoding rows to remove.")
            return
        if encoding_ids:
            reply = QMessageBox.question(
                self, "Remove All",
                f"Some rows are currently encoding and will be kept. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        removed_items = self._repository.remove_items(to_remove)
        if removed_items:
            self._undo_manager.push_removal(items_with_indices)
            self._update_undo_redo_actions()
        self._update_summary()

    def _undo_remove(self):
        # Check if queue table has focus, if not, ignore
        if QApplication.focusWidget() != self.queue_table:
            return
        if not self._undo_manager.can_undo():
            return
        op = self._undo_manager.undo()
        if op:
            # Insert items in descending order of original row index
            sorted_items = sorted(op.items_with_indices, key=lambda x: x[0], reverse=True)
            for row, item in sorted_items:
                self._queue_model.insert_item_at(item, row)
            self._update_summary()
            self._update_undo_redo_actions()

    def _redo_remove(self):
        if QApplication.focusWidget() != self.queue_table:
            return
        if not self._undo_manager.can_redo():
            return
        op = self._undo_manager.redo()
        if op:
            # Remove the items again (their ids are in the operation)
            ids = [item.id for _, item in op.items_with_indices]
            self._repository.remove_items(ids)
            self._update_summary()
            self._update_undo_redo_actions()

    def _update_undo_redo_actions(self):
        can_undo = self._undo_manager.can_undo()
        can_redo = self._undo_manager.can_redo()
        self.undo_action.setEnabled(can_undo)
        self.redo_action.setEnabled(can_redo)

    def _on_item_removed(self, item_id: str):
        self._update_undo_redo_actions()

    def _on_items_removed(self, item_ids: List[str]):
        self._update_undo_redo_actions()

    def _show_commands(self):
        selected_rows = [idx.row() for idx in self.queue_table.selectionModel().selectedRows()]
        if not selected_rows:
            QMessageBox.information(self, "No Selection", "Please select at least one row to preview commands.")
            return
        hb_path = self.handbrake_edit.text().strip()
        if not hb_path or not os.path.isfile(hb_path):
            QMessageBox.warning(self, "Error", "Please set a valid HandBrakeCLI executable path.")
            return
        commands = []
        for row in selected_rows:
            item = self._queue_model.get_item_by_row(row)
            if item:
                cmd = EncodingService.build_command(item.settings, hb_path,
                                                    item.input_path, item.output_path)
                commands.append(" ".join(cmd))
        if commands:
            dlg = CommandPreviewDialog(commands, self)
            dlg.exec()

    # ------------------------------------------------------------------
    # Queue selection & left‑panel sync
    # ------------------------------------------------------------------
    def _on_queue_selection_changed(self, selected, deselected):
        selected_rows = [idx.row() for idx in self.queue_table.selectionModel().selectedRows()]
        if len(selected_rows) == 1:
            row = selected_rows[0]
            item = self._queue_model.get_item_by_row(row)
            if item:
                self._settings_binder.load_values(item.settings)
        else:
            self._load_defaults_into_panel()

    def _load_defaults_into_panel(self):
        self._settings_binder.load_values(self._default_encoding_settings)

    def _on_apply_settings(self):
        selected_rows = [idx.row() for idx in self.queue_table.selectionModel().selectedRows()]
        if not selected_rows:
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            sorting_enabled = self.queue_table.isSortingEnabled()
            self.queue_table.setSortingEnabled(False)
            self.queue_table.setUpdatesEnabled(False)
            self.queue_table.blockSignals(True)
            sel_model = self.queue_table.selectionModel()
            if sel_model:
                sel_model.blockSignals(True)

            new_settings = self._settings_binder.get_encoding_settings()
            ids = []
            for row in selected_rows:
                item_id = self._queue_model.get_id_by_row(row)
                if item_id:
                    ids.append(item_id)
            items = self._repository.get_items_by_ids(ids)
            for item in items:
                item.settings = new_settings.copy()
                item.output_path = self._output_naming.generate_output_path(item.input_path, item.settings)
                self._repository.update_item(item)

            self.queue_table.blockSignals(False)
            if sel_model:
                sel_model.blockSignals(False)
            self.queue_table.setUpdatesEnabled(True)
            self.queue_table.setSortingEnabled(sorting_enabled)
            self.queue_table.viewport().update()
        finally:
            QApplication.restoreOverrideCursor()

    def _on_save_default(self):
        settings = self._settings_binder.get_encoding_settings()
        self._default_encoding_settings = settings
        self._save_default_encoding_settings(settings)
        self.status_bar.showMessage("Default settings saved.")

    # ------------------------------------------------------------------
    # Theme management
    # ------------------------------------------------------------------
    def _set_theme(self, theme: Theme):
        if theme == Theme.DARK:
            QApplication.setPalette(dark_palette())
            self.live_status.setStyleSheet(
                "color: lime; background-color: #1e1e1e; border: 1px solid gray; padding: 4px;"
            )
            self.theme_action_dark.setChecked(True)
        else:
            QApplication.setPalette(light_palette())
            self.live_status.setStyleSheet(
                "color: darkgreen; background-color: #f0f0f0; border: 1px solid gray; padding: 4px;"
            )
            self.theme_action_light.setChecked(True)
        self._settings_service.set_theme(theme.value)
        self._rebuild_log()

    def _restore_theme(self):
        saved_theme = self._settings_service.get_theme()
        if saved_theme not in ("light", "dark"):
            saved_theme = "light"
        self._set_theme(Theme(saved_theme))

    # ------------------------------------------------------------------
    # Column state persistence
    # ------------------------------------------------------------------
    def _save_column_state(self):
        state = self.queue_table.horizontalHeader().saveState()
        if state:
            self._settings_service.set_column_state(state)

    def _restore_column_state(self):
        state = self._settings_service.get_column_state()
        if state:
            self.queue_table.horizontalHeader().restoreState(state)
            header = self.queue_table.horizontalHeader()
            for col in range(self._queue_model.columnCount()):
                current_width = header.sectionSize(col)
                min_width = self._get_min_column_width(col)
                if current_width < min_width:
                    header.resizeSection(col, min_width)

    def _get_min_column_width(self, col: int) -> int:
        text = self._queue_model.headerData(col, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole)
        if text:
            font = self.queue_table.font()
            fm = QFontMetrics(font)
            width = fm.horizontalAdvance(text) + 20
            return max(width, 60)
        return 60

    # ------------------------------------------------------------------
    # Window state persistence
    # ------------------------------------------------------------------
    def _save_window_state(self):
        self._settings_service.set_window_geometry(self.saveGeometry())
        self._settings_service.set_window_state(self.saveState())

    def _restore_window_state(self):
        geometry = self._settings_service.get_window_geometry()
        if geometry:
            self.restoreGeometry(geometry)
        state = self._settings_service.get_window_state()
        if state:
            self.restoreState(state)

    # ------------------------------------------------------------------
    # Encoding controls
    # ------------------------------------------------------------------
    def start_encoding(self):
        hb_path = self.handbrake_edit.text().strip()
        if not hb_path or not os.path.isfile(hb_path):
            QMessageBox.warning(self, "Error", "Please set a valid HandBrakeCLI executable path.")
            return

        for item in self._repository.get_items_by_status(QueueStatus.TERMINATED):
            item.status = QueueStatus.PENDING
            self._repository.update_item(item)

        pending = self._repository.get_pending_items()
        if not pending:
            QMessageBox.warning(self, "Error", "No pending items in queue.")
            return

        self._save_default_encoding_settings(self._default_encoding_settings)
        self._save_app_settings()

        self._encoding_active = True
        self._stop_after_current = False

        self.start_btn.setEnabled(False)
        self.start_action.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.stop_action.setEnabled(True)
        self.terminate_btn.setEnabled(True)
        self.terminate_action.setEnabled(True)
        self.add_files_btn.setEnabled(True)
        self.remove_sel_btn.setEnabled(True)
        self.remove_all_btn.setEnabled(True)

        self.progress_bar.setValue(0)
        self._start_time = datetime.now()
        self._elapsed_timer.start(1000)

        self._log_entries.clear()
        self.log_widget.clear()
        self._log_buffer.clear()
        self._log_append(f"Encoding started at {self._start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        self.live_status.setText("Running...")

        for item in pending:
            item.progress = 0
            self._repository.update_item(item)

        self._update_summary()

        # Create worker and thread
        self._worker_thread = QThread()
        self._worker = EncodingWorker(hb_path)
        self._worker.moveToThread(self._worker_thread)

        # Connect signals
        self._worker.progress_signal.connect(self._on_worker_progress)
        self._worker.status_signal.connect(self._on_worker_status)
        self._worker.file_started.connect(self._on_worker_file_started)
        self._worker.file_finished.connect(self._on_worker_file_finished)
        self._worker.log_signal.connect(self._log_append)
        self._worker.log_progress_signal.connect(self._update_live_status)

        # Connect the process item signal to the worker's slot
        self.process_item_signal.connect(self._worker.process_item)

        self._worker_thread.start()

        self._send_next_item()

    def _send_next_item(self):
        if not self._encoding_active or self._worker is None:
            return
        if self._stop_after_current:
            self._finish_encoding()
            return

        pending = self._repository.get_pending_items()
        if not pending:
            self._finish_encoding()
            return

        item = pending[0]
        item_copy = item.copy()
        # Emit signal to start processing in worker thread
        self.process_item_signal.emit(item.id, item_copy)

    def _on_worker_progress(self, item_id: str, percent: int):
        item = self._repository.get_item(item_id)
        if item:
            item.progress = percent
            self._repository.update_item(item)

    def _on_worker_status(self, item_id: str, status: QueueStatus):
        item = self._repository.get_item(item_id)
        if item:
            item.status = status
            self._repository.update_item(item)
            self._update_summary()

    def _on_worker_file_started(self, item_id: str, output_path: str):
        item = self._repository.get_item(item_id)
        if item:
            item.status = QueueStatus.ENCODING
            self._repository.update_item(item)
            self.status_bar.showMessage(f"Encoding: {os.path.basename(output_path)}")

    def _on_worker_file_finished(self, item_id: str, success: bool, exit_code: int):
        self._update_summary()
        total = self._repository.total_count()
        if total > 0:
            processed = sum(1 for item in self._repository.get_all_items()
                            if item.status in (QueueStatus.SUCCESS, QueueStatus.FAILED, QueueStatus.SKIPPED, QueueStatus.TERMINATED))
            overall = int(processed / total * 100)
            self.progress_bar.setValue(overall)
        self._send_next_item()

    def stop_encoding(self):
        if self._encoding_active:
            self._stop_after_current = True
            self.stop_btn.setEnabled(False)
            self.stop_action.setEnabled(False)
            self.status_bar.showMessage("Stopping after current file...")

    def terminate_encoding(self):
        if self._encoding_active and self._worker:
            self._worker.terminate()
            self.terminate_btn.setEnabled(False)
            self.terminate_action.setEnabled(False)
            self.stop_btn.setEnabled(False)
            self.stop_action.setEnabled(False)
            self._stop_after_current = True
            self.status_bar.showMessage("Terminating...")

    def _finish_encoding(self):
        if not self._encoding_active:
            return
        self._encoding_active = False
        self._stop_after_current = False

        if self._worker_thread:
            self._worker_thread.quit()
            self._worker_thread.wait()
            self._worker_thread = None
            self._worker = None

        self._elapsed_timer.stop()
        self.start_btn.setEnabled(True)
        self.start_action.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.stop_action.setEnabled(False)
        self.terminate_btn.setEnabled(False)
        self.terminate_action.setEnabled(False)
        self.add_files_btn.setEnabled(True)
        self.remove_sel_btn.setEnabled(True)
        self.remove_all_btn.setEnabled(True)
        self.status_bar.showMessage("Encoding finished")
        if self._start_time:
            elapsed = datetime.now() - self._start_time
            self.summary_time.setText(f"Elapsed: {str(elapsed).split('.')[0]}")
            self._log_append(f"Encoding finished at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            self._log_append(f"Total elapsed: {elapsed}")
        self.live_status.setText("Finished")

    def _update_elapsed(self):
        if self._start_time:
            secs = (datetime.now() - self._start_time).total_seconds()
            self.summary_time.setText(f"Elapsed: {format_elapsed(secs)}")

    # ------------------------------------------------------------------
    # Logging methods (optimized with batching)
    # ------------------------------------------------------------------
    def _log_append(self, text: str, role: str = "normal"):
        entry = LogEntry(text=text, role=role)
        self._log_entries.append(entry)
        self._log_buffer.append(entry)

    def _flush_logs(self):
        if not self._log_buffer:
            return
        theme = self._settings_service.get_theme()
        parts = []
        for entry in self._log_buffer:
            color = self._get_color(entry.role, theme)
            parts.append(f'<span style="color:{color};">{entry.text}</span>')
        html = "<br>".join(parts) + "<br>"
        cursor = self.log_widget.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertHtml(html)
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.log_widget.setTextCursor(cursor)
        self.log_widget.ensureCursorVisible()
        self._log_buffer.clear()

    def _get_color(self, role: str, theme: str) -> str:
        if role.startswith("#") or role in ("red", "green", "blue", "orange", "yellow", "cyan", "magenta", "white", "black", "gray"):
            return role
        mapping = {
            "normal": {"light": "#000000", "dark": "#ffffff"},
            "success": {"light": "#2ecc71", "dark": "#2ecc71"},
            "warning": {"light": "#f39c12", "dark": "#f39c12"},
            "error": {"light": "#e74c3c", "dark": "#e74c3c"},
            "command": {"light": "#0047ab", "dark": "#7fb3ff"},
        }
        color_map = mapping.get(role)
        if color_map:
            return color_map.get(theme, color_map["light"])
        return "#000000" if theme == "light" else "#ffffff"

    def _rebuild_log(self):
        theme = self._settings_service.get_theme()
        parts = []
        for entry in self._log_entries:
            color = self._get_color(entry.role, theme)
            parts.append(f'<span style="color:{color};">{entry.text}</span>')
        html = "<br>".join(parts)
        self.log_widget.setHtml(html)
        cursor = self.log_widget.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.log_widget.setTextCursor(cursor)
        self.log_widget.ensureCursorVisible()

    def _update_live_status(self, text: str):
        self.live_status.setText(text)

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------
    def _show_about(self):
        about_text = f"""
        <h2>{APP_NAME} — {APP_SUBTITLE}</h2>
        <p><b>Version:</b> {get_app_full_version()}</p>
        <p><b>Developer:</b> {APP_AUTHOR}</p>
        <p><b>Description:</b><br>{APP_DESCRIPTION}</p>
        <p><b>GitHub:</b> <a href="{GITHUB_LINK}">{GITHUB_LINK}</a></p>
        <p><b>Copyright:</b> {APP_COPYRIGHT}</p>
        """
        QMessageBox.about(self, f"About {APP_NAME}", about_text)

    def closeEvent(self, event):
        self._save_default_encoding_settings(self._default_encoding_settings)
        self._save_app_settings()
        self._save_column_state()
        self._save_window_state()
        if self._encoding_active and self._worker:
            self._worker.terminate()
            self._worker_thread.quit()
            self._worker_thread.wait()
        logging.shutdown()
        event.accept()

# =============================================================================
# Global settings_service reference (for crash handler and dialogs)
# =============================================================================

settings_service: Optional[SettingsService] = None

# =============================================================================
# Crash Handling
# =============================================================================

def exception_hook(exc_type, exc_value, exc_tb):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return

    tb_lines = traceback.format_exception(exc_type, exc_value, exc_tb)
    tb_text = ''.join(tb_lines)

    logger = logging.getLogger('BatchEncoder.Crash')
    logger.critical(f"Unhandled exception:\n{tb_text}")

    crash_dir = LOG_DIR / "crashes"
    crash_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    crash_file = crash_dir / f"crash_{timestamp}.log"
    try:
        with open(crash_file, 'w', encoding='utf-8') as f:
            f.write(f"Application: {APP_NAME} v{get_app_full_version()}\n")
            f.write(f"Python: {sys.version}\n")
            f.write(f"Platform: {sys.platform}\n")
            f.write(f"Qt: {QVersionNumber().toString()}\n")
            import PySide6
            f.write(f"PySide: {PySide6.__version__}\n")
            f.write(f"Working dir: {os.getcwd()}\n")
            if settings_service:
                f.write(f"HandBrake path: {settings_service.get_handbrake_path()}\n")
            else:
                f.write("HandBrake path: unknown\n")
            f.write(f"Traceback:\n{tb_text}\n")
    except Exception:
        pass

    try:
        app = QApplication.instance()
        if app and not app.startingUp():
            dlg = UnexpectedErrorDialog(str(exc_value), tb_text)
            dlg.exec()
    except Exception:
        pass

    sys.__excepthook__(exc_type, exc_value, exc_tb)

def thread_exception_hook(args):
    exception_hook(args.exc_type, args.exc_value, args.exc_tb)

# =============================================================================
# Entry point
# =============================================================================

def main():
    sys.excepthook = exception_hook
    threading.excepthook = thread_exception_hook

    logging_service = LoggingService()
    logger = logging.getLogger('BatchEncoder')
    logger.info(f"Starting {APP_NAME} v{get_app_full_version()}")

    app = QApplication(sys.argv)
    app.setOrganizationName("BatchEncoder")
    app.setApplicationName("BatchEncoderApp")
    app.setStyle("Fusion")

    global settings_service
    settings_service = SettingsService()

    window = MainWindow()
    window.show()

    ret = app.exec()
    logging.shutdown()
    sys.exit(ret)

if __name__ == "__main__":
    main()
