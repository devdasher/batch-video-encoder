#!/usr/bin/env python3
"""
Batch Video Encoder using HandBrakeCLI
GUI built with PySide6

Refactored version with dynamic settings system, clean architecture,
improved maintainability, and new features.
"""

import sys
import os
import re
import shlex
import copy
import time
import threading
import subprocess
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Any, Union, Tuple
from dataclasses import dataclass, field
from enum import Enum, auto
from collections import deque
import queue

from PySide6.QtCore import (
    Qt, QThread, Signal, QTimer, QSettings, QSize, QModelIndex,
    QAbstractTableModel, QSortFilterProxyModel, QItemSelectionModel,
    QItemSelection, QObject, QMutex, QWaitCondition, Slot
)
from PySide6.QtGui import (
    QAction, QActionGroup, QTextCursor, QPalette, QColor, QBrush,
    QFont, QTextOption
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QFormLayout, QLineEdit, QCheckBox, QPushButton,
    QFileDialog, QTableWidget, QTableWidgetItem, QHeaderView,
    QTextEdit, QLabel, QProgressBar, QStatusBar, QSplitter,
    QMessageBox, QStyle, QScrollArea, QDialog, QDialogButtonBox,
    QAbstractItemView, QMenu, QSizePolicy, QComboBox, QTableView,
    QStyledItemDelegate, QStyleOptionProgressBar, QApplication as QtApp,
    QPlainTextEdit
)

# Try to import natsort for natural sorting
try:
    from natsort import natsorted
except ImportError:
    # Fallback simple sorting
    def natsorted(iterable):
        return sorted(iterable)

# =============================================================================
# Constants & Utilities
# =============================================================================

APP_NAME = "Batch Video Encoder"
APP_SUBTITLE = "Powered by HandBrake"
APP_VERSION = "0.1.7"
APP_STATUS = "Beta"
APP_AUTHOR = "devdasher"
APP_DESCRIPTION = (
    "A dynamic GUI for HandBrakeCLI with per‑file settings, "
    "live progress, drag & drop, and clean‑code design."
)
APP_COPYRIGHT = f"© 2026 {APP_AUTHOR}"
GITHUB_LINK = "https://github.com/devdasher/batch-video-encoder"
SETTINGS_VERSION = 1

SUPPORTED_EXTENSIONS = {
    '.mkv', '.mp4', '.avi', '.mov', '.wmv', '.m4v', '.m2ts', '.ts',
    '.webm', '.flv', '.vob', '.ogv', '.mpg', '.mpeg', '.m2t', '.mts',
    '.divx', '.xvid', '.asf', '.3gp', '.3g2', '.f4v', '.dv', '.evo'
}

def output_path(input_file: str) -> str:
    """Generate output path: same directory, subfolder 'encoded-files'."""
    src_dir = os.path.dirname(input_file)
    name = os.path.basename(input_file)
    return os.path.join(src_dir, "encoded-files", name)

def format_elapsed(seconds: int) -> str:
    """Return HH:MM:SS from total seconds."""
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d}"

def make_bool_item(value: bool) -> QTableWidgetItem:
    """Create a QTableWidgetItem showing ✅ or ❌ for boolean."""
    item = QTableWidgetItem("✅" if value else "❌")
    item.setTextAlignment(Qt.AlignCenter)
    return item

# =============================================================================
# Enums and Definitions
# =============================================================================

class WidgetType(Enum):
    TEXT = auto()          # single-line text input
    BOOL = auto()          # checkbox
    FILE = auto()          # text input + browse button
    COMBO = auto()         # dropdown
    MULTILINE = auto()     # multi-line text (e.g., extra args)

class LayoutType(Enum):
    FORM = auto()          # QFormLayout (label‑field pairs)
    VERTICAL = auto()      # QVBoxLayout (checkboxes stacked)

class SettingGroup(Enum):
    DIMENSIONS = "Dimensions"
    VIDEO = "Video"
    AUDIO = "Audio"
    SUBTITLE = "Subtitle"
    CHAPTERS = "Chapters"
    FILTERS = "Filters"
    OTHER = "Other Settings"

@dataclass
class GroupDefinition:
    """Definition of a settings group: its display name and layout style."""
    name: str
    layout: LayoutType

# Group definitions – one per SettingGroup
GROUP_DEFS = {
    SettingGroup.DIMENSIONS: GroupDefinition("Dimensions", LayoutType.FORM),
    SettingGroup.VIDEO: GroupDefinition("Video", LayoutType.FORM),
    SettingGroup.AUDIO: GroupDefinition("Audio", LayoutType.FORM),
    SettingGroup.SUBTITLE: GroupDefinition("Subtitle", LayoutType.FORM),
    SettingGroup.CHAPTERS: GroupDefinition("Chapters", LayoutType.VERTICAL),
    SettingGroup.FILTERS: GroupDefinition("Filters", LayoutType.VERTICAL),
    SettingGroup.OTHER: GroupDefinition("Other Settings", LayoutType.FORM),
}

@dataclass
class SettingDefinition:
    """
    Definition of a single encoding setting.
    All fields are self‑explanatory; some are reserved for future extension.
    """
    key: str
    label: str
    placeholder: str = ""
    default: Any = ""
    widget_type: WidgetType = WidgetType.TEXT
    group: SettingGroup = SettingGroup.OTHER
    cli_arg: Optional[str] = None          # HandBrakeCLI argument (if any)
    browse_filter: str = "All files (*)"   # for file picker
    tooltip: str = ""
    choices: Optional[List[str]] = None    # for dropdown
    validator: Optional[Any] = None        # for future validation
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    visible: bool = True
    dependencies: List[str] = field(default_factory=list)
    category: str = "encoding"

    def __post_init__(self):
        # Ensure default is appropriate for widget type
        if self.widget_type == WidgetType.BOOL and not isinstance(self.default, bool):
            self.default = bool(self.default)

# All encoding settings definitions – single source of truth
ENCODING_SETTINGS = [
    # ===== DIMENSIONS =====
    SettingDefinition(
        "resolution", "Resolution Limit",
        choices=["480p", "576p", "720p", "1080p", "2160p (4K)", "4320p (8K)"],
        default="1080p",
        widget_type=WidgetType.COMBO,
        group=SettingGroup.DIMENSIONS,
        cli_arg=None,  # handled specially
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

# Keys for quick lookup
SETTING_KEYS = [s.key for s in ENCODING_SETTINGS]
DEFAULT_VALUES = {s.key: s.default for s in ENCODING_SETTINGS}

# Resolution mapping
RESOLUTION_MAP = {
    "480p": (720, 480),
    "576p": (720, 576),
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "2160p (4K)": (3840, 2160),
    "4320p (8K)": (7680, 4320),
}

# =============================================================================
# Data Models
# =============================================================================

@dataclass
class EncodingSettings:
    """
    Per‑file encoding settings.
    Internally uses a dict for flexibility; provides a clean interface.
    """
    data: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        # Ensure all keys exist with defaults
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
    def from_dict(cls, d: Dict[str, Any]) -> "EncodingSettings":
        return cls(data=d)

    def update(self, other: Dict[str, Any]) -> None:
        self.data.update(other)

    def copy(self) -> "EncodingSettings":
        """Return a deep copy of this settings object."""
        return EncodingSettings.from_dict(copy.deepcopy(self.data))

    # Optional attribute access (for convenience)
    def __getattr__(self, name: str) -> Any:
        if name in self.data:
            return self.data[name]
        raise AttributeError(f"'EncodingSettings' has no attribute '{name}'")


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


@dataclass
class QueueItem:
    """A single queue entry with input, output, settings, and status."""
    input_path: str
    output_path: str
    settings: EncodingSettings
    status: QueueStatus = QueueStatus.PENDING
    progress: int = 0
    date_added: datetime = field(default_factory=datetime.now)


@dataclass
class LogEntry:
    """A single log entry with semantic role."""
    text: str
    role: str  # "normal", "success", "warning", "error", "command", or custom color


@dataclass
class ApplicationSettings:
    """Application‑wide settings (not per‑file)."""
    handbrake_path: str = ""
    theme: str = "light"
    settings_version: int = SETTINGS_VERSION

    def save(self, settings: QSettings) -> None:
        settings.setValue("handbrake_path", self.handbrake_path)
        settings.setValue("theme", self.theme)
        settings.setValue("settings_version", self.settings_version)

    @classmethod
    def load(cls, settings: QSettings) -> "ApplicationSettings":
        # Check version and migrate if needed
        version = settings.value("settings_version", 0, type=int)
        # For now, just load; migration will be handled if needed in future
        path = settings.value("handbrake_path", "")
        theme = settings.value("theme", "light")
        return cls(handbrake_path=path, theme=theme, settings_version=version)

# =============================================================================
# Command Builder
# =============================================================================

class HandBrakeCommandBuilder:
    """Builds command line arguments for HandBrakeCLI from settings."""

    @classmethod
    def build(cls, settings: EncodingSettings, handbrake_path: str,
              input_file: str, output_file: str) -> List[str]:
        """
        Build the full command list.
        Returns a list suitable for subprocess.Popen.
        """
        cmd = [handbrake_path, '-i', input_file, '-o', output_file]
        cmd.extend(cls._build_settings_args(settings))
        return cmd

    @classmethod
    def _build_settings_args(cls, settings: EncodingSettings) -> List[str]:
        """Build the argument list from settings."""
        args = []
        # Handle resolution first (maps to maxWidth/maxHeight)
        resolution = settings.get("resolution")
        if resolution in RESOLUTION_MAP:
            w, h = RESOLUTION_MAP[resolution]
            args.extend(["--maxWidth", str(w), "--maxHeight", str(h)])

        # Process other settings
        for s in ENCODING_SETTINGS:
            if s.cli_arg is None:
                continue
            if s.key == "resolution":
                continue  # already handled
            val = settings.get(s.key)
            if val is None or val == "":
                continue
            if s.widget_type == WidgetType.BOOL:
                if val:
                    args.append(s.cli_arg)
            else:
                args.extend([s.cli_arg, str(val)])
        # Extra args (handled separately) - appended last to allow override
        extra = settings.get("extra_args", "")
        if extra:
            try:
                args.extend(shlex.split(extra))
            except ValueError:
                # If splitting fails, add as a single string (prevent errors)
                args.append(extra)
        return args

# =============================================================================
# Log Renderer
# =============================================================================

class LogRenderer:
    """Renders a list of LogEntry objects to HTML based on current theme."""

    _ROLE_COLORS = {
        "normal": {"light": "#000000", "dark": "#ffffff"},
        "success": {"light": "#2ecc71", "dark": "#2ecc71"},
        "warning": {"light": "#f39c12", "dark": "#f39c12"},
        "error": {"light": "#e74c3c", "dark": "#e74c3c"},
        "command": {"light": "#0047ab", "dark": "#7fb3ff"},
    }

    @classmethod
    def render(cls, entries: List[LogEntry], theme: str) -> str:
        parts = []
        for entry in entries:
            color = cls._get_color(entry.role, theme)
            parts.append(f'<span style="color:{color};">{entry.text}</span>')
        return "<br>".join(parts)

    @classmethod
    def _get_color(cls, role: str, theme: str) -> str:
        # If role is a color string, use it directly
        if role.startswith("#") or role in ("red", "green", "blue", "orange", "yellow", "cyan", "magenta", "white", "black", "gray"):
            return role
        mapping = cls._ROLE_COLORS.get(role)
        if mapping:
            return mapping.get(theme, mapping["light"])
        return "#000000" if theme == "light" else "#ffffff"

# =============================================================================
# Dynamic UI Builder for Encoding Settings
# =============================================================================

class SettingsUIBuilder:
    """
    Builds the complete settings widget hierarchy from the definitions.
    Returns a self‑contained QWidget with its own layout.
    Does not handle loading/saving – that is the responsibility of SettingsBinder.
    """

    def __init__(self, definitions: List[SettingDefinition], parent: QWidget = None):
        self.definitions = definitions
        self._widgets: Dict[str, QWidget] = {}          # key -> widget (lineedit, checkbox, combobox, textedit)
        self._bool_widgets: Dict[str, QCheckBox] = {}
        self._file_edit_widgets: Dict[str, QLineEdit] = {}
        self._combo_widgets: Dict[str, QComboBox] = {}
        self._multiline_widgets: Dict[str, QPlainTextEdit] = {}
        self._container = QWidget(parent)
        self._main_layout = QVBoxLayout(self._container)
        self._main_layout.setContentsMargins(5, 5, 5, 5)

    def build(self) -> QWidget:
        """Construct the UI and return the top‑level container widget."""
        # Group definitions by SettingGroup
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
            else:  # VERTICAL
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
                    btn.clicked.connect(lambda _, key=s.key: self._browse_file(key))
                    self._file_edit_widgets[s.key] = edit
                    self._widgets[s.key] = edit  # store the line edit
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
                        # Set default index if default in choices
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

        # Add stretch to push everything to the top
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self._main_layout.addWidget(spacer)

        return self._container

    def _browse_file(self, key: str) -> None:
        """Open file dialog and set the corresponding line edit."""
        edit = self._file_edit_widgets.get(key)
        if not edit:
            return
        # Find the definition to get browse_filter
        for s in self.definitions:
            if s.key == key:
                filter_str = s.browse_filter if s.browse_filter else "All files (*)"
                break
        else:
            filter_str = "All files (*)"
        path, _ = QFileDialog.getOpenFileName(self._container, "Select File", "", filter_str)
        if path:
            edit.setText(path)

    @property
    def widgets(self) -> Dict[str, QWidget]:
        """Return the mapping of setting keys to their primary widget."""
        return self._widgets


class SettingsBinder:
    """
    Binds values to/from the widgets created by SettingsUIBuilder.
    Provides load, get, and EncodingSettings conversion.
    """

    def __init__(self, widget_map: Dict[str, QWidget]):
        self._widget_map = widget_map

    def load_values(self, values: Union[EncodingSettings, Dict[str, Any]]) -> None:
        """Populate widgets from a dictionary or EncodingSettings object."""
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
        """Extract current values from widgets."""
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
        """Return an EncodingSettings object with current values."""
        return EncodingSettings.from_dict(self.get_values())

# =============================================================================
# Settings Dialog (for adding files)
# =============================================================================

class SettingsDialog(QDialog):
    """Dialog to set encoding options before adding files to the queue."""

    def __init__(self, defaults: Union[EncodingSettings, Dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Encoding Settings for New Files")
        self.resize(600, 700)

        # Build the settings UI
        builder = SettingsUIBuilder(ENCODING_SETTINGS, self)
        settings_widget = builder.build()

        # Create scroll area
        scroll = QScrollArea(self)
        scroll.setWidget(settings_widget)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        # Dialog buttons
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        # Main layout
        layout = QVBoxLayout(self)
        layout.addWidget(scroll)
        layout.addWidget(button_box)

        # Create binder and load defaults
        self._binder = SettingsBinder(builder.widgets)
        self._binder.load_values(defaults)

    def get_settings(self) -> EncodingSettings:
        """Return the settings as an EncodingSettings object."""
        return self._binder.get_encoding_settings()

# =============================================================================
# Help Dialog for HandBrakeCLI
# =============================================================================

class HandBrakeHelpDialog(QDialog):
    """Dialog to display HandBrakeCLI --help output with copy functionality."""

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

        # Buttons
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
            clipboard = QApplication.clipboard()
            clipboard.setText(text)

    def _copy_all(self):
        text = self.text_edit.toPlainText()
        clipboard = QApplication.clipboard()
        clipboard.setText(text)

# =============================================================================
# Queue Table Model
# =============================================================================

class QueueTableModel(QAbstractTableModel):
    """Model for the queue table using QueueItem objects."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: List[QueueItem] = []
        self._columns = ["File", "Status", "Progress", "Date Added"] + [s.label for s in ENCODING_SETTINGS]
        self._column_keys = [None, None, None, None] + [s.key for s in ENCODING_SETTINGS]
        self._boolean_keys = {s.key for s in ENCODING_SETTINGS if s.widget_type == WidgetType.BOOL}
        self._combo_keys = {s.key for s in ENCODING_SETTINGS if s.widget_type == WidgetType.COMBO}

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._items)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self._columns)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row = index.row()
        col = index.column()
        if row >= len(self._items):
            return None
        item = self._items[row]
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
                # For extra_args, replace newlines with '|' to keep single line
                if key == "extra_args" and isinstance(val, str):
                    val = val.replace("\n", "|").replace("\r", "")
                if isinstance(val, bool):
                    return "✅" if val else "❌"
                return str(val)
        elif role == Qt.ItemDataRole.TextAlignmentRole:
            # Center status, progress, date, and boolean/combo columns
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
        if not index.isValid():
            return False
        row = index.row()
        col = index.column()
        if row >= len(self._items):
            return False
        item = self._items[row]
        if role == Qt.EditRole:
            if col == 0:
                return False
            elif col == 1:
                if isinstance(value, QueueStatus):
                    item.status = value
                    self.dataChanged.emit(index, index, [role])
                    return True
            elif col == 2:
                if isinstance(value, int):
                    item.progress = value
                    self.dataChanged.emit(index, index, [role])
                    return True
            else:
                return False
        return False

    def headerData(self, section: int, orientation: Qt.Orientation, role=Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation == Qt.Orientation.Vertical and role == Qt.ItemDataRole.DisplayRole:
            # Row numbers (1-based)
            return section + 1
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if section < len(self._columns):
                return self._columns[section]
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled

    def add_items(self, items: List[QueueItem]) -> None:
        """Append new items to the model."""
        if not items:
            return
        start = len(self._items)
        self.beginInsertRows(QModelIndex(), start, start + len(items) - 1)
        self._items.extend(items)
        self.endInsertRows()

    def remove_rows(self, rows: List[int]) -> None:
        """Remove rows by index (sorted descending)."""
        if not rows:
            return
        rows = sorted(rows, reverse=True)
        for row in rows:
            if 0 <= row < len(self._items):
                self.beginRemoveRows(QModelIndex(), row, row)
                del self._items[row]
                self.endRemoveRows()

    def clear_all(self) -> None:
        """Remove all rows."""
        if not self._items:
            return
        self.beginRemoveRows(QModelIndex(), 0, len(self._items)-1)
        self._items.clear()
        self.endRemoveRows()

    def get_item(self, row: int) -> Optional[QueueItem]:
        if 0 <= row < len(self._items):
            return self._items[row]
        return None

    def update_item_status(self, row: int, status: QueueStatus) -> None:
        if 0 <= row < len(self._items):
            self._items[row].status = status
            index = self.index(row, 1)
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole])

    def update_item_progress(self, row: int, progress: int) -> None:
        if 0 <= row < len(self._items):
            self._items[row].progress = progress
            index = self.index(row, 2)
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.UserRole])

    def update_settings(self, rows: List[int], settings: EncodingSettings) -> None:
        """
        Update settings for given rows.
        Emits a single dataChanged signal for all changed cells to minimize UI updates.
        """
        if not rows:
            return
        # Copy settings for each row to avoid shared references
        for row in rows:
            if 0 <= row < len(self._items):
                self._items[row].settings = settings.copy()
        # Emit dataChanged for the whole range of setting columns (col 4 to end)
        min_row = min(rows)
        max_row = max(rows)
        start_idx = self.index(min_row, 4)
        end_idx = self.index(max_row, self.columnCount() - 1)
        self.dataChanged.emit(start_idx, end_idx, [Qt.ItemDataRole.DisplayRole])

    def get_all_items(self) -> List[QueueItem]:
        return self._items[:]  # Return a copy

    def get_all_input_paths(self) -> set:
        """Return a set of all input paths currently in the queue."""
        return {item.input_path for item in self._items}

    def get_items_by_range(self, start: int, end: int) -> List[QueueItem]:
        """Get items in index range [start, end) for worker."""
        return self._items[start:end] if start < end else []

    def get_pending_rows(self) -> List[int]:
        """Return indices of rows with status PENDING or TERMINATED (to be retried)."""
        return [i for i, item in enumerate(self._items) if item.status in (QueueStatus.PENDING, QueueStatus.TERMINATED)]

    def get_encoding_rows(self) -> List[int]:
        """Return indices of rows with status ENCODING."""
        return [i for i, item in enumerate(self._items) if item.status == QueueStatus.ENCODING]

    def count_by_status(self, status: QueueStatus) -> int:
        return sum(1 for item in self._items if item.status == status)

# =============================================================================
# Progress Bar Delegate for Queue Table
# =============================================================================

class ProgressBarDelegate(QStyledItemDelegate):
    """Delegate for rendering a progress bar in the progress column."""

    def paint(self, painter, option, index):
        if index.column() == 2:
            progress = index.data(Qt.UserRole)
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

# =============================================================================
# Custom Drop Table View (replaces DropTableWidget)
# =============================================================================

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
        self.setTextElideMode(Qt.ElideNone)  # Don't elide, show full text

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
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

# =============================================================================
# Duplicate Handling Dialog
# =============================================================================

class DuplicateDialog(QDialog):
    """Dialog to handle duplicate files when adding to queue."""

    def __init__(self, duplicates: List[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Duplicate Files Detected")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)

        label = QLabel("The following files already exist in the queue:")
        layout.addWidget(label)

        list_widget = QTextEdit()
        list_widget.setPlainText("\n".join(duplicates))
        list_widget.setReadOnly(True)
        list_widget.setMaximumHeight(150)
        layout.addWidget(list_widget)

        # Buttons
        button_box = QDialogButtonBox()
        skip_btn = QPushButton("Skip Duplicates")
        add_btn = QPushButton("Add Anyway")
        cancel_btn = QPushButton("Cancel")
        button_box.addButton(skip_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        button_box.addButton(add_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        button_box.addButton(cancel_btn, QDialogButtonBox.ButtonRole.RejectRole)

        skip_btn.clicked.connect(self.accept)
        add_btn.clicked.connect(self.reject)  # reject means add anyway?
        # Actually we want to distinguish. We'll store result.
        button_box.accepted.connect(self._on_accepted)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self._chosen_action = "skip"  # default

    def _on_accepted(self):
        # Determine which button triggered by sender
        sender = self.sender()
        if hasattr(sender, 'button'):
            # button_box
            btn = sender.button(sender.standardButton(sender.clickedButton()))
            if btn.text() == "Skip Duplicates":
                self._chosen_action = "skip"
            elif btn.text() == "Add Anyway":
                self._chosen_action = "add"
            else:
                self._chosen_action = "add"
        self.accept()

    def get_action(self) -> str:
        return self._chosen_action

# =============================================================================
# Command Preview Dialog
# =============================================================================

class CommandPreviewDialog(QDialog):
    """Dialog to preview commands for selected rows."""

    def __init__(self, commands: List[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Command Preview")
        self.resize(700, 500)

        layout = QVBoxLayout(self)

        # Text edit for commands
        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setFont(QFont("Courier New", 9))
        self.text_edit.setPlainText("\n\n".join(commands))
        layout.addWidget(self.text_edit)

        # Buttons
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
            clipboard = QApplication.clipboard()
            clipboard.setText(text)
        else:
            # Copy all if no selection
            self._copy_all()

    def _copy_all(self):
        text = self.text_edit.toPlainText()
        clipboard = QApplication.clipboard()
        clipboard.setText(text)

# =============================================================================
# Encoding Worker (QObject, runs in separate thread)
# =============================================================================

class EncodingWorker(QObject):
    """Worker object that processes a single queue item. Runs in its own thread."""

    # Signals emitted to the main thread
    progress_signal = Signal(int, int)          # row, percent
    status_signal = Signal(int, QueueStatus)    # row, status
    file_started = Signal(int, str)             # row, output_path
    file_finished = Signal(int, bool, int)      # row, success, exit_code
    log_signal = Signal(str, str)               # text, role
    log_progress_signal = Signal(str)           # raw progress line

    # Compiled regex for progress parsing (class-level)
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

    @Slot(int, QueueItem)
    def process_item(self, row: int, item: QueueItem):
        """
        Process a single queue item. This slot is called from the main thread
        via a signal, but executes in the worker's thread.
        """
        self._stop_event.clear()
        self._termination_requested = False
        input_file = item.input_path
        output_file = item.output_path
        settings = item.settings
        overwrite = settings.get("overwrite", False)

        # Check for output existence
        if not overwrite and os.path.exists(output_file):
            self.log_signal.emit(f"Skipping (output exists): {output_file}", "warning")
            self.status_signal.emit(row, QueueStatus.SKIPPED)
            self.file_finished.emit(row, False, -1)
            return

        # Build command
        cmd = HandBrakeCommandBuilder.build(settings, self.handbrake_path,
                                            input_file, output_file)
        self.log_signal.emit(f"Encoding: {input_file}", "normal")
        self.log_signal.emit(f"Command: {' '.join(cmd)}", "command")
        self.file_started.emit(row, output_file)

        start_time = time.time()
        try:
            # Use CREATE_NO_WINDOW on Windows to hide console
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
                    # Throttle updates: emit only if percent changed significantly and time passed
                    now = time.time()
                    if (abs(percent - self._last_progress_value) >= 1.0 or
                        now - self._last_progress_time >= 0.3):
                        self.progress_signal.emit(row, int(percent))
                        self.log_progress_signal.emit(line)
                        self._last_progress_value = percent
                        self._last_progress_time = now
                else:
                    # Log all other lines
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

            # Determine final status
            if self._termination_requested or self._stop_event.is_set():
                status = QueueStatus.TERMINATED
                self.log_signal.emit(f"Terminated by user after {elapsed:.1f}s", "warning")
            elif success:
                self.log_signal.emit(f"Finished successfully in {elapsed:.1f}s", "success")
                status = QueueStatus.SUCCESS
            else:
                self.log_signal.emit(f"Failed with exit code {exit_code} in {elapsed:.1f}s", "error")
                status = QueueStatus.FAILED

            self.status_signal.emit(row, status)
            self.file_finished.emit(row, success, exit_code)
            # Ensure final progress is 100% on success
            if status == QueueStatus.SUCCESS:
                self.progress_signal.emit(row, 100)
        except Exception as e:
            self.log_signal.emit(f"Error: {str(e)}", "error")
            self.status_signal.emit(row, QueueStatus.FAILED)
            self.file_finished.emit(row, False, -1)
        finally:
            self._current_process = None

    @Slot()
    def terminate(self):
        """Terminate the current subprocess immediately."""
        self._stop_event.set()
        self._termination_requested = True
        with self._lock:
            if self._current_process and self._current_process.poll() is None:
                try:
                    self._current_process.terminate()
                except Exception:
                    pass

# =============================================================================
# Light/Dark palette helpers
# =============================================================================

def light_palette():
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


def dark_palette():
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


# =============================================================================
# Other helpers
# =============================================================================

def get_app_full_version():
    """Returns version with status, e.g., '0.1.4 (Beta)'"""
    if APP_STATUS.lower() in ("stable", "release"):
        return APP_VERSION
    return f"{APP_VERSION} ({APP_STATUS})"

# =============================================================================
# Main Window
# =============================================================================

class MainWindow(QMainWindow):
    """Main application window."""

    # Signal to send a queue item to the worker thread
    process_item_signal = Signal(int, QueueItem)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{get_app_full_version()}")
        self.setMinimumSize(900, 650)
        self.resize(1100, 700)

        self._log_entries: List[LogEntry] = []
        self._encoding_active = False
        self._stop_after_current = False
        self._start_time = None
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.timeout.connect(self._update_elapsed)

        # Worker and thread
        self._worker = None
        self._worker_thread = None

        # Log batching
        self._log_buffer: List[LogEntry] = []
        self._log_flush_timer = QTimer(self)
        self._log_flush_timer.setInterval(50)  # 50 ms flush interval
        self._log_flush_timer.timeout.connect(self._flush_logs)
        self._log_flush_timer.start()

        # Settings
        self._qsettings = QSettings("BatchEncoder", "BatchEncoderApp")
        self._app_settings = ApplicationSettings.load(self._qsettings)
        self._default_encoding_settings = self._load_default_encoding_settings()

        # UI
        self._create_actions()
        self._create_menu()
        self._create_statusbar()
        self._create_central_widget()
        self._connect_signals()
        self._restore_theme()
        self._load_handbrake_path()
        self._load_defaults_into_panel()

    # ------------------------------------------------------------------
    # Settings helpers
    # ------------------------------------------------------------------
    def _load_default_encoding_settings(self) -> EncodingSettings:
        data = {}
        for s in ENCODING_SETTINGS:
            if s.widget_type == WidgetType.BOOL:
                val = self._qsettings.value(f"default_{s.key}", s.default, type=bool)
            elif s.widget_type == WidgetType.COMBO:
                val = self._qsettings.value(f"default_{s.key}", s.default)
                # Ensure value is in choices
                if s.choices and val not in s.choices:
                    val = s.default
            else:
                val = self._qsettings.value(f"default_{s.key}", s.default)
            data[s.key] = val
        return EncodingSettings.from_dict(data)

    def _save_default_encoding_settings(self, settings: EncodingSettings) -> None:
        for key, val in settings.to_dict().items():
            self._qsettings.setValue(f"default_{key}", val)

    def _load_handbrake_path(self):
        """Set default HandBrakeCLI path for both development and portable builds."""
        # If a path is already saved, just display it
        if self._app_settings.handbrake_path:
            self.handbrake_edit.setText(self._app_settings.handbrake_path)
            return

        # Determine the base directory:
        # - For PyInstaller bundles, sys.executable is the .exe itself
        # - For normal scripts, __file__ is the .py file
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))

        # Try the old location: /tools/HandBrakeCLI.exe
        if sys.platform == "win32":
            default_path = os.path.join(base_dir, "tools", "HandBrakeCLI.exe")
        else:
            default_path = os.path.join(base_dir, "tools", "HandBrakeCLI")

        # If not found, try the same folder as the executable (for portable one‑file)
        if not os.path.isfile(default_path):
            if sys.platform == "win32":
                fallback = os.path.join(base_dir, "HandBrakeCLI.exe")
            else:
                fallback = os.path.join(base_dir, "HandBrakeCLI")
            if os.path.isfile(fallback):
                default_path = fallback

        if os.path.isfile(default_path):
            self._app_settings.handbrake_path = default_path

        # Finally, display the resolved path (or empty if not found)
        self.handbrake_edit.setText(self._app_settings.handbrake_path)

    def _save_app_settings(self) -> None:
        self._app_settings.handbrake_path = self.handbrake_edit.text().strip()
        self._app_settings.save(self._qsettings)

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

        self.theme_action_light = QAction("Light", self, checkable=True)
        self.theme_action_dark = QAction("Dark", self, checkable=True)
        self.theme_group = QActionGroup(self)
        self.theme_group.addAction(self.theme_action_light)
        self.theme_group.addAction(self.theme_action_dark)
        self.theme_action_light.setChecked(True)

    def _create_menu(self):
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("&File")
        file_menu.addAction(self.add_action)
        file_menu.addSeparator()
        file_menu.addAction(self.exit_action)

        view_menu = menu_bar.addMenu("&View")
        theme_menu = view_menu.addMenu("Theme")
        theme_menu.addAction(self.theme_action_light)
        theme_menu.addAction(self.theme_action_dark)

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

        # Main splitter (left/right)
        main_splitter = QSplitter(Qt.Horizontal)

        # Left configuration panel (now with fixed buttons at bottom)
        self.config_panel = self._create_config_panel()
        main_splitter.addWidget(self.config_panel)

        # Right side: nested splitters for queue, log, summary/progress
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # Queue controls
        queue_ctrl = QHBoxLayout()
        self.add_files_btn = QPushButton("Add Files...")
        self.remove_sel_btn = QPushButton("Remove Selected")
        self.remove_all_btn = QPushButton("Remove All")
        self.show_cmd_btn = QPushButton("Show Command(s)")
        queue_ctrl.addWidget(self.add_files_btn)
        queue_ctrl.addWidget(self.remove_sel_btn)
        queue_ctrl.addWidget(self.remove_all_btn)
        queue_ctrl.addWidget(self.show_cmd_btn)
        queue_ctrl.addStretch()
        right_layout.addLayout(queue_ctrl)

        # Queue table (using QTableView + model)
        self.queue_table = DropTableView()
        self.queue_model = QueueTableModel(self)
        self.queue_table.setModel(self.queue_model)
        self.queue_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.queue_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.queue_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.queue_table.files_dropped.connect(self._on_files_dropped)
        # Enable vertical header (row numbers)
        self.queue_table.verticalHeader().setVisible(True)
        # Allow vertical resizing of rows
        self.queue_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)

        # --- Column sizing configuration ---
        header = self.queue_table.horizontalHeader()
        # Remove maximum width restriction so users can resize freely
        header.setMaximumSectionSize(16777215)  # effectively unlimited (Qt's default is large)
        # Set all columns to interactive resize mode
        for col in range(self.queue_model.columnCount()):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        # Set an initial width for the file column (index 0) to show paths
        self.queue_table.setColumnWidth(0, 350)
        # Set other columns to reasonable default widths
        self.queue_table.setColumnWidth(1, 120)  # Status
        self.queue_table.setColumnWidth(2, 80)   # Progress
        self.queue_table.setColumnWidth(3, 150)  # Date Added

        # Set custom delegate for progress column (col 2)
        self.progress_delegate = ProgressBarDelegate(self.queue_table)
        self.queue_table.setItemDelegateForColumn(2, self.progress_delegate)

        # Nested splitter: queue on top, log below, then summary/progress at bottom
        right_splitter = QSplitter(Qt.Orientation.Vertical)

        # Queue container
        queue_container = QWidget()
        queue_container_layout = QVBoxLayout(queue_container)
        queue_container_layout.setContentsMargins(0, 0, 0, 0)
        queue_container_layout.addWidget(self.queue_table)
        right_splitter.addWidget(queue_container)

        # Log container
        log_container = QWidget()
        log_layout = QVBoxLayout(log_container)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.addWidget(QLabel("General Log"))
        self.log_widget = QTextEdit()
        self.log_widget.setReadOnly(True)
        self.log_widget.setAcceptRichText(True)
        log_layout.addWidget(self.log_widget)
        right_splitter.addWidget(log_container)

        # Bottom part: live status, summary, progress, buttons
        bottom_container = QWidget()
        bottom_layout = QVBoxLayout(bottom_container)
        bottom_layout.setContentsMargins(0, 0, 0, 0)

        # Live status
        bottom_layout.addWidget(QLabel("Live Status"))
        self.live_status = QLabel("Idle")
        self.live_status.setFrameStyle(QLabel.Shape.StyledPanel)
        bottom_layout.addWidget(self.live_status)

        # Summary
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

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        bottom_layout.addWidget(self.progress_bar)

        # Action buttons
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

        # Set initial sizes for splitters
        right_splitter.setSizes([400, 200, 200])

        right_layout.addWidget(right_splitter)

        main_splitter.addWidget(right_widget)
        main_splitter.setSizes([450, 950])
        main_layout.addWidget(main_splitter)

    def _create_config_panel(self):
        """Build the left panel with fixed buttons at bottom."""
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(5, 5, 5, 5)

        # General settings group (application) - at top, outside scroll
        general_group = QGroupBox("General")
        general_form = QFormLayout(general_group)

        self.handbrake_edit = QLineEdit()
        self.handbrake_edit.setPlaceholderText("Path to HandBrakeCLI executable")
        self.handbrake_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.handbrake_edit.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        general_form.addRow("HandBrakeCLI:", self.handbrake_edit)

        # Buttons on second row, right aligned
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

        # Scroll area for settings groups
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        # Build the settings UI inside a container
        settings_container = QWidget()
        settings_layout = QVBoxLayout(settings_container)
        settings_layout.setContentsMargins(0, 0, 0, 0)

        builder = SettingsUIBuilder(ENCODING_SETTINGS, settings_container)
        settings_widget = builder.build()
        settings_layout.addWidget(settings_widget)

        scroll.setWidget(settings_container)
        layout.addWidget(scroll, 1)  # Give stretch

        # Buttons: Apply and Save Default (fixed at bottom)
        btn_layout = QHBoxLayout()
        self.apply_settings_btn = QPushButton("Apply Settings")
        self.save_default_btn = QPushButton("Save Current as Default")
        btn_layout.addWidget(self.apply_settings_btn)
        btn_layout.addWidget(self.save_default_btn)
        layout.addLayout(btn_layout)

        # Store binder for later use
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

        self.theme_action_light.triggered.connect(lambda: self._set_theme("light"))
        self.theme_action_dark.triggered.connect(lambda: self._set_theme("dark"))

        # Selection change in table
        selection_model = self.queue_table.selectionModel()
        selection_model.selectionChanged.connect(self._on_queue_selection_changed)

        self.apply_settings_btn.clicked.connect(self._on_apply_settings)
        self.save_default_btn.clicked.connect(self._on_save_default)

        # Connect model changes to summary updates
        self.queue_model.rowsInserted.connect(self._update_summary)
        self.queue_model.rowsRemoved.connect(self._update_summary)
        self.queue_model.dataChanged.connect(self._update_summary)

    def _on_apply_settings(self):
        """
        Apply current left-panel settings to selected queue items.
        Batching UI updates to prevent freezing for large selections.
        """
        selected_rows = set()
        for idx in self.queue_table.selectionModel().selectedRows():
            selected_rows.add(idx.row())
        if not selected_rows:
            return

        # Set wait cursor
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            # Disable sorting, updates, and block signals to prevent constant repaints
            sorting_enabled = self.queue_table.isSortingEnabled()
            self.queue_table.setSortingEnabled(False)
            self.queue_table.setUpdatesEnabled(False)
            self.queue_table.blockSignals(True)
            # Also block selection model signals to avoid extra processing
            sel_model = self.queue_table.selectionModel()
            if sel_model:
                sel_model.blockSignals(True)

            # Get new settings from the UI
            new_settings = self._settings_binder.get_encoding_settings()

            # Update model (internally emits a single dataChanged)
            self.queue_model.update_settings(list(selected_rows), new_settings)

            # Re-enable UI updates and signals
            self.queue_table.blockSignals(False)
            if sel_model:
                sel_model.blockSignals(False)
            self.queue_table.setUpdatesEnabled(True)
            self.queue_table.setSortingEnabled(sorting_enabled)
            # Force one final repaint of the viewport
            self.queue_table.viewport().update()

        finally:
            QApplication.restoreOverrideCursor()

    def _on_save_default(self):
        """Save current left-panel settings as default encoding settings."""
        settings = self._settings_binder.get_encoding_settings()
        self._default_encoding_settings = settings
        self._save_default_encoding_settings(settings)
        self.status_bar.showMessage("Default settings saved.")

    # ------------------------------------------------------------------
    # HandBrakeCLI handling
    # ------------------------------------------------------------------
    def _browse_handbrake(self):
        """Browse for HandBrakeCLI and validate."""
        file_filter = "Executable (*)" if sys.platform != "win32" else "Executable (*.exe)"
        path, _ = QFileDialog.getOpenFileName(self, "Select HandBrakeCLI", "", file_filter)
        if path:
            # Validate
            self.status_bar.showMessage("Validating HandBrakeCLI...")
            QApplication.processEvents()
            valid, msg = self._validate_handbrake(path)
            if valid:
                self.handbrake_edit.setText(path)
                self.status_bar.showMessage("HandBrakeCLI validated successfully.")
            else:
                QMessageBox.critical(self, "Invalid HandBrakeCLI", f"The selected file is not a valid HandBrakeCLI executable.\n\n{msg}")
                self.status_bar.showMessage("HandBrakeCLI validation failed.")

    def _validate_handbrake(self, path: str) -> Tuple[bool, str]:
        """
        Validate that the selected executable is HandBrakeCLI.

        Returns:
            (True, version_text)
            (False, error_message)
        """

        import os
        import sys
        import subprocess

        # -----------------------------
        # Basic checks
        # -----------------------------
        if not path:
            return False, "No file selected."

        if not os.path.isfile(path):
            return False, "File does not exist."

        if sys.platform == "win32":
            if not path.lower().endswith(".exe"):
                return False, "Please select an executable."

            # Fast rejection without launching random programs.
            #
            # If you later decide to support custom filenames,
            # simply remove this block.
            if os.path.basename(path).lower() != "handbrakecli.exe":
                return False, (
                    "The selected executable is not named "
                    "'HandBrakeCLI.exe'."
                )

        # -----------------------------
        # Hide console window on Windows
        # -----------------------------
        startupinfo = None
        creationflags = 0

        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            creationflags = subprocess.CREATE_NO_WINDOW

        # -----------------------------
        # Execute HandBrakeCLI
        # -----------------------------
        try:
            result = subprocess.run(
                [path, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                startupinfo=startupinfo,
                creationflags=creationflags,
            )

        except subprocess.TimeoutExpired:
            return False, (
                "HandBrakeCLI did not respond within 10 seconds."
            )

        except Exception as e:
            return False, str(e)

        output = (
            (result.stdout or "")
            + "\n"
            + (result.stderr or "")
        ).strip()

        # -----------------------------
        # Validate output
        # -----------------------------
        if result.returncode != 0:
            return False, output or "HandBrakeCLI returned an error."

        if "HandBrake" not in output:
            return False, (
                "The executable does not appear to be HandBrakeCLI."
            )

        # First line is usually:
        # HandBrake 1.11.2
        version_line = output.splitlines()[0].strip()

        return True, version_line

    def _show_handbrake_help(self):
        """Run HandBrakeCLI --help and show in a dialog (no console window)."""
        path = self.handbrake_edit.text().strip()
        if not path or not os.path.isfile(path):
            QMessageBox.warning(self, "Error", "Please set a valid HandBrakeCLI executable path.")
            return

        self.status_bar.showMessage("Fetching HandBrakeCLI help...")
        QApplication.processEvents()

        try:
            # Prevent a console window from appearing on Windows
            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NO_WINDOW

            proc = subprocess.run(
                [path, "--help"],
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=creationflags
            )

            if proc.returncode != 0:
                QMessageBox.warning(self, "Error", f"Failed to run --help (exit code {proc.returncode})")
                return

            help_text = proc.stdout
            if not help_text:
                help_text = proc.stderr
            if not help_text:
                help_text = "(No output from --help)"

            dlg = HandBrakeHelpDialog(help_text, self)
            dlg.exec()
            self.status_bar.showMessage("HandBrakeCLI help displayed.")

        except subprocess.TimeoutExpired:
            QMessageBox.critical(self, "Error", "Timeout while fetching help (process took too long).")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to run HandBrakeCLI: {str(e)}")

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
        # Filter duplicates
        existing_paths = self.queue_model.get_all_input_paths()
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
                if dlg.get_action() == "skip":
                    # Only add new_paths
                    pass
                else:
                    # Add all including duplicates
                    new_paths = file_paths
            else:
                return  # Cancel

        if not new_paths:
            return

        # Show settings dialog for new files
        dlg = SettingsDialog(self._default_encoding_settings, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            settings = dlg.get_settings()
            # Natural sort the new paths (according to filename)
            sorted_new_paths = natsorted(new_paths, key=lambda p: os.path.basename(p))
            # Create QueueItems with independent copy of settings
            items = []
            for path in sorted_new_paths:
                out = output_path(path)
                os.makedirs(os.path.dirname(out), exist_ok=True)
                # Deep copy settings per item
                item_settings = settings.copy()
                items.append(QueueItem(input_path=path, output_path=out, settings=item_settings))
            # Add to model
            self.queue_model.add_items(items)
            self._update_summary()

    def _update_summary(self):
        total = self.queue_model.rowCount()
        success = self.queue_model.count_by_status(QueueStatus.SUCCESS)
        failed = self.queue_model.count_by_status(QueueStatus.FAILED)
        skipped = self.queue_model.count_by_status(QueueStatus.SKIPPED)
        terminated = self.queue_model.count_by_status(QueueStatus.TERMINATED)
        self.summary_total.setText(f"Total: {total}")
        self.summary_success.setText(f"Successful: {success}")
        self.summary_failed.setText(f"Failed: {failed}")
        self.summary_skipped.setText(f"Skipped: {skipped}")
        self.summary_terminated.setText(f"Terminated: {terminated}")

    def _remove_selected(self):
        selected_rows = [idx.row() for idx in self.queue_table.selectionModel().selectedRows()]
        if not selected_rows:
            return
        # Check for encoding rows
        encoding_rows = set(self.queue_model.get_encoding_rows())
        selected_set = set(selected_rows)
        if selected_set & encoding_rows:
            QMessageBox.warning(self, "Cannot Remove", "Cannot remove rows that are currently encoding.")
            return
        # Remove all selected rows (they are not encoding)
        selected_rows.sort(reverse=True)
        self.queue_model.remove_rows(selected_rows)
        self._update_summary()

    def _remove_all(self):
        # Remove all rows except encoding
        encoding_rows = set(self.queue_model.get_encoding_rows())
        all_rows = set(range(self.queue_model.rowCount()))
        to_remove = all_rows - encoding_rows
        if not to_remove:
            QMessageBox.information(self, "No Removable Rows", "No non-encoding rows to remove.")
            return
        # If encoding rows exist, warn
        if encoding_rows:
            reply = QMessageBox.question(
                self, "Remove All",
                f"Some rows are currently encoding and will be kept. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        to_remove = sorted(to_remove, reverse=True)
        self.queue_model.remove_rows(list(to_remove))
        self._update_summary()

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
            item = self.queue_model.get_item(row)
            if item:
                cmd = HandBrakeCommandBuilder.build(item.settings, hb_path,
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
            item = self.queue_model.get_item(row)
            if item:
                self._settings_binder.load_values(item.settings)
        else:
            self._load_defaults_into_panel()

    def _load_defaults_into_panel(self):
        self._settings_binder.load_values(self._default_encoding_settings)

    # ------------------------------------------------------------------
    # Theme management
    # ------------------------------------------------------------------
    def _set_theme(self, theme_name: str):
        if theme_name == "dark":
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
        self._app_settings.theme = theme_name
        self._app_settings.save(self._qsettings)
        self._rebuild_log()

    def _restore_theme(self):
        saved_theme = self._app_settings.theme
        if saved_theme not in ("light", "dark"):
            saved_theme = "light"
        self._set_theme(saved_theme)

    # ------------------------------------------------------------------
    # Encoding controls
    # ------------------------------------------------------------------
    def start_encoding(self):
        hb_path = self.handbrake_edit.text().strip()
        if not hb_path or not os.path.isfile(hb_path):
            QMessageBox.warning(self, "Error", "Please set a valid HandBrakeCLI executable path.")
            return

        # Reset terminated items to pending (they will be retried)
        for i in range(self.queue_model.rowCount()):
            item = self.queue_model.get_item(i)
            if item and item.status == QueueStatus.TERMINATED:
                self.queue_model.update_item_status(i, QueueStatus.PENDING)

        # Check if there are pending items
        pending = self.queue_model.get_pending_rows()
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

        # self.handbrake_edit.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.progress_bar.setValue(0)
        self._start_time = datetime.now()
        self._elapsed_timer.start(1000)

        self._log_entries.clear()
        self.log_widget.clear()
        self._log_buffer.clear()
        self._log_append(f"Encoding started at {self._start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        self.live_status.setText("Running...")

        # Reset progress for pending items
        for row in pending:
            self.queue_model.update_item_progress(row, 0)

        self._update_summary()

        # Create worker and thread
        self._worker_thread = QThread()
        self._worker = EncodingWorker(hb_path)
        self._worker.moveToThread(self._worker_thread)

        # Connect worker signals to main thread slots
        self.process_item_signal.connect(self._worker.process_item)
        self._worker.progress_signal.connect(self._on_worker_progress)
        self._worker.status_signal.connect(self._on_worker_status)
        self._worker.file_started.connect(self._on_worker_file_started)
        self._worker.file_finished.connect(self._on_worker_file_finished)
        self._worker.log_signal.connect(self._log_append)
        self._worker.log_progress_signal.connect(self._update_live_status)

        # Start thread
        self._worker_thread.start()

        # Send first item
        self._send_next_item()

    def _send_next_item(self):
        """Find the next pending row and send it to the worker."""
        if not self._encoding_active or self._worker is None:
            return
        if self._stop_after_current:
            # Stop requested: do not send more items
            self._finish_encoding()
            return

        total = self.queue_model.rowCount()
        for row in range(total):
            item = self.queue_model.get_item(row)
            if item and item.status == QueueStatus.PENDING:
                # Create a copy of the item to avoid shared state
                item_copy = QueueItem(
                    input_path=item.input_path,
                    output_path=item.output_path,
                    settings=item.settings.copy(),
                    status=item.status,
                    progress=item.progress,
                    date_added=item.date_added
                )
                # Emit signal to start processing in worker thread
                self.process_item_signal.emit(row, item_copy)
                return

        # No pending items found
        self._finish_encoding()

    def _on_worker_progress(self, row: int, percent: int):
        """Update model with progress from worker."""
        self.queue_model.update_item_progress(row, percent)

    def _on_worker_status(self, row: int, status: QueueStatus):
        """Update model with status from worker."""
        self.queue_model.update_item_status(row, status)
        self._update_summary()

    def _on_worker_file_started(self, row: int, output_path: str):
        """Handle file started."""
        self.status_bar.showMessage(f"Encoding: {os.path.basename(output_path)}")
        self.queue_model.update_item_status(row, QueueStatus.ENCODING)

    def _on_worker_file_finished(self, row: int, success: bool, exit_code: int):
        """Handle file finished. Send next item."""
        self._update_summary()
        # Update overall progress
        total = self.queue_model.rowCount()
        if total > 0:
            processed = 0
            for i in range(total):
                item = self.queue_model.get_item(i)
                if item and item.status in (QueueStatus.SUCCESS, QueueStatus.FAILED, QueueStatus.SKIPPED, QueueStatus.TERMINATED):
                    processed += 1
            overall = int(processed / total * 100)
            self.progress_bar.setValue(overall)
        # Send next item
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
            self._stop_after_current = True  # prevent further items
            self.status_bar.showMessage("Terminating...")

    def _finish_encoding(self):
        """Clean up after encoding finishes or stops."""
        if not self._encoding_active:
            return
        self._encoding_active = False
        self._stop_after_current = False

        # Stop worker thread
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
        """Flush pending log entries to the widget in one batch."""
        if not self._log_buffer:
            return
        theme = self._app_settings.theme
        parts = []
        for entry in self._log_buffer:
            color = LogRenderer._get_color(entry.role, theme)
            parts.append(f'<span style="color:{color};">{entry.text}</span>')
        html = "<br>".join(parts) + "<br>"
        cursor = self.log_widget.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertHtml(html)
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.log_widget.setTextCursor(cursor)
        self.log_widget.ensureCursorVisible()
        self._log_buffer.clear()

    def _render_log(self):
        """Full rebuild (used on theme change)."""
        self._flush_logs()
        theme = self._app_settings.theme
        html = LogRenderer.render(self._log_entries, theme)
        self.log_widget.setHtml(html)
        cursor = self.log_widget.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.log_widget.setTextCursor(cursor)
        self.log_widget.ensureCursorVisible()

    def _rebuild_log(self):
        self._render_log()

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
        # Stop worker if running
        if self._encoding_active and self._worker:
            self._worker.terminate()
            self._worker_thread.quit()
            self._worker_thread.wait()
        event.accept()


# =============================================================================
# Entry point
# =============================================================================

def main():
    app = QApplication(sys.argv)
    app.setOrganizationName("BatchEncoder")
    app.setApplicationName("BatchEncoderApp")
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
