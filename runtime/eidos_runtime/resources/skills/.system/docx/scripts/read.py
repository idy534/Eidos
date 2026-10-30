"""Print Word text and tables, without converting or modifying the file."""
import sys

from docx import Document

document = Document(sys.argv[1])
for block in document.iter_inner_content():
    if hasattr(block, "rows"):
        for row in block.rows:
            print(" | ".join(cell.text for cell in row.cells))
    else:
        print(block.text)
