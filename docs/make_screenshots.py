"""Regenerate the README screenshots from the real widgets, using fake chat data.

    .venv\\Scripts\\python docs\\make_screenshots.py

Settings are isolated in a temp dir, so no personal config or API keys can appear in the images.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from chat_translator import config  # noqa: E402

config.CONFIG_DIR = Path(tempfile.mkdtemp())
config.CONFIG_PATH = config.CONFIG_DIR / "config.json"

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from chat_translator import worker  # noqa: E402

worker.Worker.start = lambda self: None  # no OCR models / network needed for screenshots

from chat_translator.app import ControlWindow, FeedPanel  # noqa: E402
from chat_translator.i18n import tr  # noqa: E402

OUT = ROOT / "docs" / "images"
FONT = "Microsoft YaHei UI"

SCENES = {
    "en": {
        "channel": "Local [38]",
        "chat": [
            ("12:01:05", "LaoWang", "#e0a040", "隔壁有敌人，大鱼被抓了"),
            ("12:01:09", "Bob Kerman", "#7fb0ff", "anyone selling plex"),
            ("12:01:15", "XiaoLi", "#e0a040", "我们有缪宁和狂热，贴泰坦，过桥"),
            ("12:01:30", "LaoWang", "#e0a040", "小白在泡泡里，挑我"),
        ],
        "entries": [
            ("[12:01:05] LaoWang > 隔壁有敌人，大鱼被抓了", "[12:01:05] LaoWang > Enemies in next system, Rorqual was caught", False),
            ("[12:01:15] XiaoLi > 我们有缪宁和狂热，贴泰坦，过桥",
             "[12:01:15] XiaoLi > We have Muninn and Zealot, keep at range from the titan(bridger), and jump", False),
            ("[12:01:30] LaoWang > 小白在泡泡里，挑我", "[12:01:30] LaoWang > Astero is in the interdiction bubble, warp to me", False),
            ("Thanks FC, warp to me, I have a Muninn", "谢谢FC，挑我，我有缪宁", True),
        ],
        "reply_typing": "o7 see you at the gate",
        "notes": ["1  Drag a box around the chat", "2  Translations appear here",
                  "3  Type a reply + Enter: translation is copied, paste it in chat"],
    },
    "zh": {
        "channel": "本地 [38]",
        "chat": [
            ("12:01:09", "Bob Kerman", "#7fb0ff", "anyone selling plex"),
            ("12:01:12", "XiaoLi", "#e0a040", "is the fleet still going?"),
            ("12:01:20", "Ann Reyes", "#7fb0ff", "hostiles in next system, dock up"),
            ("12:01:26", "LaoWang", "#e0a040", "收到"),
        ],
        "entries": [
            ("[12:01:09] Bob Kerman > anyone selling plex", "[12:01:09] Bob Kerman > 有人卖 plex 吗", False),
            ("[12:01:12] XiaoLi > is the fleet still going?", "[12:01:12] XiaoLi > 舰队还在吗？", False),
            ("[12:01:20] Ann Reyes > hostiles in next system, dock up", "[12:01:20] Ann Reyes > 隔壁有敌人，进站", False),
            ("隔壁有敌人，大家进站", "Enemies in next system, everyone dock", True),
        ],
        "reply_typing": "谢谢，马上到",
        "notes": ["1  拖动鼠标框选聊天窗口", "2  译文显示在这里", "3  输入回复并回车：译文自动复制，粘贴到聊天即可"],
    },
}


def fake_chat(scene: dict) -> QPixmap:
    rows = "".join(
        f"<div style='margin:2px 0'><span style='color:#8a8f98'>[{t}]</span> "
        f"<span style='color:{c}'>{n}</span> <span style='color:#8a8f98'>&gt;</span> {msg}</div>"
        for t, n, c, msg in scene["chat"])
    label = QLabel(f"<div style='color:#9aa4b2; font-size:11px; margin-bottom:6px'>{scene['channel']}</div>{rows}")
    label.setStyleSheet(f"background:#0f1115; color:#d0d0d0; font: 13px '{FONT}'; padding:8px;"
                        "border:1px solid #2a2f38;")
    label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
    label.resize(470, 140)
    return label.grab()


def badge(p: QPainter, pos: QPoint, number: str):
    p.setBrush(QColor("#ffb020"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(pos, 13, 13)
    p.setPen(QColor("#111"))
    p.setFont(QFont(FONT, 11, QFont.Weight.Bold))
    p.drawText(QRect(pos.x() - 13, pos.y() - 13, 26, 26), Qt.AlignmentFlag.AlignCenter, number)


def overview(lang: str, panel: FeedPanel) -> QPixmap:
    scene = SCENES[lang]
    panel.cfg["language"] = lang
    panel.retranslate()
    panel.entries = list(scene["entries"])
    panel.render_entries()
    panel.reply.setText(scene["reply_typing"])
    panel.resize(560, 330)
    panel_pix = panel.grab()

    chat = fake_chat(scene)
    W, H = 1180, 520
    canvas = QPixmap(W, H)
    canvas.fill(QColor("#05070a"))
    p = QPainter(canvas)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Space-ish backdrop so it reads as "the game" behind the overlay.
    grad_path = QPainterPath()
    grad_path.addEllipse(QRectF(700, -200, 700, 600))
    p.fillPath(grad_path, QBrush(QColor(40, 60, 100, 60)))
    p.setPen(QColor(255, 255, 255, 90))
    for i in range(70):
        p.drawPoint((i * 97) % W, (i * 53 + i * i) % H)

    chat_pos = QPoint(40, 70)
    p.drawPixmap(chat_pos, chat)
    region = QRect(chat_pos, chat.size()).adjusted(-4, -4, 4, 4)
    p.setPen(QPen(QColor(0, 200, 255, 220), 2, Qt.PenStyle.DashLine))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRect(region)

    panel_pos = QPoint(580, 120)
    p.drawPixmap(panel_pos, panel_pix)

    # Arrow from the region to the panel.
    p.setPen(QPen(QColor("#ffb020"), 3))
    a, b = QPointF(region.right() + 8, region.center().y()), QPointF(panel_pos.x() - 10, panel_pos.y() + 80)
    p.drawLine(a, b)
    p.setBrush(QColor("#ffb020"))
    arrow = QPainterPath(b)
    arrow.lineTo(b + QPointF(-14, -12))
    arrow.lineTo(b + QPointF(-16, 6))
    arrow.closeSubpath()
    p.drawPath(arrow)

    badge(p, QPoint(region.left() + 4, region.top() - 22), "1")
    badge(p, QPoint(panel_pos.x() + panel_pix.width() - 20, panel_pos.y() + 14), "2")
    badge(p, QPoint(panel_pos.x() + panel_pix.width() - 20, panel_pos.y() + panel_pix.height() - 22), "3")

    p.setFont(QFont(FONT, 12))
    p.setPen(QColor("#e8e8e8"))
    for i, note in enumerate(scene["notes"]):
        p.drawText(40, 280 + i * 30, note)
    p.end()
    return canvas


def main():
    app = QApplication([])
    OUT.mkdir(parents=True, exist_ok=True)
    win = ControlWindow()
    win.region = QRect(40, 70, 470, 140)
    for lang in ("en", "zh"):
        win.language.setCurrentIndex(win.language.findData(lang))
        win.btn_run.blockSignals(True)
        win.btn_run.setChecked(True)
        win.btn_run.blockSignals(False)
        win.retranslate()
        win.set_status(tr(lang, "watching"))
        win.resize(470, win.sizeHint().height())
        win.show()
        app.processEvents()
        win.grab().save(str(OUT / f"settings_{lang}.png"))
        overview(lang, win.panel).save(str(OUT / f"overview_{lang}.png"))
    print("wrote", sorted(p.name for p in OUT.glob("*.png")))


if __name__ == "__main__":
    main()
