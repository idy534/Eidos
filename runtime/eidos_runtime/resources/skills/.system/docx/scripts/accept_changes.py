"""Accept OOXML text revisions and paragraph deletions without changing the source."""
import argparse
from pathlib import Path
import os
import tempfile
import zipfile

from lxml import etree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MAX_EXPANDED = 128 * 1024 * 1024
MAX_PART = 32 * 1024 * 1024


def accept_part(data: bytes) -> bytes:
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    root = etree.fromstring(data, parser)
    if root.getroottree().docinfo.doctype:
        raise ValueError("Document types are not allowed in OOXML parts")
    deleted_marks = [p for p in root.iter(W + "p") if p.find(f"{W}pPr/{W}rPr/{W}del") is not None]
    for row in list(root.iter(W + "tr")):
        if row.find(f"{W}trPr/{W}del") is not None:
            row.getparent().remove(row)
    for node in list(root.iter()):
        parent = node.getparent()
        if parent is None:
            continue
        local = etree.QName(node).localname
        if node.tag in {W + "del", W + "moveFrom"} or (
            node.tag.startswith(W) and (local.endswith("PrChange") or local in {
                "tblGridChange", "moveFromRangeStart", "moveFromRangeEnd", "moveToRangeStart", "moveToRangeEnd",
            })
        ):
            parent.remove(node)
        elif node.tag in {W + "ins", W + "moveTo"}:
            index = parent.index(node)
            for child in list(node):
                parent.insert(index, child)
                index += 1
            parent.remove(node)
    for paragraph in reversed(deleted_marks):
        parent = paragraph.getparent()
        if parent is None:
            continue
        following = paragraph.getnext()
        if following is not None and following.tag == W + "p":
            if not any(t.text for t in paragraph.iter(W + "t")):
                parent.remove(paragraph)
            else:
                for child in list(following):
                    if child.tag != W + "pPr":
                        paragraph.append(child)
                parent.remove(following)
        elif not any(t.text for t in paragraph.iter(W + "t")):
            parent.remove(paragraph)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def accept_changes(input_file: str, output_file: str) -> tuple[None, str]:
    source, target = Path(input_file).resolve(), Path(output_file).resolve()
    temporary = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(source) as original:
            entries = original.infolist()
            if len(entries) > 4096 or sum(entry.file_size for entry in entries) > MAX_EXPANDED:
                raise ValueError("Document package exceeds the bounded processing size")
            descriptor, name = tempfile.mkstemp(prefix="eidos-accepted-", suffix=".docx", dir=target.parent)
            os.close(descriptor)
            temporary = Path(name)
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as accepted:
                for entry in entries:
                    if entry.file_size > MAX_PART:
                        raise ValueError("Document part exceeds the bounded processing size")
                    data = original.read(entry)
                    if entry.filename.startswith("word/") and entry.filename.endswith(".xml"):
                        data = accept_part(data)
                    accepted.writestr(entry, data)
        os.replace(temporary, target)
        return None, f"Accepted tracked changes: {input_file} -> {output_file}"
    except (OSError, ValueError, zipfile.BadZipFile, etree.XMLSyntaxError) as error:
        return None, f"Error: Changes were not confirmed: {error}"
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_file")
    parser.add_argument("output_file")
    args = parser.parse_args()
    _, message = accept_changes(args.input_file, args.output_file)
    print(message)
    raise SystemExit(1 if message.startswith("Error:") else 0)
