import threading
import traceback
from collections import deque
from difflib import SequenceMatcher

import numpy as np
from PySide6.QtCore import QThread, Signal

from .glossary import SHEET_PATH, Glossary
from .i18n import other, tr
from .translate import BaiduBackend, ClaudeBackend, GoogleBackend, Translator, _norm, needs_translation


class SeenLines:
    """Remembers recently shown lines, fuzzily, because OCR of the same line jitters between frames."""

    def __init__(self, size: int = 600, threshold: float = 0.85):
        self.keys: deque[str] = deque(maxlen=size)
        self.threshold = threshold

    def is_new(self, line: str) -> bool:
        key = _norm(line)
        for seen in self.keys:
            if key == seen or (len(key) >= 4 and key in seen):  # partial line cut off at the region edge
                return False
            if SequenceMatcher(None, key, seen).quick_ratio() >= self.threshold and \
                    SequenceMatcher(None, key, seen).ratio() >= self.threshold:
                return False
        return True

    def add(self, line: str) -> None:
        self.keys.append(_norm(line))

    def clear(self) -> None:
        self.keys.clear()


class Worker(QThread):
    """Processes only the most recent frame; frames that arrive while busy are dropped."""

    messages = Signal(list)  # list[tuple[str original, str translation]]
    reply_ready = Signal(str, str)  # (what the user typed, translation to paste)
    status = Signal(str)

    def __init__(self, cfg: dict):
        super().__init__()
        self.cfg = cfg
        self._frame: np.ndarray | None = None
        self._cond = threading.Condition()
        self._stop = False
        self._last_small: np.ndarray | None = None
        self.seen = SeenLines()
        self.context: deque[str] = deque(maxlen=10)
        self.translator: Translator | None = None
        self._backend_key = None
        self.glossary = Glossary()

    def submit(self, frame: np.ndarray) -> None:
        with self._cond:
            self._frame = frame
            self._cond.notify()

    def reset(self) -> None:
        self.seen.clear()
        self.context.clear()
        self._last_small = None

    def _status(self, key: str, **kw) -> None:
        self.status.emit(tr(self.cfg["language"], key, **kw))

    def reply(self, text: str) -> None:
        """Translate the user's own message into the other language (network; call off the GUI thread)."""
        try:
            self._status("translating_reply")
            self.reply_ready.emit(text, self._get_translator().reply(text, other(self.cfg["language"])))
        except Exception as e:
            traceback.print_exc()
            self._status("error", e=e)

    def update_glossary(self) -> None:
        """Re-download the slang sheet (network; call off the GUI thread) and drop cached translations."""
        try:
            n = Glossary.update_sheet()
            self.glossary.load()
            self.translator = None
            self._status("glossary_updated", n=n, terms=len(self.glossary.hints))
        except Exception as e:
            self._status("glossary_failed", e=e)

    def stop(self) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify()
        self.wait(5000)

    def _get_translator(self) -> Translator:
        key = (self.cfg["backend"], self.cfg["claude_model"], self.cfg["anthropic_api_key"],
               self.cfg["baidu_appid"], self.cfg["baidu_key"])
        if self.translator is None or key != self._backend_key:
            if self.cfg["backend"] == "claude":
                backend = ClaudeBackend(self.glossary, self.cfg["anthropic_api_key"], self.cfg["claude_model"])
            elif self.cfg["backend"] == "baidu":
                backend = BaiduBackend(self.glossary, self.cfg["baidu_appid"], self.cfg["baidu_key"])
            else:
                backend = GoogleBackend(self.glossary)
            self.translator = Translator(backend)
            self._backend_key = key
        return self.translator

    def _changed(self, frame: np.ndarray) -> bool:
        small = frame[::4, ::4].mean(axis=2).astype(np.int16)
        changed = self._last_small is None or small.shape != self._last_small.shape or \
            np.abs(small - self._last_small).mean() > 0.5
        self._last_small = small
        return changed

    def run(self) -> None:
        self._status("loading_ocr")
        from .ocr import Ocr

        ocr = Ocr()
        if not SHEET_PATH.exists():  # first run: the slang sheet isn't bundled, fetch it
            self.update_glossary()
        self._status("ready")
        while True:
            with self._cond:
                while self._frame is None and not self._stop:
                    self._cond.wait()
                if self._stop:
                    return
                frame, self._frame = self._frame, None
            try:
                self._process(ocr, frame)
            except Exception as e:  # keep the loop alive; surface the error in the UI
                traceback.print_exc()
                self._last_small = None  # retry this frame's content next tick
                self._status("error", e=e)

    def _process(self, ocr, frame: np.ndarray) -> None:
        if not self._changed(frame):
            return
        scale = float(self.cfg["upscale"])
        if scale != 1.0:
            import cv2

            frame = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        lines = ocr.read_lines(frame, self.cfg["ocr_min_score"])
        unseen = [l for l in lines if self.seen.is_new(l)]
        target = self.cfg["language"]  # chat is translated into the user's language
        new = unseen if self.cfg["translate_all"] else [l for l in unseen if needs_translation(l, target)]
        if not new:
            for l in unseen:
                self.seen.add(l)
            return

        self._status("translating", n=len(new))
        translations = self._get_translator().translate(new, list(self.context), target)
        for l in unseen:  # only after success, so a failed request is retried on the next change
            self.seen.add(l)
        self.context.extend(new)
        out = [(src, dst) for src, dst in zip(new, translations)
               if self.cfg["translate_all"] is False or _norm(src).lower() != _norm(dst).lower()]
        if out:
            self.messages.emit(out)
        self._status("watching")
