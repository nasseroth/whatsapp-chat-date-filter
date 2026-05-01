# whatsapp-chat-date-filter

Filter a WhatsApp chat export — both the `.txt` transcript **and the
attached media** — down to a single date range, in one command.

WhatsApp's *Export chat* feature dumps the whole history. When you only
need a specific period (for legal evidence, journalism, a personal
archive, an LLM context window, etc.), you have to manually trim
hundreds of messages and delete every media file that no longer
belongs. This script does it for you, lossless and reproducible.

---

## Features

- Trims the chat `.txt` to the user-supplied date range (inclusive).
- Copies only the media files referenced by the kept messages — every
  audio, image, PDF, video or document outside the range is left out.
- Auto-detects the date format used inside the export
  (`DD/MM/YYYY`, `MM/DD/YYYY`, two- or four-digit years, AM/PM, etc.).
- Preserves multi-line messages exactly as they were written.
- `--dry-run` mode reports what *would* be kept/removed before you
  commit to writing anything.
- Pure standard library — no `pip install` step.

---

## Why the American date format on the CLI?

The chat export keeps the date format of the phone that produced it
(usually `DD/MM/YYYY` outside the US). The script's **command-line
arguments** intentionally use the American convention `MM/DD/YYYY`, so
that anyone landing on this repository from any country reads the
examples the same way. ISO `YYYY-MM-DD` is also accepted.

---

## Requirements

- Python 3.9 or newer.
- The exported chat folder, containing:
  - the `WhatsApp conversation with <name>.txt` (or its localised name);
  - every media file that came along with the export.

> Tip: when exporting from WhatsApp, choose **"Include media"** on
> mobile, then unzip the result to a folder.

---

## Installation

```bash
git clone https://github.com/nasseroth/whatsapp-chat-date-filter.git
cd whatsapp-chat-date-filter
```

There are no third-party dependencies.

---

## Usage

```bash
python whatsapp_chat_filter.py \
    --input  ./chat \
    --output ./chat_filtered \
    --start  08/10/2023 \
    --end    05/29/2025
```

The example above keeps every message **from August 10 2023 through
May 29 2025**, copies the corresponding media into `./chat_filtered`,
and leaves the original folder untouched.

### Arguments

| Flag | Required | Description |
|------|----------|-------------|
| `--input`, `-i` | ✓ | Folder with the original chat `.txt` and its media. |
| `--output`, `-o` | ✓ | Destination folder (created if missing). Must differ from `--input`. |
| `--start`, `-s` | ✓ | Range start, inclusive. Format `MM/DD/YYYY` or `YYYY-MM-DD`. |
| `--end`, `-e` | ✓ | Range end, inclusive. Same formats as `--start`. |
| `--chat-file` | | Path to the `.txt` (auto-detected when only one exists in `--input`). |
| `--chat-date-format` | | `strptime` format used inside the chat. Auto-detected when omitted. |
| `--encoding` | | Defaults to `utf-8`. |
| `--dry-run` | | Print the summary without writing the output folder. |

### Sample output

```
Chat file:        ./chat/WhatsApp conversation with Someone.txt
Detected format:  %d/%m/%Y %H:%M
Range:            2023-08-10 .. 2025-05-29
Messages kept:    327 / 601
Media kept:       117
Media dropped:    49
Wrote filtered chat to: ./chat_filtered/WhatsApp conversation with Someone.txt
```

---

## How it works

1. **Parse the transcript.** Each line that begins with a recognisable
   `date time -` prefix starts a new message; any line that does not
   match that pattern is treated as a continuation of the previous
   message (so multi-paragraph quotes stay intact).
2. **Filter by date range.** Messages whose timestamp falls inside
   `[--start, --end]` (inclusive on both ends) are kept; the rest are
   discarded.
3. **Track referenced media.** For every kept message, the script looks
   for two patterns:
   - `‎FILENAME (arquivo anexado)` — the marker WhatsApp adds for any
     attached file, also recognised in English, Spanish and French.
   - `IMG-YYYYMMDD-WAxxxx.ext` style names mentioned in plain text.
4. **Sanity check via filename dates.** WhatsApp encodes the capture
   date inside auto-generated filenames (`PTT-20250505-WA0036.opus` →
   `2025-05-05`). Files whose embedded date sits inside the range are
   kept even if the message that referenced them happened to be cut.
5. **Write the output folder.** A new `.txt` is written with only the
   surviving messages, and the kept media files are copied across.
   The original folder is never modified.

---

## Date-format cheatsheet

| Locale shown in the export | `--chat-date-format` |
|----------------------------|----------------------|
| `05/05/2025 16:19` (Brazil, Portugal, most of EU) | `"%d/%m/%Y %H:%M"` |
| `5/5/25, 16:19` (compact 24h) | `"%d/%m/%y, %H:%M"` |
| `5/5/25, 4:19 PM` (US, 12h) | `"%m/%d/%y, %I:%M %p"` |
| `2025-05-05 16:19` (ISO) | `"%Y-%m-%d %H:%M"` |

Pass it through `--chat-date-format` only if auto-detection picks the
wrong one.

---

## Limitations

- Messages flagged by WhatsApp as `<Mídia oculta>` / `<Media omitted>`
  contain no filename, so there is nothing to match against the media
  folder. They are kept inside the trimmed `.txt` if their timestamp
  falls in range, but no attachment is copied for them.
- The script never deletes anything from the input folder; it produces
  a fresh output folder so you can always rerun with a different range.
- Edits / deletions performed on the phone after the export are not
  reflected — WhatsApp does not include that history.

---

## Contributing

Pull requests are welcome. If your phone exports the chat in a format
the auto-detector misses, please open an issue with a redacted sample
of the first few lines (just the date/time prefix is enough) and the
locale of your device.
