import importlib.util
from pathlib import Path
import zipfile

import pytest

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[1] / "eidos_runtime/resources/skills/.system"


def acceptance():
    pytest.importorskip("lxml")
    spec = importlib.util.spec_from_file_location("document_acceptance", ROOT / "docx/scripts/accept_changes.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_accepts_insert_delete_and_moved_text_without_old_content():
    helper = acceptance()
    xml = b'''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p>
      <w:del><w:r><w:delText>old</w:delText></w:r></w:del>
      <w:ins><w:r><w:t>new</w:t></w:r></w:ins>
      <w:moveFrom><w:r><w:t>before move</w:t></w:r></w:moveFrom>
      <w:moveTo><w:r><w:t>after move</w:t></w:r></w:moveTo>
    </w:p></w:body></w:document>'''
    root = helper.etree.fromstring(helper.accept_part(xml))
    assert [node.text for node in root.iter(helper.W + "t")] == ["new", "after move"]
    assert not list(root.iter(helper.W + "ins"))


def test_deleted_empty_paragraph_does_not_leave_empty_list_item():
    helper = acceptance()
    xml = b'''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
      <w:p><w:pPr><w:rPr><w:del/></w:rPr></w:pPr><w:del><w:r><w:delText>removed item</w:delText></w:r></w:del></w:p>
      <w:p><w:r><w:t>remaining item</w:t></w:r></w:p>
    </w:body></w:document>'''
    root = helper.etree.fromstring(helper.accept_part(xml))
    assert len(list(root.iter(helper.W + "p"))) == 1
    assert list(root.iter(helper.W + "t"))[0].text == "remaining item"


def test_bad_xml_does_not_replace_existing_output_or_source(tmp_path):
    helper = acceptance()
    source, target = tmp_path / "source.docx", tmp_path / "accepted.docx"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("word/document.xml", "<broken")
    original = source.read_bytes()
    target.write_bytes(b"preserve existing output")
    _, message = helper.accept_changes(str(source), str(target))
    assert message.startswith("Error:")
    assert source.read_bytes() == original
    assert target.read_bytes() == b"preserve existing output"
    assert not list(tmp_path.glob("eidos-accepted-*"))


def test_acceptance_rejects_document_types():
    helper = acceptance()
    with pytest.raises(ValueError, match="Document types"):
        helper.accept_part(b'<!DOCTYPE document [<!ENTITY x "value">]><document>&x;</document>')
