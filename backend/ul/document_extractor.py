"""Document input extraction for PDF, DOCX, and email.

PDF page extraction is unchanged and delegated to document_chunking.extract_pages_from_pdf.
DOCX and email extractors return the same page dicts used by semantic chunking:
source_file, page_number, and text, plus document_type metadata.
"""

from __future__ import annotations

import email
import logging
import re
import tempfile
from email.message import EmailMessage, Message
from html.parser import HTMLParser
from pathlib import Path

logger = logging.getLogger(__name__)

PDF_EXTENSIONS = {".pdf"}
DOCX_EXTENSIONS = {".docx"}
EMAIL_EXTENSIONS = {".eml", ".msg"}
SUPPORTED_UPLOAD_EXTENSIONS = PDF_EXTENSIONS | DOCX_EXTENSIONS | EMAIL_EXTENSIONS

DOCUMENT_TYPE_PDF = "pdf"
DOCUMENT_TYPE_DOCX = "docx"
DOCUMENT_TYPE_EMAIL = "email"

_HEADING_STYLE_RE = re.compile(r"^Heading\s+(\d+)$", re.IGNORECASE)


def suffix_of(filename: str) -> str:
    return Path(filename).suffix.lower()


def is_supported_upload_filename(filename: str | None) -> bool:
    if not filename:
        return False
    return suffix_of(filename) in SUPPORTED_UPLOAD_EXTENSIONS


def document_type_for_filename(filename: str) -> str:
    suffix = suffix_of(filename)
    if suffix in PDF_EXTENSIONS:
        return DOCUMENT_TYPE_PDF
    if suffix in DOCX_EXTENSIONS:
        return DOCUMENT_TYPE_DOCX
    if suffix in EMAIL_EXTENSIONS:
        return DOCUMENT_TYPE_EMAIL
    raise ValueError(
        f"Unsupported file type '{filename}'. "
        "Accepted types: PDF (.pdf), DOCX (.docx), Email (.eml, .msg)."
    )


def _page_record(
    source_file: str,
    page_number: int,
    text: str,
    document_type: str,
) -> dict:
    return {
        "source_file": source_file,
        "page_number": page_number,
        "text": text,
        "document_type": document_type,
    }


def _require_pages(pages: list[dict], source_file: str) -> list[dict]:
    if not pages:
        raise ValueError(f"No text could be extracted from {source_file}")
    return pages


def extract_pages_from_document(file_path: str) -> list[dict]:
    """Dispatch to the extractor for this file type. PDF behavior is unchanged."""
    path = Path(file_path)
    document_type = document_type_for_filename(path.name)

    if document_type == DOCUMENT_TYPE_PDF:
        from ul.document_chunking import extract_pages_from_pdf

        pages = extract_pages_from_pdf(str(path))
        for page in pages:
            page.setdefault("document_type", DOCUMENT_TYPE_PDF)
        return pages

    if document_type == DOCUMENT_TYPE_DOCX:
        return extract_pages_from_docx(str(path))

    return extract_pages_from_email(str(path))


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------


def extract_pages_from_docx(file_path: str) -> list[dict]:
    """
    Extract DOCX text while preserving paragraph, heading, and table structure.

    Word files have no reliable page numbers, so the document is returned as
    one page (page_number=1) in the same format used by PDF extraction.
    """
    from docx import Document
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    path = Path(file_path)
    source_file = path.name
    logger.info("Extracting text from DOCX: %s", source_file)

    try:
        document = Document(file_path)
    except Exception as exc:
        raise ValueError(f"Failed to open DOCX '{source_file}': {exc}") from exc

    blocks: list[str] = []
    body = document.element.body
    for child in body.iterchildren():
        try:
            if isinstance(child, CT_P):
                paragraph = Paragraph(child, document)
                formatted = _format_docx_paragraph(paragraph)
                if formatted:
                    blocks.append(formatted)
            elif isinstance(child, CT_Tbl):
                table = Table(child, document)
                formatted = _format_docx_table(table)
                if formatted:
                    blocks.append(formatted)
        except Exception as exc:
            logger.warning("Failed to extract a block from %s: %s", source_file, exc)

    text = "\n\n".join(blocks).strip()
    if not text:
        raise ValueError(f"No text could be extracted from {source_file}")

    logger.info("Extracted %d characters from DOCX %s", len(text), source_file)
    return [_page_record(source_file, 1, text, DOCUMENT_TYPE_DOCX)]


def _format_docx_paragraph(paragraph) -> str:
    raw = (paragraph.text or "").strip()
    if not raw:
        return ""

    style_name = ""
    try:
        if paragraph.style is not None and paragraph.style.name:
            style_name = str(paragraph.style.name).strip()
    except Exception:
        style_name = ""

    heading_match = _HEADING_STYLE_RE.match(style_name)
    if heading_match:
        level = max(1, min(int(heading_match.group(1)), 6))
        return f"{'#' * level} {raw}"
    if style_name.lower() in {"title", "subtitle"}:
        return f"# {raw}" if style_name.lower() == "title" else f"## {raw}"
    return raw


def _format_docx_table(table) -> str:
    rows: list[str] = []
    for row in table.rows:
        cells = [" ".join((cell.text or "").split()) for cell in row.cells]
        if any(cells):
            rows.append(" | ".join(cells))
    if not rows:
        return ""
    return "[Table]\n" + "\n".join(rows)


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------


def extract_pages_from_email(file_path: str) -> list[dict]:
    """
    Extract an email as one source document: headers, body, and attachment content.

    Returns the same page dict format used by PDF extraction. Attachment PDF/DOCX
    content is appended as later pages of this same source_file.
    """
    path = Path(file_path)
    source_file = path.name
    suffix = suffix_of(source_file)
    logger.info("Extracting text from email: %s", source_file)

    if suffix == ".msg":
        message = _load_outlook_msg(path)
    else:
        message = _load_eml(path)

    header_and_body = _format_email_main_text(message)
    pages: list[dict] = []
    if header_and_body.strip():
        pages.append(_page_record(source_file, 1, header_and_body.strip(), DOCUMENT_TYPE_EMAIL))

    next_page = 2 if pages else 1
    attachment_pages, attachment_names = _extract_email_attachments(
        message,
        source_file=source_file,
        start_page=next_page,
    )
    pages.extend(attachment_pages)

    if attachment_names and pages:
        listing = "Attachments:\n" + "\n".join(f"- {name}" for name in attachment_names)
        pages[0]["text"] = f"{pages[0]['text']}\n\n{listing}".strip()

    return _require_pages(pages, source_file)


def _load_eml(path: Path) -> Message:
    try:
        with path.open("rb") as handle:
            return email.message_from_binary_file(handle)
    except Exception as exc:
        raise ValueError(f"Failed to open email '{path.name}': {exc}") from exc


def _load_outlook_msg(path: Path) -> Message:
    """Parse Outlook .msg into an email.message.Message so the rest of the path is shared."""
    try:
        import extract_msg
    except ImportError as exc:
        raise ValueError(
            "Outlook .msg files require the 'extract-msg' package. "
            "Save the email as .eml, or install extract-msg."
        ) from exc

    try:
        msg = extract_msg.Message(str(path))
        constructed = EmailMessage()
        if msg.sender:
            constructed["From"] = str(msg.sender)
        if msg.to:
            constructed["To"] = str(msg.to)
        if msg.cc:
            constructed["Cc"] = str(msg.cc)
        if msg.subject:
            constructed["Subject"] = str(msg.subject)
        if msg.date:
            constructed["Date"] = str(msg.date)

        body = (msg.body or "").strip()
        html_body = (getattr(msg, "htmlBody", None) or "").strip()
        if body:
            constructed.set_content(body)
        elif html_body:
            constructed.set_content(_html_to_text(html_body))
        else:
            constructed.set_content("")

        for attachment in msg.attachments or []:
            filename = (
                getattr(attachment, "longFilename", None)
                or getattr(attachment, "shortFilename", None)
                or "attachment"
            )
            data = getattr(attachment, "data", None)
            if not data:
                continue
            maintype, subtype = _guess_mime_parts(str(filename))
            constructed.add_attachment(
                data if isinstance(data, bytes) else bytes(data),
                maintype=maintype,
                subtype=subtype,
                filename=str(filename),
            )
        return constructed
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Failed to open Outlook email '{path.name}': {exc}") from exc
    finally:
        close = getattr(msg, "close", None) if "msg" in locals() else None
        if callable(close):
            close()


def _guess_mime_parts(filename: str) -> tuple[str, str]:
    suffix = suffix_of(filename)
    if suffix == ".pdf":
        return "application", "pdf"
    if suffix == ".docx":
        return "application", "vnd.openxmlformats-officedocument.wordprocessingml.document"
    if suffix in EMAIL_EXTENSIONS:
        return "message", "rfc822"
    return "application", "octet-stream"


def _decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    parts = email.header.decode_header(value)
    decoded: list[str] = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(str(part))
    return " ".join(decoded).strip()


def _header(message: Message, name: str) -> str:
    return _decode_header_value(message.get(name))


def _format_email_main_text(message: Message) -> str:
    lines = [
        f"Subject: {_header(message, 'Subject') or '(none)'}",
        f"From: {_header(message, 'From') or '(unknown)'}",
        f"To: {_header(message, 'To') or '(unknown)'}",
    ]
    cc = _header(message, "Cc")
    if cc:
        lines.append(f"Cc: {cc}")
    bcc = _header(message, "Bcc")
    if bcc:
        lines.append(f"Bcc: {bcc}")
    date = _header(message, "Date")
    if date:
        lines.append(f"Date: {date}")

    body = _extract_email_body(message)
    lines.append("")
    lines.append(body.strip() if body.strip() else "(no email body)")
    return "\n".join(lines)


def _extract_email_body(message: Message) -> str:
    plain_parts: list[str] = []
    html_parts: list[str] = []

    if message.is_multipart():
        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if _is_attachment_part(part):
                continue
            content_type = (part.get_content_type() or "").lower()
            payload = _decode_part_payload(part)
            if not payload:
                continue
            if content_type == "text/plain":
                plain_parts.append(payload)
            elif content_type == "text/html":
                html_parts.append(_html_to_text(payload))
    else:
        content_type = (message.get_content_type() or "").lower()
        payload = _decode_part_payload(message)
        if content_type == "text/html":
            html_parts.append(_html_to_text(payload))
        elif payload:
            plain_parts.append(payload)

    if plain_parts:
        return "\n\n".join(part.strip() for part in plain_parts if part.strip())
    return "\n\n".join(part.strip() for part in html_parts if part.strip())


def _is_attachment_part(part: Message) -> bool:
    disposition = str(part.get_content_disposition() or "").lower()
    if disposition == "attachment":
        return True
    filename = part.get_filename()
    if filename and disposition != "inline":
        return True
    return False


def _decode_part_payload(part: Message) -> str:
    try:
        payload = part.get_payload(decode=True)
    except Exception:
        payload = part.get_payload()

    if payload is None:
        return ""
    if isinstance(payload, bytes):
        charset = part.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")
    if isinstance(payload, str):
        return payload
    return str(payload)


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self._chunks.append(text)

    def get_text(self) -> str:
        return "\n".join(self._chunks)


def _html_to_text(html: str) -> str:
    extractor = _HTMLTextExtractor()
    try:
        extractor.feed(html)
        extractor.close()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)
    return extractor.get_text()


def _extract_email_attachments(
    message: Message,
    *,
    source_file: str,
    start_page: int,
) -> tuple[list[dict], list[str]]:
    pages: list[dict] = []
    names: list[str] = []
    page_number = start_page

    for part in message.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if not _is_attachment_part(part):
            continue

        filename = part.get_filename() or "attachment"
        filename = Path(filename).name
        names.append(filename)

        payload = part.get_payload(decode=True)
        if not payload or not isinstance(payload, (bytes, bytearray)):
            continue

        suffix = suffix_of(filename)
        if suffix not in PDF_EXTENSIONS | DOCX_EXTENSIONS:
            continue

        extracted = _extract_attachment_pages(bytes(payload), filename)
        if not extracted:
            continue

        for item in extracted:
            attachment_text = (
                f"[Attachment: {filename}]\n\n{item['text']}"
            ).strip()
            pages.append(
                _page_record(
                    source_file,
                    page_number,
                    attachment_text,
                    DOCUMENT_TYPE_EMAIL,
                )
            )
            page_number += 1

    return pages, names


def _extract_attachment_pages(content: bytes, filename: str) -> list[dict]:
    suffix = suffix_of(filename)
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / filename
            path.write_bytes(content)
            if suffix in PDF_EXTENSIONS:
                from ul.document_chunking import extract_pages_from_pdf

                return extract_pages_from_pdf(str(path))
            if suffix in DOCX_EXTENSIONS:
                return extract_pages_from_docx(str(path))
    except Exception as exc:
        logger.warning("Failed to extract email attachment %s: %s", filename, exc)
    return []
