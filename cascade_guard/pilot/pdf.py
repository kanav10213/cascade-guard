"""PDF invoices: text out of a PDF, and a small sample-PDF writer.

    text = pdf_to_text(pdf_bytes)          # uses pypdf
    pdf_bytes = text_to_pdf(invoice_text)   # a plain one-page PDF, no extra library

A PDF laid out like the sample (one item per line, "Q x NAME @ $PRICE") is read by
the rule agents. Other layouts need the LLM agents (agents="llm"), which read the
extracted text whatever its layout; the checks stay the same.
"""
import io
import re

MAX_PDF_BYTES = 5 * 1024 * 1024


class PDFError(ValueError):
    pass


def pdf_to_text(data: bytes) -> str:
    if len(data) > MAX_PDF_BYTES:
        raise PDFError("PDF is larger than 5 MB")
    if not data.startswith(b"%PDF"):
        raise PDFError("not a PDF file")
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise PDFError("PDF support needs the pypdf package: pip install pypdf") from e
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise PDFError("the PDF is password-protected")
        pages = [p.extract_text() or "" for p in reader.pages[:20]]
    except PDFError:
        raise
    except Exception as e:
        raise PDFError(f"could not read the PDF: {type(e).__name__}") from e
    text = "\n".join(pages)
    text = "\n".join(re.sub(r"[ \t]+", " ", line).rstrip() for line in text.splitlines())
    if not text.strip():
        raise PDFError("the PDF has no text layer (a scanned image needs OCR first)")
    return text


def _escape(s: str) -> str:
    s = s.encode("latin-1", "replace").decode("latin-1")
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def text_to_pdf(text: str, title: str = "Invoice") -> bytes:
    """A one-page A4 PDF with the text in Helvetica, one line per line."""
    lines = text.splitlines()[:60]
    ops = ["BT", "/F1 11 Tf", "14 TL", "56 790 Td"]
    for i, line in enumerate(lines):
        if i == 0:
            ops += ["/F2 16 Tf", f"({_escape(line)}) Tj", "T*", "/F1 11 Tf", "T*"]
        else:
            ops += [f"({_escape(line)}) Tj", "T*"]
    ops.append("ET")
    stream = "\n".join(ops).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
        b"<< /Title (" + _escape(title).encode("latin-1") + b") /Producer (Cascade Guard) >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for n, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(f"{n} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info {len(objects)} 0 R >>\n"
              f"startxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()
