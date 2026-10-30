"""Print slide text and notes, without converting or modifying the file."""
import sys

from pptx import Presentation

presentation = Presentation(sys.argv[1])
for index, slide in enumerate(presentation.slides, 1):
    print(f"## Slide {index}")
    for shape in slide.shapes:
        if shape.has_text_frame:
            print(shape.text)
        if shape.has_table:
            for row in shape.table.rows:
                print(" | ".join(cell.text for cell in row.cells))
    if slide.has_notes_slide:
        print(slide.notes_slide.notes_text_frame.text)
