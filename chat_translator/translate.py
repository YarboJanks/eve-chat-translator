"""Translation between English and Chinese, both directions, with the EVE glossary.

target "en": Chinese -> English    target "zh": English -> Simplified Chinese
"""
import hashlib
import json
import random
import re
from collections import OrderedDict

import requests

from .glossary import Glossary

BACKENDS = ("google", "baidu", "claude")

LANG_NAME = {"en": "natural English", "zh": "Simplified Chinese"}
EVE_STYLE = {
    "en": "as English-speaking EVE players phrase it (align, warp, anchor, tackle, bubble, cyno, bridge, SRP, PAP, FC, MOTD)",
    "zh": "the way Chinese EVE players actually type: short and casual, using their ship nicknames and fleet slang",
}

CHAT_SYSTEM = """You translate EVE Online chat messages into {lang}, {style}.
Messages are OCR'd from a screenshot, so expect occasional misread characters; infer the intended text.
Keep player names, system names, channel tags and numbers as they are.
A glossary of EVE terms may be provided; prefer its meanings when they fit the context.
If a message is already in the target language, return it unchanged.
Reply with ONLY a JSON array of strings: one translation per input message, in the same order, same length."""

REPLY_SYSTEM = """You translate a message the user wants to send in an EVE Online chat channel into {lang}, {style}.
A glossary of EVE terms may be provided; use those terms.
Keep player names, system names and numbers as they are.
Reply with ONLY the translated message, no quotes or explanation."""


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def needs_translation(line: str, target: str) -> bool:
    """Should a chat line be translated into `target`?

    Chat lines mix scripts (an English message from a pilot with a Chinese name, a Chinese message
    mentioning a Muninn), so decide by which script dominates. One CJK character carries roughly a
    word, so it's weighted against ~4 Latin letters.
    """
    non_latin = sum(1 for ch in line if ch.isalpha() and ord(ch) > 0x24F)
    latin = sum(1 for ch in line if ch.isascii() and ch.isalpha())
    mostly_latin = latin > non_latin * 4
    if target == "en":
        return non_latin > 0 and not mostly_latin
    return latin >= 3 and mostly_latin


class GoogleBackend:
    URL = "https://translate.googleapis.com/translate_a/single"
    GOOGLE_LANG = {"en": "en", "zh": "zh-CN"}

    def __init__(self, glossary: Glossary):
        self.glossary = glossary

    def _prepare(self, text: str, target: str) -> str:
        # Swap known EVE terms into the target language first so Google keeps them as-is.
        return self.glossary.substitute(text) if target == "en" else self.glossary.substitute_reverse(text)

    def translate(self, lines: list[str], context: list[str], target: str) -> list[str]:
        prepared = [self._prepare(l, target) for l in lines]
        out = self._request("\n".join(prepared), target).split("\n")
        if len(out) != len(lines):  # segmenting didn't round-trip; fall back to one call per line
            out = [self._request(line, target) for line in prepared]
        return [self._tidy(o) for o in out]

    def reply(self, text: str, target: str) -> str:
        return self._tidy(self._request(self._prepare(text, target), target))

    @staticmethod
    def _tidy(text: str) -> str:
        text = re.sub(r"\s{2,}", " ", text)
        text = re.sub(r"([\[(【（])\s+", r"\1", text)
        return re.sub(r"\s+([\])】）,.!?:;，。])", r"\1", text).strip()

    def _request(self, text: str, target: str) -> str:
        r = requests.get(
            self.URL,
            # Source must be explicit for en->zh: after glossary substitution the English text contains
            # Chinese terms, and auto-detect then decides it's already Chinese and returns it unchanged.
            params={"client": "gtx", "sl": "en" if target == "zh" else "auto", "tl": self.GOOGLE_LANG[target],
                    "dt": "t", "q": text},
            timeout=10,
        )
        r.raise_for_status()
        return "".join(seg[0] for seg in r.json()[0] if seg[0])


class BaiduBackend(GoogleBackend):
    """Baidu Translate general API (fanyi-api.baidu.com). Reachable from mainland China, unlike Google."""

    URL = "https://fanyi-api.baidu.com/api/trans/vip/translate"
    BAIDU_LANG = {"en": "en", "zh": "zh"}

    def __init__(self, glossary: Glossary, appid: str, key: str):
        super().__init__(glossary)
        if not appid or not key:
            raise RuntimeError("Baidu Translate needs an APP ID and secret key (fanyi-api.baidu.com)")
        self.appid, self.key = appid, key

    def translate(self, lines: list[str], context: list[str], target: str) -> list[str]:
        # Baidu returns one trans_result per input line, so a newline-joined batch maps back 1:1.
        prepared = [self._prepare(l, target) for l in lines]
        results = self._query("\n".join(prepared), target)
        if len(results) != len(lines):
            results = [" ".join(self._query(line, target)) for line in prepared]
        return [self._tidy(r) for r in results]

    def reply(self, text: str, target: str) -> str:
        return self._tidy(" ".join(self._query(self._prepare(text, target), target)))

    def _query(self, text: str, target: str) -> list[str]:
        salt = str(random.randint(32768, 65536))
        sign = hashlib.md5((self.appid + text + salt + self.key).encode("utf-8")).hexdigest()
        source = "en" if target == "zh" else "auto"  # see GoogleBackend._request
        r = requests.post(self.URL, data={"q": text, "from": source, "to": self.BAIDU_LANG[target],
                                          "appid": self.appid, "salt": salt, "sign": sign}, timeout=10)
        r.raise_for_status()
        data = r.json()
        if "error_code" in data and str(data["error_code"]) != "52000":
            raise RuntimeError(f"Baidu error {data['error_code']}: {data.get('error_msg', '')}")
        return [item["dst"] for item in data["trans_result"]]


class ClaudeBackend:
    def __init__(self, glossary: Glossary, api_key: str, model: str):
        import anthropic

        self.glossary = glossary
        self.client = anthropic.Anthropic(api_key=api_key or None)
        self.model = model

    def _glossary_block(self, text: str, target: str) -> str:
        if target == "en":
            pairs = self.glossary.hints_for([text]) + self.glossary.templates
            title = "Glossary (EVE Chinese = English):"
        else:
            pairs = self.glossary.reverse_hints_for(text) + [(en, zh) for zh, en in self.glossary.templates]
            title = "Glossary (EVE English = Chinese):"
        if not pairs:
            return ""
        return title + "\n" + "\n".join(f"{a} = {b}" for a, b in pairs) + "\n\n"

    def _ask(self, system: str, prompt: str) -> str:
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=8000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low"},
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("Claude declined to translate this")
        return "".join(b.text for b in response.content if b.type == "text").strip()

    def translate(self, lines: list[str], context: list[str], target: str) -> list[str]:
        prompt = self._glossary_block("\n".join(lines), target)
        if context:
            prompt += "Earlier messages (context only, do not translate):\n" + "\n".join(context) + "\n\n"
        prompt += "Translate these messages:\n" + json.dumps(lines, ensure_ascii=False)
        text = self._ask(CHAT_SYSTEM.format(lang=LANG_NAME[target], style=EVE_STYLE[target]), prompt)
        result = json.loads(text[text.index("[") : text.rindex("]") + 1])
        if len(result) != len(lines):
            raise RuntimeError(f"Claude returned {len(result)} translations for {len(lines)} lines")
        return [str(r) for r in result]

    def reply(self, text: str, target: str) -> str:
        prompt = self._glossary_block(text, target) + "Message:\n" + text
        return self._ask(REPLY_SYSTEM.format(lang=LANG_NAME[target], style=EVE_STYLE[target]), prompt)


class Translator:
    """Caches chat-line translations so repeated OCR of the same line is free."""

    def __init__(self, backend, cache_size: int = 3000):
        self.backend = backend
        self.cache: OrderedDict[tuple[str, str], str] = OrderedDict()
        self.cache_size = cache_size

    def translate(self, lines: list[str], context: list[str], target: str) -> list[str]:
        missing = [l for l in dict.fromkeys(lines) if (target, _norm(l)) not in self.cache]
        if missing:
            for src, dst in zip(missing, self.backend.translate(missing, context, target)):
                self.cache[(target, _norm(src))] = dst
            while len(self.cache) > self.cache_size:
                self.cache.popitem(last=False)
        return [self.cache[(target, _norm(l))] for l in lines]

    def reply(self, text: str, target: str) -> str:
        return self.backend.reply(text, target)
