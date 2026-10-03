"""EVE Chinese -> English glossary.

Sources, highest priority first:
  data/glossary_custom.csv  - your own additions (columns: chinese,english)
  data/glossary_sheet.csv   - the community slang sheet (Chinese, GoogleTranslate, Actual Meaning, ...)
  data/ships.json           - official ship names from ESI (language=zh), e.g. 矮脚鸡级 -> Bantam
"""
import csv
import io
import json
import re
from pathlib import Path

import requests

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SHEET_PATH = DATA_DIR / "glossary_sheet.csv"
CUSTOM_PATH = DATA_DIR / "glossary_custom.csv"
SHIPS_PATH = DATA_DIR / "ships.json"
SHEET_CSV_URL = ("https://docs.google.com/spreadsheets/d/1r3wM2W1tbchWn59fAIhMrvfN0uOAAKcSMZbAga_xQik"
                 "/export?format=csv&gid=0")

PLACEHOLDER = re.compile(r"X{1,3}|xxx|\[range\]")
CJK = re.compile(r"[㐀-鿿豈-﫿]")
# Full-width punctuation -> ASCII (length-preserving), so OCR's "，" matches the sheet's ","
PUNCT = str.maketrans("，。？！：；（）【】", ",.?!:;()[]")


def _canon(s: str) -> str:
    return re.sub(r"\s+", "", s.translate(PUNCT))


def _variants(cell: str) -> list[str]:
    """'小白/阿斯特罗' -> both; '国門(过门)' -> '国門', '过门', '国門过门'."""
    out = []
    for part in re.split(r"[/／]", cell):
        part = part.strip()
        if not part:
            continue
        inner = re.findall(r"[(（]([^)）]*)[)）]", part)
        stripped = re.sub(r"[(（][^)）]*[)）]", "", part).strip()
        out += [part, stripped, re.sub(r"[()（）]", "", part).strip(), *[i.strip() for i in inner]]
    return [v for v in dict.fromkeys(out) if CJK.search(v)]


class Glossary:
    def __init__(self):
        self.entries: dict[str, str] = {}   # chinese -> english, used for Google pre-substitution
        self.hints: dict[str, str] = {}     # superset, includes riskier short forms; Claude hints only
        self.templates: list[tuple[str, str]] = []  # phrases with X / [range] placeholders
        self.reverse: dict[str, str] = {}   # english (lowercase) -> chinese, for replying in Chinese
        self.load()

    def load(self) -> None:
        # Build into fresh containers and swap at the end: the worker thread may be mid-translation.
        self.entries, self.hints, self.templates, self.reverse = {}, {}, [], {}
        # Lowest priority first; later sources overwrite.
        if SHIPS_PATH.exists():
            for ship in json.loads(SHIPS_PATH.read_text(encoding="utf-8")):
                zh, en = ship["zh"].strip(), ship["en"].strip()
                self._add(zh, en)
                short = zh.removesuffix("级")
                if short != zh and len(short) >= 2:
                    self.hints[short] = en  # e.g. 秃鹫 - also an everyday word, so hint only
                self.reverse[en.lower()] = short if len(short) >= 2 else zh  # chat drops the 级 suffix
        for path, en_col in ((SHEET_PATH, 2), (CUSTOM_PATH, 1)):
            if path.exists():
                self._load_csv(path.read_text(encoding="utf-8-sig"), en_col)
        self._pattern, self._hint_pattern = self._compile(self.entries), self._compile(self.hints)
        self._reverse_pattern = re.compile(
            r"(?<![A-Za-z])(" + "|".join(re.escape(k) for k in sorted(self.reverse, key=len, reverse=True))
            + r")(?:e?s)?(?![A-Za-z])", re.IGNORECASE) if self.reverse else None  # allow plurals

    def _load_csv(self, text: str, en_col: int) -> None:
        rows = list(csv.reader(io.StringIO(text)))
        reverse: dict[str, str] = {}  # first row wins within a file; the file still overrides earlier sources
        for row in rows[1:]:
            if len(row) <= en_col:
                continue
            zh, en = row[0].strip(), row[en_col].strip()
            if not zh or not en:
                continue
            if PLACEHOLDER.search(zh):
                self.templates.append((zh, en))
                continue
            zh_parts = [z for z in re.split(r"[/／]", zh) if z.strip()]
            en_parts = [e.strip() for e in en.split("/") if e.strip()]
            if len(zh_parts) > 1 and len(zh_parts) == len(en_parts):  # '进站/出站' = 'dock / undock'
                pairs = zip(zh_parts, en_parts)
            else:
                pairs = [(zh, en)]
            for z, e in pairs:
                for v in _variants(z):
                    self._add(v, e)
                zh_out = re.sub(r"[()（）]", "", re.split(r"[/／]", z)[0]).strip()
                for e_variant in e.split("/"):
                    e_variant = e_variant.strip().lower()
                    if len(e_variant) >= 3 and "(" not in e_variant and CJK.search(zh_out):
                        reverse.setdefault(e_variant, zh_out)
        self.reverse.update(reverse)

    def _add(self, zh: str, en: str) -> None:
        zh = _canon(zh)
        self.hints[zh] = en
        if len(zh) >= 2:  # single characters like 带/马 occur inside unrelated words
            self.entries[zh] = en

    @staticmethod
    def _compile(d: dict[str, str]):
        if not d:
            return None
        # Allow whitespace between characters: OCR spacing varies.
        alts = (r"\s*".join(re.escape(c) for c in k) for k in sorted(d, key=len, reverse=True))
        return re.compile("|".join(alts))

    def substitute(self, line: str) -> str:
        """Replace known Chinese terms with English before machine translation (longest match wins)."""
        pattern, entries = self._pattern, self.entries
        if not pattern:
            return line
        return pattern.sub(lambda m: f" {entries.get(_canon(m.group(0)), m.group(0))} ", line.translate(PUNCT))

    def hints_for(self, lines: list[str]) -> list[tuple[str, str]]:
        pattern, hints = self._hint_pattern, self.hints
        if not pattern:
            return []
        found = {}
        for line in lines:
            for m in pattern.finditer(line.translate(PUNCT)):
                key = _canon(m.group(0))
                if key in hints:
                    found[key] = hints[key]
        return list(found.items())

    def substitute_reverse(self, text: str) -> str:
        """Replace known English EVE terms with their Chinese chat equivalents (whole words, longest first)."""
        pattern, reverse = self._reverse_pattern, self.reverse
        if not pattern:
            return text
        return pattern.sub(lambda m: reverse.get(m.group(1).lower(), m.group(0)), text)

    def reverse_hints_for(self, text: str) -> list[tuple[str, str]]:
        pattern, reverse = self._reverse_pattern, self.reverse
        if not pattern:
            return []
        return list({m.group(1).lower(): reverse[m.group(1).lower()]
                     for m in pattern.finditer(text) if m.group(1).lower() in reverse}.items())

    @staticmethod
    def update_sheet() -> int:
        """Re-download the slang sheet. Returns number of rows."""
        r = requests.get(SHEET_CSV_URL, timeout=30)
        r.raise_for_status()
        r.encoding = "utf-8"
        rows = list(csv.reader(io.StringIO(r.text)))
        if not rows or not rows[0] or rows[0][0].strip().lower() != "chinese":
            raise RuntimeError("Sheet format not recognised (is it still shared publicly?)")
        DATA_DIR.mkdir(exist_ok=True)
        SHEET_PATH.write_text(r.text, encoding="utf-8")
        return len(rows) - 1
