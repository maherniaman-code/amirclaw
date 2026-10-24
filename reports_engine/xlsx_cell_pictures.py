"""Pictures placed inside cells (Excel's "Place in Cell"), added to a workbook openpyxl has saved.

openpyxl can only float a picture over the sheet: it stays put when rows are filtered or sorted,
so pictures pile up on the wrong rows (owner, 24-10). Excel 365 keeps an in-cell picture as a rich
value of the cell itself, so it moves, hides and sorts with its row. The parts written here are the
ones Excel writes (same layout as XlsxWriter's embed_image). Older Excel and other spreadsheet apps
show #VALUE! in those cells.
"""

import hashlib
import re
import zipfile
from pathlib import Path
from typing import Dict, List
from xml.sax.saxutils import escape

PLACEHOLDER = "Picture"   # value openpyxl writes in the cell; replaced by the picture

_RELS = [
    ("http://schemas.openxmlformats.org/officeDocument/2006/relationships/sheetMetadata", "metadata.xml"),
    ("http://schemas.microsoft.com/office/2022/10/relationships/richValueRel", "richData/richValueRel.xml"),
    ("http://schemas.microsoft.com/office/2017/06/relationships/rdRichValue", "richData/rdrichvalue.xml"),
    ("http://schemas.microsoft.com/office/2017/06/relationships/rdRichValueStructure", "richData/rdrichvaluestructure.xml"),
    ("http://schemas.microsoft.com/office/2017/06/relationships/rdRichValueTypes", "richData/rdRichValueTypes.xml"),
]
_TYPES = {
    "/xl/metadata.xml": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheetMetadata+xml",
    "/xl/richData/rdRichValueTypes.xml": "application/vnd.ms-excel.rdrichvaluetypes+xml",
    "/xl/richData/rdrichvalue.xml": "application/vnd.ms-excel.rdrichvalue+xml",
    "/xl/richData/rdrichvaluestructure.xml": "application/vnd.ms-excel.rdrichvaluestructure+xml",
    "/xl/richData/richValueRel.xml": "application/vnd.ms-excel.richvaluerel+xml",
}
_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
_KEYS = ["_Self", "_DisplayString", "_Flags", "_Format", "_SubLabel", "_Attribution", "_Icon", "_Display",
         "_CanonicalPropertyNames", "_ClassificationId"]


def _types_xml() -> str:
    keys = "".join(
        f'<key name="{k}">' + ('<flag name="ExcludeFromFile" value="1"/>' if k == "_Self" else "")
        + '<flag name="ExcludeFromCalcComparison" value="1"/></key>' for k in _KEYS)
    return (_HEAD + '<rvTypesInfo xmlns="http://schemas.microsoft.com/office/spreadsheetml/2017/richdata2" '
            'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" mc:Ignorable="x" '
            'xmlns:x="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f"<global><keyFlags>{keys}</keyFlags></global></rvTypesInfo>")


def _as_jpeg(data: bytes) -> bytes:
    if data[:3] == b"\xff\xd8\xff":
        return data
    import io
    from PIL import Image
    out = io.BytesIO()
    Image.open(io.BytesIO(data)).convert("RGB").save(out, "JPEG", quality=85)
    return out.getvalue()


def _sheet_with_pictures(xml: str, cells: Dict[str, int]) -> str:
    """Turn each placeholder cell into an in-cell picture (t="e" vm="n", like Excel writes it)."""
    def repl(m: "re.Match[str]") -> str:
        ref = m.group(1)
        if ref not in cells:
            return m.group(0)
        style = re.search(r'\ss="\d+"', m.group(2) or "")
        return f'<c r="{ref}"{style.group(0) if style else ""} t="e" vm="{cells[ref]}"><v>#VALUE!</v></c>'
    return re.sub(r'<c r="([A-Z]+\d+)"([^>]*?)(?:/>|>.*?</c>)', repl, xml, flags=re.S)


def place_pictures_in_cells(xlsx_path: Path, pictures: Dict[int, Dict[str, bytes]]) -> int:
    """
    pictures: {sheet number (1 = first sheet): {"G2": jpeg bytes, ...}}. Each cell must hold
    PLACEHOLDER (written with openpyxl before saving). Returns the number of cells filled.
    """
    pictures = {n: cells for n, cells in pictures.items() if cells}
    if not pictures:
        return 0
    xlsx_path = Path(xlsx_path)
    with zipfile.ZipFile(xlsx_path) as z:
        parts = {name: z.read(name) for name in z.namelist()}

    # One rich value per distinct picture; cells with the same picture share it
    images: List[bytes] = []
    by_hash: Dict[str, int] = {}
    cell_vm: Dict[int, Dict[str, int]] = {}
    for sheet_no, cells in pictures.items():
        for ref, data in cells.items():
            data = _as_jpeg(data)
            h = hashlib.sha1(data).hexdigest()
            if h not in by_hash:
                by_hash[h] = len(images)
                images.append(data)
            cell_vm.setdefault(sheet_no, {})[ref] = by_hash[h] + 1   # vm is 1-based
    filled = 0
    for sheet_no, cells in cell_vm.items():
        name = f"xl/worksheets/sheet{sheet_no}.xml"
        xml = parts[name].decode("utf-8")
        new = _sheet_with_pictures(xml, cells)
        filled += new.count(' t="e" vm="') - xml.count(' t="e" vm="')
        parts[name] = new.encode("utf-8")

    n = len(images)
    for i, data in enumerate(images, 1):
        parts[f"xl/media/cellpicture{i}.jpeg"] = data
    parts["xl/richData/_rels/richValueRel.xml.rels"] = (
        _HEAD + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
                  f'Target="../media/cellpicture{i}.jpeg"/>' for i in range(1, n + 1))
        + "</Relationships>").encode()
    parts["xl/richData/richValueRel.xml"] = (
        _HEAD + '<richValueRels xmlns="http://schemas.microsoft.com/office/spreadsheetml/2022/richvaluerel" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        + "".join(f'<rel r:id="rId{i}"/>' for i in range(1, n + 1)) + "</richValueRels>").encode()
    parts["xl/richData/rdrichvalue.xml"] = (
        _HEAD + f'<rvData xmlns="http://schemas.microsoft.com/office/spreadsheetml/2017/richdata" count="{n}">'
        + "".join(f'<rv s="0"><v>{i}</v><v>5</v></rv>' for i in range(n)) + "</rvData>").encode()
    parts["xl/richData/rdrichvaluestructure.xml"] = (
        _HEAD + '<rvStructures xmlns="http://schemas.microsoft.com/office/spreadsheetml/2017/richdata" count="1">'
        '<s t="_localImage"><k n="_rvRel:LocalImageIdentifier" t="i"/><k n="CalcOrigin" t="i"/></s></rvStructures>').encode()
    parts["xl/richData/rdRichValueTypes.xml"] = _types_xml().encode()
    parts["xl/metadata.xml"] = (
        _HEAD + '<metadata xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:xlrd="http://schemas.microsoft.com/office/spreadsheetml/2017/richdata">'
        '<metadataTypes count="1"><metadataType name="XLRICHVALUE" minSupportedVersion="120000" copy="1" pasteAll="1" '
        'pasteValues="1" merge="1" splitFirst="1" rowColShift="1" clearFormats="1" clearComments="1" assign="1" coerce="1"/>'
        f'</metadataTypes><futureMetadata name="XLRICHVALUE" count="{n}">'
        + "".join(f'<bk><extLst><ext uri="{{3e2802c4-a4d2-4d8b-9148-e3be6c30e623}}"><xlrd:rvb i="{i}"/></ext></extLst></bk>'
                  for i in range(n))
        + f'</futureMetadata><valueMetadata count="{n}">'
        + "".join(f'<bk><rc t="1" v="{i}"/></bk>' for i in range(n)) + "</valueMetadata></metadata>").encode()

    rels = parts["xl/_rels/workbook.xml.rels"].decode("utf-8")
    rels = rels.replace("</Relationships>", "".join(
        f'<Relationship Id="rIdCellPic{i}" Type="{escape(t)}" Target="{target}"/>' for i, (t, target) in enumerate(_RELS, 1))
        + "</Relationships>")
    parts["xl/_rels/workbook.xml.rels"] = rels.encode("utf-8")

    types = parts["[Content_Types].xml"].decode("utf-8")
    add = ""
    if 'Extension="jpeg"' not in types:
        add += '<Default Extension="jpeg" ContentType="image/jpeg"/>'
    add += "".join(f'<Override PartName="{p}" ContentType="{ct}"/>' for p, ct in _TYPES.items() if p not in types)
    parts["[Content_Types].xml"] = types.replace("</Types>", add + "</Types>").encode("utf-8")

    tmp = xlsx_path.with_suffix(".tmp.xlsx")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        names = ["[Content_Types].xml"] + [k for k in parts if k != "[Content_Types].xml"]
        for name in names:
            z.writestr(name, parts[name])
    tmp.replace(xlsx_path)
    return filled
