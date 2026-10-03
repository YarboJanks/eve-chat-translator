# EVE Chat Translator

Live OCR + translation overlay for EVE Online chat, English ⇄ Chinese (or any other chat on your screen).
Drag a box around a chat window. New lines are read every second and shown translated in an
always-on-top panel. Type in the panel's bottom box to translate your own reply; the result is copied
to your clipboard for you to paste.

## Setup (Windows, Python 3.11+)

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

Then double-click `Chat Translator.bat` (or run `.venv\Scripts\python run.pyw`).
The OCR models are bundled with RapidOCR, so no extra downloads are needed.

## Language / 语言

| Setting | Menus | Chat lines translated | Reply box |
|---|---|---|---|
| English | English | Chinese → English | English → Chinese |
| 中文 | 中文 | English → Chinese | Chinese → English |

Lines are classified by which script dominates, so an English message from a pilot with a Chinese name
counts as English, and a Chinese message mentioning a Muninn counts as Chinese.

## Translators

- **Google Translate**: free, no setup (default). Not reachable from mainland China.
- **Baidu Translate (百度翻译)**: works in mainland China. Needs a free APP ID + secret key from
  https://fanyi-api.baidu.com (sign up, enable 通用文本翻译).
- **Claude** (`claude-opus-5-5`): best with slang and context. Needs an Anthropic API key (in the UI or `ANTHROPIC_API_KEY`).

## EVE glossary (`data/`), used in both directions

Highest priority first:
- `glossary_custom.csv`: your own `chinese,english` additions
- The community EVE slang [Google Sheet](https://docs.google.com/spreadsheets/d/1r3wM2W1tbchWn59fAIhMrvfN0uOAAKcSMZbAga_xQik):
  downloaded on first run; **Update glossary** refreshes it
- `ships.json`: official Chinese ship names from ESI (e.g. 矮脚鸡级 = Bantam)

Google/Baidu: known terms are swapped into the target language before translating. Claude: matching
terms are passed as hints, plus the X/[range] command templates.

## What it does and doesn't do

- It **only reads screen pixels** inside the box you draw, like a screenshot.
- It **never touches the EVE client**: no process access, no memory reading, no client modification.
- It **never sends input to the game**. Replies go to your clipboard and you paste and send them yourself.
- The text it reads is sent to the translator you choose (Google, Baidu or Anthropic). Point the box at
  public channels, not private corp/alliance chat, if that matters to you.
- The panel and region outline are hidden from screen capture, so they can overlap the chat safely.
- The game must run in **windowed / borderless** mode for the overlay to appear above it.
- Settings are stored in `%APPDATA%\ChatTranslator\config.json` (API keys are stored there in plain text).

## Legal

EVE Online and the EVE logo are the registered trademarks of CCP hf. All rights are reserved worldwide.
All other trademarks are the property of their respective owners. EVE Online, the EVE logo, EVE and all
associated logos and designs are the intellectual property of CCP hf. All artwork, screenshots,
characters, vehicles, storylines, world facts or other recognizable features of the intellectual property
relating to these trademarks are likewise the intellectual property of CCP hf. CCP hf. has granted
permission to this project to use EVE Online and all associated logos and designs for promotional and
information purposes on its website but does not endorse, and is not in any way affiliated with, this
project. CCP is in no way responsible for the content on or functioning of this project, nor can it be
liable for any damage arising from the use of this project.

Licensed under the MIT License (see `LICENSE`).
