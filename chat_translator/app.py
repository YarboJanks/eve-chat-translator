import ctypes
import html
import sys
import threading

import numpy as np
from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSizeGrip, QSlider, QSpinBox, QTextBrowser, QVBoxLayout, QWidget,
)

from . import config
from .i18n import LANGUAGES, tr
from .translate import BACKENDS
from .worker import Worker

WDA_NONE = 0x00
WDA_EXCLUDEFROMCAPTURE = 0x11


def set_capture_hidden(widget: QWidget, hidden: bool) -> None:
    """Hide/show one of our windows in ALL screen capture (screenshots, recording, streaming, and our own OCR)."""
    try:
        ctypes.windll.user32.SetWindowDisplayAffinity(
            int(widget.winId()), WDA_EXCLUDEFROMCAPTURE if hidden else WDA_NONE)
    except (AttributeError, OSError):
        pass


def mask_rect(frame: np.ndarray, region: QRect, cover: QRect) -> None:
    """Black out the part of a captured region that `cover` (one of our windows) sits on,
    so our own translations are never OCR'd back in."""
    overlap = region.intersected(cover)
    if overlap.isEmpty():
        return
    sx, sy = frame.shape[1] / region.width(), frame.shape[0] / region.height()  # logical -> pixels
    x0, y0 = int((overlap.left() - region.left()) * sx), int((overlap.top() - region.top()) * sy)
    x1, y1 = int((overlap.right() + 1 - region.left()) * sx), int((overlap.bottom() + 1 - region.top()) * sy)
    frame[y0:y1, x0:x1] = 0


def grab_region(rect: QRect) -> np.ndarray | None:
    """Capture a global logical rect as a BGR array."""
    screen = QGuiApplication.screenAt(rect.center())
    if screen is None:
        return None
    geo = screen.geometry()
    # QScreen.grabWindow(0, ...) takes coordinates relative to that screen.
    pix = screen.grabWindow(0, rect.x() - geo.x(), rect.y() - geo.y(), rect.width(), rect.height())
    img = pix.toImage().convertToFormat(QImage.Format.Format_RGB32)
    w, h = img.width(), img.height()
    if w == 0 or h == 0:
        return None
    buf = np.frombuffer(img.constBits(), np.uint8).reshape(h, img.bytesPerLine() // 4, 4)
    return np.ascontiguousarray(buf[:, :w, :3])  # BGRA in memory -> BGR


class RegionSelector(QWidget):
    """Full-screen dimmed overlay on one screen; drag to select a rectangle."""

    selected = Signal(QRect)
    cancelled = Signal()

    def __init__(self, screen, hint: str):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setGeometry(screen.geometry())
        self.hint = hint
        self.origin: QPoint | None = None
        self.current: QPoint | None = None

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 90))
        if self.origin and self.current:
            r = QRect(self.origin, self.current).normalized()
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            p.fillRect(r, Qt.GlobalColor.transparent)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            p.setPen(QPen(QColor(0, 200, 255), 2))
            p.drawRect(r)
        else:
            p.setPen(QColor(255, 255, 255))
            p.setFont(QFont("Microsoft YaHei UI", 16))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.hint)

    def mousePressEvent(self, e):
        self.origin = self.current = e.position().toPoint()
        self.update()

    def mouseMoveEvent(self, e):
        self.current = e.position().toPoint()
        self.update()

    def mouseReleaseEvent(self, e):
        r = QRect(self.origin, e.position().toPoint()).normalized()
        if r.width() > 10 and r.height() > 10:
            self.selected.emit(r.translated(self.geometry().topLeft()))
        else:
            self.origin = self.current = None
            self.update()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()


class RegionOutline(QWidget):
    """Thin click-through border marking the watched region."""

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

    def set_region(self, r: QRect):
        self.setGeometry(r.adjusted(-3, -3, 3, 3))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setPen(QPen(QColor(0, 200, 255, 180), 2, Qt.PenStyle.DashLine))
        p.drawRect(self.rect().adjusted(1, 1, -2, -2))

class FeedPanel(QWidget):
    """Always-on-top translucent panel listing translated messages, with a reply box at the bottom."""

    reply_requested = Signal(str)

    def __init__(self, cfg: dict):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.cfg = cfg
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._drag: QPoint | None = None

        self.header = QLabel()
        self.header.setStyleSheet("color:#9ad; padding:4px 8px; font: 9pt 'Microsoft YaHei UI';")
        self.header.setCursor(Qt.CursorShape.SizeAllCursor)

        self.text = QTextBrowser()
        self.text.setOpenLinks(False)
        self.text.setFrameShape(QTextBrowser.Shape.NoFrame)
        self.text.setStyleSheet("background: transparent; color: white;")

        self.reply = QLineEdit()
        self.reply.setStyleSheet("background: rgba(255,255,255,20); color: white; border: 1px solid #456;"
                                 "border-radius: 4px; padding: 4px 6px;")
        self.reply.returnPressed.connect(self._submit_reply)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(4, 4, 0, 0)
        bottom.addWidget(self.reply)
        bottom.addWidget(QSizeGrip(self), 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 0, 4, 4)
        lay.setSpacing(0)
        lay.addWidget(self.header)
        lay.addWidget(self.text)
        lay.addLayout(bottom)

        self.entries: list[tuple[str, str, bool]] = []  # (original, translation, is_own_reply)
        g = cfg.get("panel_geometry")
        self.setGeometry(*(g or (100, 100, 520, 340)))
        self.retranslate()
        self.apply_style()

    def retranslate(self):
        lang = self.cfg["language"]
        self.header.setText(tr(lang, "panel_title"))
        self.reply.setPlaceholderText(tr(lang, "reply_placeholder"))

    def apply_style(self):
        font = QFont("Microsoft YaHei UI", int(self.cfg["font_size"]))
        self.text.setFont(font)
        self.reply.setFont(font)
        self.update()
        self.render_entries()

    def _submit_reply(self):
        text = self.reply.text().strip()
        if text:
            self.reply.clear()
            self.reply_requested.emit(text)

    def add(self, items: list[tuple[str, str]]):
        self.entries.extend((src, dst, False) for src, dst in items)
        self._trim_and_render()

    def add_reply(self, src: str, dst: str):
        self.entries.append((src, dst, True))
        self._trim_and_render()

    def _trim_and_render(self):
        del self.entries[: -int(self.cfg["max_messages"])]
        self.render_entries()

    def clear(self):
        self.entries.clear()
        self.render_entries()

    def render_entries(self):
        small = max(8, int(self.cfg["font_size"]) - 4)
        parts = []
        for src, dst, own in self.entries:
            if own:  # the user's reply: show the text to paste prominently
                block = (f"<div style='margin-bottom:6px'><span style='color:#7fd4ff'>➜ {html.escape(dst)}</span>"
                         f"<br><span style='color:#99a; font-size:{small}pt'>{html.escape(src)}</span></div>")
            else:
                block = f"<div style='margin-bottom:6px'><span>{html.escape(dst)}</span>"
                if self.cfg["show_original"]:
                    block += f"<br><span style='color:#99a; font-size:{small}pt'>{html.escape(src)}</span>"
                block += "</div>"
            parts.append(block)
        self.text.setHtml("".join(parts))
        sb = self.text.verticalScrollBar()
        sb.setValue(sb.maximum())

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(15, 17, 22, int(255 * float(self.cfg["opacity"]))))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(self.rect(), 8, 8)

    def mousePressEvent(self, e):
        if self.header.geometry().contains(e.position().toPoint()):
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None

    def showEvent(self, e):
        super().showEvent(e)
        set_capture_hidden(self, bool(self.cfg["hide_from_capture"]))


class ControlWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.cfg = config.load()

        self.panel = FeedPanel(self.cfg)
        self.outline = RegionOutline()
        self.selectors: list[RegionSelector] = []
        self.region: QRect | None = QRect(*self.cfg["region"]) if self.cfg["region"] else None

        self.worker = Worker(self.cfg)
        self.worker.messages.connect(self.panel.add)
        self.worker.reply_ready.connect(self.on_reply_ready)
        self.worker.status.connect(self.set_status)
        self.worker.start()
        self.panel.reply_requested.connect(
            lambda text: threading.Thread(target=self.worker.reply, args=(text,), daemon=True).start())

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)

        self._build_ui()
        self.panel.show()
        self.update_outline()

    def t(self, key: str, **kw) -> str:
        return tr(self.cfg["language"], key, **kw)

    def _build_ui(self):
        self.language = QComboBox()
        for code, name in LANGUAGES:
            self.language.addItem(name, code)
        self.language.setCurrentIndex(max(0, self.language.findData(self.cfg["language"])))
        self.language.currentIndexChanged.connect(self.on_language)
        self.language_label = QLabel()
        lang_row = QHBoxLayout()
        lang_row.addWidget(self.language_label)
        lang_row.addWidget(self.language, 1)

        self.btn_region = QPushButton()
        self.btn_region.clicked.connect(self.select_region)
        self.btn_run = QPushButton()
        self.btn_run.setCheckable(True)
        self.btn_run.toggled.connect(self.toggle_run)
        self.btn_clear = QPushButton()
        self.btn_clear.clicked.connect(self.clear)
        self.btn_glossary = QPushButton()
        self.btn_glossary.clicked.connect(
            lambda: threading.Thread(target=self.worker.update_glossary, daemon=True).start())

        buttons = QHBoxLayout()
        for b in (self.btn_region, self.btn_run, self.btn_clear, self.btn_glossary):
            buttons.addWidget(b)

        self.backend = QComboBox()
        for code in BACKENDS:
            self.backend.addItem("", code)
        self.backend.setCurrentIndex(max(0, self.backend.findData(self.cfg["backend"])))
        self.backend.currentIndexChanged.connect(self.on_settings)

        self.api_key = QLineEdit(self.cfg["anthropic_api_key"])
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.editingFinished.connect(self.on_settings)
        self.baidu_appid = QLineEdit(self.cfg["baidu_appid"])
        self.baidu_appid.editingFinished.connect(self.on_settings)
        self.baidu_key = QLineEdit(self.cfg["baidu_key"])
        self.baidu_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.baidu_key.editingFinished.connect(self.on_settings)

        self.interval = QSpinBox()
        self.interval.setRange(250, 10000)
        self.interval.setSingleStep(250)
        self.interval.setSuffix(" ms")
        self.interval.setValue(int(self.cfg["interval_ms"]))
        self.interval.valueChanged.connect(self.on_settings)

        self.upscale = QComboBox()
        for s in ("1.0", "1.5", "2.0"):
            self.upscale.addItem(f"{s}×", float(s))
        self.upscale.setCurrentIndex(max(0, self.upscale.findData(float(self.cfg["upscale"]))))
        self.upscale.currentIndexChanged.connect(self.on_settings)

        self.font_size = QSpinBox()
        self.font_size.setRange(8, 32)
        self.font_size.setValue(int(self.cfg["font_size"]))
        self.font_size.valueChanged.connect(self.on_settings)

        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(0, 100)
        self.opacity.setValue(int(float(self.cfg["opacity"]) * 100))
        self.opacity.valueChanged.connect(self.on_settings)

        self.show_original = QCheckBox()
        self.show_original.setChecked(self.cfg["show_original"])
        self.show_original.toggled.connect(self.on_settings)
        self.translate_all = QCheckBox()
        self.translate_all.setChecked(self.cfg["translate_all"])
        self.translate_all.toggled.connect(self.on_settings)
        self.show_outline = QCheckBox()
        self.show_outline.setChecked(self.cfg["show_outline"])
        self.show_outline.toggled.connect(self.on_settings)
        self.hide_from_capture = QCheckBox()
        self.hide_from_capture.setChecked(self.cfg["hide_from_capture"])
        self.hide_from_capture.toggled.connect(self.on_settings)

        self.form = QFormLayout()
        self.form_rows = {}  # i18n key -> (label, field)
        for key, field in (("translator", self.backend), ("api_key", self.api_key),
                           ("baidu_appid", self.baidu_appid), ("baidu_key", self.baidu_key),
                           ("interval", self.interval), ("upscale", self.upscale),
                           ("font_size", self.font_size), ("opacity", self.opacity)):
            label = QLabel()
            self.form.addRow(label, field)
            self.form_rows[key] = (label, field)

        self.status = QLabel()
        self.status.setStyleSheet("color:#666")
        self.status.setWordWrap(True)
        self.region_label = QLabel()

        lay = QVBoxLayout(self)
        lay.addLayout(lang_row)
        lay.addLayout(buttons)
        lay.addWidget(self.region_label)
        lay.addLayout(self.form)
        for w in (self.show_original, self.translate_all, self.show_outline, self.hide_from_capture, self.status):
            lay.addWidget(w)
        self.retranslate()
        self.set_status(self.t("starting"))
        self.on_settings()

    def retranslate(self):
        self.setWindowTitle(self.t("window_title"))
        self.language_label.setText(self.t("language"))
        self.btn_region.setText(self.t("select_region"))
        self.btn_run.setText(self.t("pause") if self.btn_run.isChecked() else self.t("start"))
        self.btn_clear.setText(self.t("clear"))
        self.btn_glossary.setText(self.t("update_glossary"))
        self.btn_glossary.setToolTip(self.t("update_glossary_tip"))
        for i, code in enumerate(BACKENDS):
            self.backend.setItemText(i, self.t(f"backend_{code}"))
        self.api_key.setPlaceholderText(self.t("api_key_placeholder"))
        self.baidu_key.setPlaceholderText(self.t("baidu_key_placeholder"))
        self.upscale.setToolTip(self.t("upscale_tip"))
        for key, (label, _) in self.form_rows.items():
            label.setText(self.t(key))
        self.show_original.setText(self.t("show_original"))
        self.translate_all.setText(self.t("translate_all"))
        self.show_outline.setText(self.t("show_outline"))
        self.hide_from_capture.setText(self.t("hide_from_capture"))
        self.hide_from_capture.setToolTip(self.t("hide_from_capture_tip"))
        self.update_region_label()
        self.panel.retranslate()

    def on_language(self, *_):
        self.cfg["language"] = self.language.currentData()
        self.retranslate()
        self.set_status(self.t("watching") if self.btn_run.isChecked() else self.t("ready"))
        self.worker.reset()  # re-read the visible chat in the new direction
        self.save()

    def on_settings(self, *_):
        self.cfg["backend"] = self.backend.currentData()
        self.cfg["anthropic_api_key"] = self.api_key.text().strip()
        self.cfg["baidu_appid"] = self.baidu_appid.text().strip()
        self.cfg["baidu_key"] = self.baidu_key.text().strip()
        # Only show the credential rows for the selected translator.
        for key, backend in (("api_key", "claude"), ("baidu_appid", "baidu"), ("baidu_key", "baidu")):
            self.form.setRowVisible(self.form_rows[key][1], self.cfg["backend"] == backend)
        self.cfg["interval_ms"] = self.interval.value()
        self.cfg["upscale"] = self.upscale.currentData()
        self.cfg["font_size"] = self.font_size.value()
        self.cfg["opacity"] = self.opacity.value() / 100
        self.cfg["show_original"] = self.show_original.isChecked()
        self.cfg["translate_all"] = self.translate_all.isChecked()
        self.cfg["show_outline"] = self.show_outline.isChecked()
        self.cfg["hide_from_capture"] = self.hide_from_capture.isChecked()
        set_capture_hidden(self.panel, self.cfg["hide_from_capture"])
        self.timer.setInterval(self.cfg["interval_ms"])
        self.panel.apply_style()
        self.update_outline()
        self.save()

    def save(self):
        r = self.region
        self.cfg["region"] = [r.x(), r.y(), r.width(), r.height()] if r else None
        g = self.panel.geometry()
        self.cfg["panel_geometry"] = [g.x(), g.y(), g.width(), g.height()]
        config.save(self.cfg)

    def set_status(self, s: str):
        self.status.setText(s)

    def on_reply_ready(self, src: str, dst: str):
        self.panel.add_reply(src, dst)
        QGuiApplication.clipboard().setText(dst)
        self.set_status(self.t("copied"))

    def update_region_label(self):
        r = self.region
        self.region_label.setText(self.t("region", w=r.width(), h=r.height(), x=r.x(), y=r.y()) if r
                                  else self.t("region_none"))

    def update_outline(self):
        if self.region and self.cfg["show_outline"]:
            self.outline.set_region(self.region)
            self.outline.show()
        else:
            self.outline.hide()

    def select_region(self):
        self.outline.hide()
        self.selectors = [RegionSelector(s, self.t("select_hint")) for s in QGuiApplication.screens()]
        for sel in self.selectors:
            sel.selected.connect(self.on_region)
            sel.cancelled.connect(self.close_selectors)
            sel.show()
            sel.activateWindow()

    def close_selectors(self):
        for sel in self.selectors:
            sel.close()
        self.selectors = []
        self.update_outline()

    def on_region(self, r: QRect):
        self.region = r
        self.worker.reset()
        self.close_selectors()
        self.update_region_label()
        self.save()
        if not self.btn_run.isChecked():
            self.btn_run.setChecked(True)

    def toggle_run(self, on: bool):
        if on and not self.region:
            self.btn_run.setChecked(False)
            self.select_region()
            return
        self.btn_run.setText(self.t("pause") if on else self.t("start"))
        if on:
            self.timer.start(self.cfg["interval_ms"])
            self.set_status(self.t("watching"))
        else:
            self.timer.stop()
            self.set_status(self.t("paused"))

    def tick(self):
        if self.region:
            frame = grab_region(self.region)
            if frame is not None and not self.cfg["hide_from_capture"] and self.panel.isVisible():
                mask_rect(frame, self.region, self.panel.frameGeometry())
            if frame is not None:
                self.worker.submit(frame)

    def clear(self):
        self.panel.clear()
        self.worker.reset()

    def closeEvent(self, e):
        self.save()
        self.timer.stop()
        self.worker.stop()
        self.panel.close()
        self.outline.close()
        QApplication.quit()


def main():
    if sys.platform == "win32":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ChatTranslator")
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    win = ControlWindow()
    win.resize(460, 0)
    win.show()
    sys.exit(app.exec())
