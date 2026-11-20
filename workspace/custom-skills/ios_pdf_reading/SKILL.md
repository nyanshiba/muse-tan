# Read iOS-Generated PDFs

Read PDFs produced by iOS Safari or Shortcuts with PyMuPDF, not poppler (learned 2026-11-07).

- iOS Safari/Shortcuts PDFs use non-embedded CID fonts (Adobe-Japan1). This VM's poppler — `pdftotext`, `pdftoppm`, and the built-in PDF converter — lacks the CMap/language pack and silently drops Japanese glyphs in both text extraction and rendering.

- Extract text and render pages with PyMuPDF instead: create a venv (`python3 -m venv`) and `pip install pymupdf`. Text and rendering come through complete. The venv is ephemeral; recreate it as needed.

- Never conclude an iOS PDF is broken or image-only from a poppler result.
