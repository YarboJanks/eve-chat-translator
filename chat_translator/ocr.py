import logging

import numpy as np

logging.getLogger("RapidOCR").setLevel(logging.WARNING)


class Ocr:
    """Wraps RapidOCR (PP-OCR models, Chinese + English) and groups boxes into text rows."""

    def __init__(self):
        from rapidocr import RapidOCR

        self.engine = RapidOCR()
        logging.getLogger("RapidOCR").setLevel(logging.WARNING)

    def read_lines(self, img_bgr: np.ndarray, min_score: float = 0.6) -> list[str]:
        result = self.engine(img_bgr)
        if result.txts is None or result.boxes is None:
            return []

        items = []
        for box, txt, score in zip(result.boxes, result.txts, result.scores):
            txt = txt.strip()
            if score < min_score or not txt:
                continue
            ys, xs = box[:, 1], box[:, 0]
            items.append((float(ys.min()), float(ys.max()), float(xs.min()), txt))

        # Boxes whose vertical centres are close belong to the same chat row
        # (e.g. "[World] Name:" detected separately from the message body).
        items.sort(key=lambda i: (i[0] + i[1]) / 2)
        rows: list[dict] = []
        for top, bottom, x, txt in items:
            centre = (top + bottom) / 2
            if rows and abs(centre - rows[-1]["centre"]) < 0.5 * (bottom - top):
                rows[-1]["parts"].append((x, txt))
            else:
                rows.append({"centre": centre, "parts": [(x, txt)]})
        return [" ".join(t for _, t in sorted(r["parts"])) for r in rows]
