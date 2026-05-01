#!/usr/bin/env python3
"""
WhatsApp Chat Date Filter
=========================

Filters a WhatsApp chat export (the ``.txt`` file plus its attached media)
to keep only the messages and files that fall inside a user-defined date
range.

Usage example
-------------

    python whatsapp_chat_filter.py \
        --input  ./chat \
        --output ./chat_filtered \
        --start  08/10/2023 \
        --end    05/29/2025

Dates passed via ``--start`` and ``--end`` use the American format
``MM/DD/YYYY``. The dates inside the WhatsApp ``.txt`` follow the locale of
the phone that exported the chat. The most common variants
(``DD/MM/YYYY`` and ``MM/DD/YYYY``, with two- or four-digit years) are
auto-detected; you can also force a format with ``--chat-date-format``.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Iterable


# A WhatsApp message line looks roughly like:
#   "DD/MM/YYYY HH:MM - Sender: text"
#   "M/D/YY, H:MM AM - Sender: text"
# The separator between date+time and the rest is " - " (with surrounding
# spaces). Some locales insert a comma between date and time.
MESSAGE_LINE_RE = re.compile(
    r"""
    ^\s*
    (?P<datetime>
        \d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}     # date
        [,\s]+                                 # separator
        \d{1,2}:\d{2}(?::\d{2})?               # time
        (?:\s?[APap][Mm])?                     # optional AM/PM
    )
    \s[-–]\s                              # " - " (or en dash)
    """,
    re.VERBOSE,
)

# File extensions WhatsApp normally exports as attachments.
_MEDIA_EXTS = (
    "pdf", "jpe?g", "png", "opus", "mp3", "mp4", "webp", "3gp",
    "gif", "m4a", "aac", "ogg", "wav", "doc", "docx", "xls", "xlsx",
    "txt", "zip", "vcf",
)
_EXT_GROUP = "|".join(_MEDIA_EXTS)

# Primary pattern: WhatsApp prefixes a left-to-right mark (U+200E) before
# the attached filename and follows it with the localised "(arquivo anexado)"
# marker. We capture everything between those two anchors.
ATTACHMENT_RE = re.compile(
    r"‎?([^‎\n]+?\.(?:" + _EXT_GROUP + r"))\s*"
    r"\((?:arquivo anexado|file attached|archivo adjunto|fichier joint)\)",
    re.IGNORECASE,
)

# Fallback pattern: WhatsApp-style auto-generated names (IMG-YYYYMMDD-WAxxxx)
# that may appear in plain text without the attachment marker.
WHATSAPP_NAME_RE = re.compile(
    r"\b(?:IMG|VID|PTT|AUD|STK|DOC)-\d{8}-WA\d+\.[A-Za-z0-9]+\b",
    re.IGNORECASE,
)


@dataclass
class ChatMessage:
    """A single chat entry, possibly spanning multiple lines."""

    timestamp: datetime
    raw_lines: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.raw_lines)


# Candidate date+time formats that we try when ``--chat-date-format`` is not
# explicitly provided. Order matters: more specific patterns come first.
_AUTO_FORMATS: tuple[str, ...] = (
    "%d/%m/%Y %H:%M",
    "%d/%m/%y %H:%M",
    "%m/%d/%Y %H:%M",
    "%m/%d/%y %H:%M",
    "%d/%m/%Y, %H:%M",
    "%d/%m/%y, %H:%M",
    "%m/%d/%Y, %H:%M",
    "%m/%d/%y, %H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M:%S",
    "%d/%m/%Y, %I:%M %p",
    "%m/%d/%Y, %I:%M %p",
    "%d/%m/%y, %I:%M %p",
    "%m/%d/%y, %I:%M %p",
)


def parse_datetime(token: str, formats: Iterable[str]) -> datetime | None:
    token = token.strip().replace(" ", " ").replace("\xa0", " ")
    for fmt in formats:
        try:
            return datetime.strptime(token, fmt)
        except ValueError:
            continue
    return None


def detect_chat_format(sample_tokens: list[str]) -> str:
    """Pick the format that parses the most sample timestamps."""
    best_fmt, best_hits = _AUTO_FORMATS[0], -1
    for fmt in _AUTO_FORMATS:
        hits = sum(1 for tok in sample_tokens if parse_datetime(tok, [fmt]))
        if hits > best_hits:
            best_fmt, best_hits = fmt, hits
    if best_hits <= 0:
        raise SystemExit(
            "Could not auto-detect the date format used by the chat file. "
            "Re-run with --chat-date-format \"%d/%m/%Y %H:%M\" (or similar)."
        )
    return best_fmt


def collect_datetime_tokens(lines: Iterable[str], limit: int = 50) -> list[str]:
    tokens: list[str] = []
    for line in lines:
        match = MESSAGE_LINE_RE.match(line)
        if match:
            tokens.append(match.group("datetime"))
            if len(tokens) >= limit:
                break
    return tokens


def parse_messages(lines: list[str], chat_format: str) -> list[ChatMessage]:
    messages: list[ChatMessage] = []
    current: ChatMessage | None = None

    for line in lines:
        stripped = line.rstrip("\n")
        match = MESSAGE_LINE_RE.match(stripped)
        if match:
            ts = parse_datetime(match.group("datetime"), [chat_format])
            if ts is None:
                # Date pattern matched but format didn't parse — treat as
                # continuation of the previous message rather than dropping it.
                if current is not None:
                    current.raw_lines.append(stripped)
                continue
            if current is not None:
                messages.append(current)
            current = ChatMessage(timestamp=ts, raw_lines=[stripped])
        else:
            if current is None:
                # Stray text before any dated message: keep attached to a
                # synthetic header so we don't lose it on rewrite.
                current = ChatMessage(timestamp=datetime.min, raw_lines=[stripped])
            else:
                current.raw_lines.append(stripped)

    if current is not None:
        messages.append(current)
    return messages


def referenced_media(message: ChatMessage) -> set[str]:
    """Filenames mentioned anywhere inside a message."""
    found: set[str] = set()
    text = message.text
    for raw in ATTACHMENT_RE.findall(text):
        found.add(raw.lstrip("‎‏ ").strip())
    for raw in WHATSAPP_NAME_RE.findall(text):
        found.add(raw.strip())
    return found


def filename_date(filename: str) -> date | None:
    """Extract the YYYYMMDD date from WhatsApp-style media filenames."""
    m = re.search(r"-(\d{8})-WA\d+\.", filename)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def parse_user_date(value: str, label: str) -> date:
    """Parse a CLI date in American MM/DD/YYYY format."""
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise SystemExit(
        f"Invalid {label} date: {value!r}. Expected MM/DD/YYYY (e.g. 08/10/2023)."
    )


def find_chat_txt(input_dir: Path, override: Path | None) -> Path:
    if override is not None:
        return override
    candidates = sorted(input_dir.glob("*.txt"))
    if not candidates:
        raise SystemExit(f"No .txt file found inside {input_dir}.")
    if len(candidates) > 1:
        names = ", ".join(p.name for p in candidates)
        raise SystemExit(
            f"Multiple .txt files found ({names}). Use --chat-file to pick one."
        )
    return candidates[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Filter a WhatsApp chat export by date range, keeping only the "
            "messages and media files that fall inside the period."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input", "-i", required=True, type=Path,
        help="Folder containing the chat .txt and the attached media.",
    )
    parser.add_argument(
        "--output", "-o", required=True, type=Path,
        help="Destination folder for the filtered chat and media.",
    )
    parser.add_argument(
        "--start", "-s", required=True,
        help="Start date in MM/DD/YYYY format (inclusive).",
    )
    parser.add_argument(
        "--end", "-e", required=True,
        help="End date in MM/DD/YYYY format (inclusive).",
    )
    parser.add_argument(
        "--chat-file",
        type=Path, default=None,
        help="Path to the chat .txt (auto-detected inside --input if omitted).",
    )
    parser.add_argument(
        "--chat-date-format",
        default=None,
        help=(
            "strptime format used by the chat (e.g. \"%%d/%%m/%%Y %%H:%%M\"). "
            "Auto-detected when omitted."
        ),
    )
    parser.add_argument(
        "--encoding",
        default="utf-8",
        help="Encoding of the chat .txt (default: utf-8).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be kept/removed without writing anything.",
    )
    args = parser.parse_args(argv)

    start_date = parse_user_date(args.start, "start")
    end_date = parse_user_date(args.end, "end")
    if start_date > end_date:
        raise SystemExit("Start date must be earlier than or equal to end date.")

    input_dir: Path = args.input.resolve()
    output_dir: Path = args.output.resolve()
    if not input_dir.is_dir():
        raise SystemExit(f"Input folder not found: {input_dir}")
    if output_dir == input_dir:
        raise SystemExit("--output must be different from --input.")

    chat_path = find_chat_txt(input_dir, args.chat_file)
    raw_text = chat_path.read_text(encoding=args.encoding)
    raw_lines = raw_text.splitlines()

    chat_format = args.chat_date_format or detect_chat_format(
        collect_datetime_tokens(raw_lines)
    )

    messages = parse_messages(raw_lines, chat_format)
    kept: list[ChatMessage] = []
    kept_media: set[str] = set()

    start_dt = datetime.combine(start_date, datetime.min.time())
    end_dt = datetime.combine(end_date, datetime.max.time())

    for msg in messages:
        if msg.timestamp == datetime.min:
            # Pre-amble lines with no parseable timestamp are dropped on
            # purpose: WhatsApp's own header ("As mensagens e ligações são
            # protegidas...") always carries a date and survives normally.
            continue
        if start_dt <= msg.timestamp <= end_dt:
            kept.append(msg)
            kept_media.update(referenced_media(msg))

    # Belt-and-suspenders: also keep any media file whose embedded date sits
    # inside the range, even if the message that referenced it got cut.
    for entry in input_dir.iterdir():
        if not entry.is_file() or entry == chat_path:
            continue
        d = filename_date(entry.name)
        if d is not None and start_date <= d <= end_date:
            kept_media.add(entry.name)

    available_media = {p.name for p in input_dir.iterdir() if p.is_file()}
    available_media.discard(chat_path.name)
    media_to_copy = sorted(kept_media & available_media)
    media_missing = sorted(kept_media - available_media)
    media_dropped = sorted(available_media - kept_media)

    print(f"Chat file:        {chat_path}")
    print(f"Detected format:  {chat_format}")
    print(f"Range:            {start_date.isoformat()} .. {end_date.isoformat()}")
    print(f"Messages kept:    {len(kept)} / {len(messages)}")
    print(f"Media kept:       {len(media_to_copy)}")
    print(f"Media dropped:    {len(media_dropped)}")
    if media_missing:
        print(f"Referenced but missing on disk: {len(media_missing)}")

    if args.dry_run:
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    new_chat_path = output_dir / chat_path.name
    new_chat_path.write_text(
        "\n".join(msg.text for msg in kept) + ("\n" if kept else ""),
        encoding=args.encoding,
    )
    for name in media_to_copy:
        shutil.copy2(input_dir / name, output_dir / name)

    print(f"Wrote filtered chat to: {new_chat_path}")
    print(f"Output folder:          {output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
