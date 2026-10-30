---
name: pdf
description: Use this skill whenever the user wants to do anything with PDF files. This includes reading or extracting text/tables from PDFs, combining or merging multiple PDFs into one, splitting PDFs apart, rotating pages, adding watermarks, creating new PDFs, filling PDF forms, encrypting/decrypting PDFs, extracting images, and OCR on scanned PDFs to make them searchable. If the user mentions a .pdf file or asks to produce one, use this skill.
---

## Eidos execution environment

Eidos manages interpreters and versioned document resources. Use the Runtime-provided
paths; do not use host Python/Node, global npm/pip packages, or install packages yourself.
Resolve `<skill root>` to this Skill's canonical directory from the catalog.
Keep cwd in the Run workspace. Put generated scripts, outputs and temporary files there.
Run every example through this Skill's launcher using its absolute path:

```bash
"$RUNTIME_PYTHON" "<skill root>/scripts/run.py" python generated.py
"$RUNTIME_PYTHON" "<skill root>/scripts/run.py" node generated.js
"$RUNTIME_PYTHON" "<skill root>/scripts/run.py" python "<skill root>/scripts/HELPER.py" arguments
"$RUNTIME_PYTHON" "<skill root>/scripts/run.py" --resource ocr-eng node "<skill root>/scripts/ocr.cjs" page.png eng searchable.pdf
"$RUNTIME_PYTHON" "<skill root>/scripts/run.py" --resource render python "<skill root>/scripts/render.py" output.pdf pages
```

The launcher lets Eidos select and prepare the declared resource profile **before**
the command executes. A first use may wait for a background download; other Eidos tasks
remain available. Run the command instead of asking the user to install dependencies.
If preparation fails, report the resource error and offer a retry or an available
alternative. Do not replace interpreters, fetch packages, or claim unrun rendering succeeded.

| Operation | Launcher resource selector |
|---|---|
| Basic Python reading/editing | none; `python SCRIPT` |
| JavaScript generation with docx/pptxgenjs | none; `node SCRIPT` |
| PDF page images through PDFium | `--resource render` |
| PDF generation/table extraction, document fonts | `--resource pdf` |
| pandas/NumPy data analysis | `--resource data` |
| sharp image conversion/SVG rasterization | `--resource images` |
| OCR with English / Chinese / both | `--resource ocr-eng` / `ocr-chi-sim` / `ocr-bilingual` |

Replace relative `scripts/...` paths below with canonical absolute Skill paths and
apply the selector for that operation. Do not hard-code resource versions or search
`~/Eidos/Resource/` yourself. Eidos supplies `RUNTIME_PYTHON`, `RUNTIME_NODE`, Python
import roots and a Node loader for both CJS and ESM. The profiles which need fonts
also receive `EIDOS_DOCUMENT_FONTS`. Managed resources are read-only in the ordinary
sandbox; all document writes belong in the workspace. Resource downloads do not grant
the Shell network access.


# PDF Processing Guide

## Overview

This guide covers essential PDF processing operations using Python libraries and command-line tools. For advanced features, JavaScript libraries, and detailed examples, see reference.md. If you need to fill out a PDF form, read forms.md and follow its instructions.

## Quick Start

```python
from pypdf import PdfReader, PdfWriter

# Read a PDF
reader = PdfReader("document.pdf")
print(f"Pages: {len(reader.pages)}")

# Extract text
text = ""
for page in reader.pages:
    text += page.extract_text()
```

## Python Libraries

### pypdf - Basic Operations

#### Merge PDFs
```python
from pypdf import PdfWriter, PdfReader

writer = PdfWriter()
for pdf_file in ["doc1.pdf", "doc2.pdf", "doc3.pdf"]:
    reader = PdfReader(pdf_file)
    for page in reader.pages:
        writer.add_page(page)

with open("merged.pdf", "wb") as output:
    writer.write(output)
```

#### Split PDF
```python
reader = PdfReader("input.pdf")
for i, page in enumerate(reader.pages):
    writer = PdfWriter()
    writer.add_page(page)
    with open(f"page_{i+1}.pdf", "wb") as output:
        writer.write(output)
```

#### Extract Metadata
```python
reader = PdfReader("document.pdf")
meta = reader.metadata
print(f"Title: {meta.title}")
print(f"Author: {meta.author}")
print(f"Subject: {meta.subject}")
print(f"Creator: {meta.creator}")
```

#### Rotate Pages
```python
reader = PdfReader("input.pdf")
writer = PdfWriter()

page = reader.pages[0]
page.rotate(90)  # Rotate 90 degrees clockwise
writer.add_page(page)

with open("rotated.pdf", "wb") as output:
    writer.write(output)
```

### pdfplumber - Text and Table Extraction

#### Extract Text with Layout
```python
import pdfplumber

with pdfplumber.open("document.pdf") as pdf:
    for page in pdf.pages:
        text = page.extract_text()
        print(text)
```

#### Extract Tables
```python
with pdfplumber.open("document.pdf") as pdf:
    for i, page in enumerate(pdf.pages):
        tables = page.extract_tables()
        for j, table in enumerate(tables):
            print(f"Table {j+1} on page {i+1}:")
            for row in table:
                print(row)
```

#### Advanced Table Extraction
```python
import pandas as pd

with pdfplumber.open("document.pdf") as pdf:
    all_tables = []
    for page in pdf.pages:
        tables = page.extract_tables()
        for table in tables:
            if table:  # Check if table is not empty
                df = pd.DataFrame(table[1:], columns=table[0])
                all_tables.append(df)

# Combine all tables
if all_tables:
    combined_df = pd.concat(all_tables, ignore_index=True)
    combined_df.to_excel("extracted_tables.xlsx", index=False)
```

### reportlab - Create PDFs

#### Basic PDF Creation
```python
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

c = canvas.Canvas("hello.pdf", pagesize=letter)
width, height = letter

# Add text
c.drawString(100, height - 100, "Hello World!")
c.drawString(100, height - 120, "This is a PDF created with reportlab")

# Add a line
c.line(100, height - 140, 400, height - 140)

# Save
c.save()
```

#### Create PDF with Multiple Pages
```python
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet

doc = SimpleDocTemplate("report.pdf", pagesize=letter)
styles = getSampleStyleSheet()
story = []

# Add content
title = Paragraph("Report Title", styles['Title'])
story.append(title)
story.append(Spacer(1, 12))

body = Paragraph("This is the body of the report. " * 20, styles['Normal'])
story.append(body)
story.append(PageBreak())

# Page 2
story.append(Paragraph("Page 2", styles['Heading1']))
story.append(Paragraph("Content for page 2", styles['Normal']))

# Build PDF
doc.build(story)
```

#### Subscripts and Superscripts

**IMPORTANT**: Never use Unicode subscript/superscript characters (₀₁₂₃₄₅₆₇₈₉, ⁰¹²³⁴⁵⁶⁷⁸⁹) in ReportLab PDFs. The built-in fonts do not include these glyphs, causing them to render as solid black boxes.

Instead, use ReportLab's XML markup tags in Paragraph objects:
```python
from reportlab.platypus import Paragraph
from reportlab.lib.styles import getSampleStyleSheet

styles = getSampleStyleSheet()

# Subscripts: use <sub> tag
chemical = Paragraph("H<sub>2</sub>O", styles['Normal'])

# Superscripts: use <super> tag
squared = Paragraph("x<super>2</super> + y<super>2</super>", styles['Normal'])
```

For canvas-drawn text (not Paragraph objects), manually adjust font the size and position rather than using Unicode subscripts/superscripts.

## Rendering and OCR

Use `scripts/render.py document.pdf pages --dpi 150 --start 1 --end 20`
for page images. Use pypdf/pdfplumber for text, page manipulation and encryption;
Poppler, qpdf and pdftk are not part of this environment.

## Common Tasks

### Extract Text from Scanned PDFs

Render a bounded page range with `scripts/render.py`, then use the bundled
Tesseract.js helper through the entry point. It supports English and simplified Chinese,
with local language data and no downloads:

```bash
"$RUNTIME_PYTHON" "<skill root>/scripts/run.py" node "<skill root>/scripts/ocr.cjs" pages/page-1.png eng+chi_sim
```

An optional fourth argument saves a searchable PDF for that image. For a multipage
result, OCR each rendered page and combine the page PDFs with pypdf. OCR can be wrong;
check important numbers against the source image.

### Add Watermark
```python
from pypdf import PdfReader, PdfWriter

# Create watermark (or load existing)
watermark = PdfReader("watermark.pdf").pages[0]

# Apply to all pages
reader = PdfReader("document.pdf")
writer = PdfWriter()

for page in reader.pages:
    page.merge_page(watermark)
    writer.add_page(page)

with open("watermarked.pdf", "wb") as output:
    writer.write(output)
```

### Extract Images
```bash
# Using pdfimages (poppler-utils)
pdfimages -j input.pdf output_prefix

# This extracts all images as output_prefix-000.jpg, output_prefix-001.jpg, etc.
```

### Password Protection
```python
from pypdf import PdfReader, PdfWriter

reader = PdfReader("input.pdf")
writer = PdfWriter()

for page in reader.pages:
    writer.add_page(page)

# Add password
writer.encrypt("userpassword", "ownerpassword")

with open("encrypted.pdf", "wb") as output:
    writer.write(output)
```

## Quick Reference

| Task | Best Tool | Command/Code |
|------|-----------|--------------|
| Merge PDFs | pypdf | `writer.add_page(page)` |
| Split PDFs | pypdf | One page per file |
| Extract text | pdfplumber | `page.extract_text()` |
| Extract tables | pdfplumber | `page.extract_tables()` |
| Create PDFs | reportlab | Canvas or Platypus |
| Merge pages | pypdf | `PdfWriter.append` |
| OCR scanned PDFs | Tesseract.js helper | Render pages with PDFium first |
| Fill PDF forms | pdf-lib or pypdf (see FORMS.md) | See FORMS.md |

## Next Steps

- For advanced pypdfium2 usage, see reference.md
- For JavaScript libraries (pdf-lib), see reference.md
- If you need to fill out a PDF form, follow the instructions in FORMS.md
- For troubleshooting guides, see reference.md
