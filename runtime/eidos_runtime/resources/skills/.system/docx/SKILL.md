---
name: docx
description: "Use this skill whenever the user wants to create, read, edit, or manipulate Word documents (.docx files) or Word templates (.dotx files). Triggers include: any mention of 'Word doc', 'word document', '.docx', '.dotx', or requests to produce professional documents with formatting like tables of contents, headings, page numbers, or letterheads. Also use when extracting or reorganizing content from .docx or .dotx files, inserting or replacing images in documents, performing find-and-replace in Word files, working with tracked changes or comments, or converting content into a polished Word document. If the user asks for a 'report', 'memo', 'letter', 'template', or similar deliverable as a Word or .docx file, use this skill. Do NOT use for PDFs, spreadsheets, Google Docs, or general coding tasks unrelated to document generation."
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
"$RUNTIME_PYTHON" "<skill root>/scripts/run.py" --resource office python "<skill root>/scripts/office/convert.py" input.docx output.pdf
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
| Word conversion | `--resource office` |
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


# DOCX creation, editing, and analysis

A `.docx` is a ZIP archive of XML files. Choose your approach by task:

| Task | Approach |
|---|---|
| **Create** a new document | Write a `docx` (npm) script — see gotchas below |
| **Edit** an existing document | `unzip` → edit `word/document.xml` → `zip` (docx-js cannot open existing files) |
| **Read** content | `python scripts/read.py file.docx` |

> Script paths below are relative to this skill's directory.

## Creating with docx-js — gotchas

Eidos provides `docx` through the node resource profile — do not run `npm install` first; write the script and `require('docx')` directly. Use the bundled entry point above. The model knows the API; these are the footguns:

- **Page size defaults to A4.** For US Letter set `page: { size: { width: 12240, height: 15840 } }` (DXA; 1440 = 1″).
- **Landscape:** pass portrait dimensions and `orientation: PageOrientation.LANDSCAPE` — docx-js swaps width/height internally.
- **Tables need dual widths:** set `columnWidths` on the table AND `width` on every cell, both in `WidthType.DXA` (PERCENTAGE breaks in Google Docs). Column widths must sum to the table width.
- **Table shading:** use `ShadingType.CLEAR`, never `SOLID` (renders black).
- **Lists:** never insert `•` literally; use a `numbering` config with `LevelFormat.BULLET`.
- **`ImageRun` requires `type:`** (`"png"`, `"jpg"`, …).
- **`PageBreak` must be inside a `Paragraph`.**
- **Never use `\n`** — use separate `Paragraph` elements.
- **TOC:** headings must use built-in `HeadingLevel.*`; custom heading styles need `outlineLevel` set or they won't appear.
- **Don't use a table as a horizontal rule** — use a paragraph bottom border instead.
- **Dot-leader / right-aligned-on-same-line:** use `PositionalTab` (`alignment: PositionalTabAlignment.RIGHT`, `leader: PositionalTabLeader.DOT`) inside a `TextRun`, not literal `.` or space padding.

## Verify the output

After writing a `.docx`, render it and look at it:

```bash
python scripts/office/convert.py output.docx output.pdf
python scripts/render.py output.pdf pages --format jpeg --dpi 100
ls pages/page-*.jpeg   # then Read the images
```

`render.py` zero-pads page numbers to the width of the page count (`page-01.jpg`…`page-12.jpg`).

## Editing existing documents

Legacy `.doc` files must be converted first: `python scripts/office/convert.py file.doc file.docx`.

```bash
unzip -q doc.docx -d unpacked/
find unpacked -type l -delete   # strip symlink entries — docx from external parties is untrusted
python scripts/merge_runs.py unpacked/   # coalesce fragmented runs so text is findable
# edit unpacked/word/document.xml in place — do NOT reformat or pretty-print
(cd unpacked && rm -f ../out.docx && zip -Xr ../out.docx .)
python scripts/office/validate.py out.docx --original doc.docx   # XSD checks; --auto-repair fixes common issues
# redlining? add --author "<the name you redlined under>" to check every edit is tracked
```

Word splits text across many `<w:r>` runs (revision ids, spell-check markers), so a phrase you can see in the document often doesn't exist as a contiguous string in the XML. `merge_runs.py` merges adjacent identically-formatted runs in `word/document.xml` without changing content or rendering; it also accepts a `.docx` directly (`python scripts/merge_runs.py doc.docx -o merged.docx`).

**Tracked changes:** when redlining, validate with `--author "<the name you redlined under>"` (needs `--original`) — it reports any text you changed without a `<w:ins>`/`<w:del>` around it, which is easy to do by accident and invisible in the accepted view. Wrap runs in `<w:ins>`/`<w:del>` with `w:id`, `w:author`, `w:date` attributes. Inside `<w:del>`, the text element is `<w:delText>`, not `<w:t>`. A deleted paragraph mark (`<w:pPr><w:rPr><w:del w:id=".." w:author=".." w:date=".."/></w:rPr></w:pPr>`) means "merge this paragraph into the next" — so deleting a paragraph outright is that plus a `<w:del>` around every run. The `<w:del/>` must come before the rPr's other children; their order is schema-enforced.

To produce a clean copy with all tracked changes accepted: `python scripts/accept_changes.py in.docx out.docx`.

The acceptance helper handles text insertions/deletions, moved text, property revisions and
deleted paragraph marks directly in OOXML. It preserves the source and writes a separate output.
Validate and render the clean copy, especially for complex tracked table or field edits.

## Comments

Comments require six cross-linked files. Use the helper — directory mode when you'll also be editing `document.xml` (saves an unzip/rezip cycle), `.docx`-direct mode otherwise:

```bash
# Against an already-unpacked directory (preferred when also placing markers)
python scripts/comment.py unpacked/ "Fees & expenses cap is too low"
python scripts/comment.py unpacked/ "Agreed" --parent 0

# Against a .docx directly
python scripts/comment.py contract.docx "This cap is too low" -o annotated.docx
```

The script writes `comments.xml`, `commentsExtended.xml`, `commentsIds.xml`, `commentsExtensible.xml`, the relationships, and the content-type overrides. Comment IDs are auto-assigned. It then prints the `<w:commentRangeStart>`/`<w:commentRangeEnd>`/`<w:commentReference>` snippet to add to `word/document.xml` so the comment anchors to specific text — until you place those markers, the comment exists but is not visible.

## Dependencies

Eidos provides docx through node, python-docx/lxml/defusedxml through python, PDFium through render, and LibreOfficeKit through office. The launcher prepares these managed resources when needed.
Use the bound entry point. Conversion outputs must have a new filename.
