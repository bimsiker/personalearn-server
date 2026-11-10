"""Legacy Word .doc (Word 97-2003 binary) text extraction, following [MS-DOC].

The file is an OLE compound file. Its text lives in pieces described by the "piece
table" (the Clx structure in the 0Table/1Table stream), located through the File
Information Block (FIB) at the start of the WordDocument stream. We only read text;
nothing in the file is executed. Encrypted files and files carrying macros are refused.

Also handles the case where an *encrypted .docx* arrives: Word wraps those in an OLE
container with an "EncryptedPackage" stream.
"""

import io
import logging
import struct

import olefile

from app.services.documents.common import (
    SUPPORTED_HINT,
    DocumentError,
    ExtractedDocument,
    finish,
    macros_not_allowed,
    password_protected,
)

logger = logging.getLogger(__name__)

FIB_MAGIC = 0xA5EC
FLAG_ENCRYPTED = 0x0100
FLAG_TABLE_1 = 0x0200
CLX_PAIR_INDEX = 33  # fcClx/lcbClx in FibRgFcLcb97
CCP_TEXT_INDEX = 3  # ccpText in FibRgLw97: length of the main document in characters
COMPRESSED_BIT = 0x40000000

# Word's special characters -> plain text.
_SPECIAL = {
    "\r": "\n",  # paragraph end
    "\x07": "\t",  # table cell / row end
    "\x0b": "\n",  # manual line break
    "\x0c": "\n",  # page / section break
    "\x1e": "-",  # non-breaking hyphen
    "\x1f": "",  # optional hyphen
    "\xa0": " ",
}


def _invalid() -> DocumentError:
    return DocumentError(
        "INVALID_DOCUMENT", "We couldn't read that Word document. It may be damaged."
    )


def _strip_fields(text: str) -> str:
    """Fields look like \\x13 code \\x14 result \\x15; keep only the visible result."""
    out: list[str] = []
    stack: list[bool] = []  # per open field: are we past its separator?
    for ch in text:
        if ch == "\x13":
            stack.append(False)
        elif ch == "\x14":
            if stack:
                stack[-1] = True
        elif ch == "\x15":
            if stack:
                stack.pop()
        elif not stack or all(stack):
            out.append(ch)
    return "".join(out)


def _to_plain(text: str) -> str:
    text = _strip_fields(text)
    text = text.replace("\x07\x07", "\n")  # cell end + row end -> one row per line
    text = "".join(_SPECIAL.get(ch, ch) for ch in text)
    # Drop any remaining control characters except newlines and tabs.
    return "".join(ch for ch in text if ch in "\n\t" or ord(ch) >= 0x20)


def _read_pieces(word: bytes, table: bytes) -> str:
    csw = struct.unpack_from("<H", word, 32)[0]
    lw_start = 32 + 2 + csw * 2
    cslw = struct.unpack_from("<H", word, lw_start)[0]
    rglw = lw_start + 2
    ccp_text = struct.unpack_from("<i", word, rglw + CCP_TEXT_INDEX * 4)[0]
    fclcb_start = rglw + cslw * 4 + 2
    fc_clx, lcb_clx = struct.unpack_from("<II", word, fclcb_start + CLX_PAIR_INDEX * 8)
    if lcb_clx == 0 or fc_clx + lcb_clx > len(table):
        raise ValueError("bad Clx location")

    clx = table[fc_clx : fc_clx + lcb_clx]
    pos = 0
    while pos < len(clx) and clx[pos] == 0x01:  # skip Prc entries (formatting)
        pos += 3 + struct.unpack_from("<h", clx, pos + 1)[0]
    if pos >= len(clx) or clx[pos] != 0x02:
        raise ValueError("piece table not found")
    lcb = struct.unpack_from("<I", clx, pos + 1)[0]
    plc = clx[pos + 5 : pos + 5 + lcb]
    count = (len(plc) - 4) // 12
    if count <= 0:
        raise ValueError("empty piece table")

    cps = struct.unpack_from(f"<{count + 1}I", plc, 0)
    pieces: list[str] = []
    for i in range(count):
        length = cps[i + 1] - cps[i]
        if length <= 0:
            continue
        fc_raw = struct.unpack_from("<I", plc, (count + 1) * 4 + i * 8 + 2)[0]
        if fc_raw & COMPRESSED_BIT:
            start = (fc_raw & ~COMPRESSED_BIT) // 2
            pieces.append(word[start : start + length].decode("cp1252", errors="replace"))
        else:
            start = fc_raw
            pieces.append(word[start : start + 2 * length].decode("utf-16-le", errors="replace"))
    text = "".join(pieces)
    return text[:ccp_text] if 0 < ccp_text <= len(text) else text


def extract_doc(data: bytes, *, max_chars: int) -> ExtractedDocument:
    try:
        ole = olefile.OleFileIO(io.BytesIO(data))
    except Exception as exc:
        raise _invalid() from exc

    try:
        if ole.exists("EncryptedPackage"):
            raise password_protected()  # an encrypted .docx in an OLE wrapper
        if not ole.exists("WordDocument"):
            # Another OLE format: old .xls/.ppt/.msg, etc.
            raise DocumentError(
                "UNSUPPORTED_FILE_TYPE", f"That file type isn't supported. {SUPPORTED_HINT}"
            )
        if ole.exists("Macros"):
            raise macros_not_allowed()

        word = ole.openstream("WordDocument").read()
        ident, _nfib = struct.unpack_from("<HH", word, 0)
        flags = struct.unpack_from("<H", word, 0x0A)[0]
        if ident != FIB_MAGIC:
            raise _invalid()
        if flags & FLAG_ENCRYPTED:
            raise password_protected()
        table_name = "1Table" if flags & FLAG_TABLE_1 else "0Table"
        if not ole.exists(table_name):
            raise _invalid()
        table = ole.openstream(table_name).read()
        raw = _read_pieces(word, table)

        try:
            pages = ole.get_metadata().num_pages
        except Exception:
            pages = None
    except DocumentError:
        raise
    except Exception as exc:
        logger.warning("Unreadable DOC: %s", type(exc).__name__)
        raise _invalid() from exc
    finally:
        ole.close()

    return finish("doc", _to_plain(raw), pages if pages and pages > 0 else None, max_chars)
