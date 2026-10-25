"""M8-CP3 (D-065): receiving one workbook safely — services/upload_files.py. SYNTHETIC files only."""
import io
import os
import secrets
import zipfile

import pytest

import dashboard_builder as db
from services import upload_files as UF
from tests.fixtures import fair_synth as FX


@pytest.fixture(scope="module")
def good(tmp_path_factory):
    d = tmp_path_factory.mktemp("upf")
    FX.write_all(str(d))
    return {"stock": (d / FX.STOCK_FILE).read_bytes(), "sales": (d / FX.SALES_FILE).read_bytes()}


def chunks(data: bytes, n=4096):
    for i in range(0, len(data), n):
        yield data[i:i + n]


def receive(tmp_path, data, **kw):
    return UF.BlobStore(str(tmp_path / "up")).receive(chunks(data), **kw)


def zip_bytes(entries, *, compression=zipfile.ZIP_DEFLATED):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as z:
        for name, content in entries:
            z.writestr(name, content)
    return buf.getvalue()


MINIMAL = [("[Content_Types].xml", "<Types/>"), ("xl/workbook.xml", "<workbook/>")]


def code_of(tmp_path, data, **kw):
    with pytest.raises(UF.UploadRejected) as e:
        receive(tmp_path, data, **kw)
    assert e.value.message and "{" not in e.value.message           # fixed Turkish text, never the input
    return e.value.code


def test_a_real_workbook_is_stored_privately_under_a_content_name(tmp_path, good):
    s = receive(tmp_path, good["stock"])
    assert s.name == UF.stored_name(s.sha256) and UF.is_stored_name(s.name) and not s.existed
    assert os.path.dirname(s.path) == str(tmp_path / "up" / "blobs")
    assert oct(os.stat(s.path).st_mode & 0o777) == "0o600"
    assert oct(os.stat(tmp_path / "up" / "blobs").st_mode & 0o777) == "0o700"
    assert open(s.path, "rb").read() == good["stock"]
    assert os.listdir(tmp_path / "up" / "incoming") == []               # no partial file is ever left behind


def test_the_same_content_is_stored_once_whatever_its_name(tmp_path, good):
    a = receive(tmp_path, good["sales"])
    b = receive(tmp_path, good["sales"])
    assert a.sha256 == b.sha256 and a.path == b.path and b.existed
    assert len(os.listdir(tmp_path / "up" / "blobs")) == 1


def test_stored_names_never_carry_meaning_or_match_the_legacy_keyword_selection():
    for _ in range(500):
        n = UF.stored_name(secrets.token_hex(32))
        assert not (db.is_stock(n) or db.is_sales(n) or db.is_aging(n)), n
    assert UF.clean_label("../../etc/STOK 5 Ekim\x00\n.xlsx") == "STOK 5 Ekim.xlsx"
    assert UF.clean_label("C:\\Users\\x\\satış.xlsx") == "satış.xlsx"
    assert len(UF.clean_label("a" * 500)) == UF.LABEL_MAX and UF.clean_label("") == "adsız dosya"


@pytest.mark.parametrize("data,code", [
    (b"", "empty"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600, "not_xlsx"),           # legacy .xls / encrypted OLE container
    ("Stok_Kodu;Stok_Adı\n1;x\n".encode(), "not_xlsx"),                           # CSV renamed to .xlsx
    (b"PK\x03\x04" + b"\x00" * 40, "corrupt"),                                     # truncated archive
])
def test_files_that_are_not_valid_xlsx_are_refused(tmp_path, data, code):
    assert code_of(tmp_path, data) == code
    assert not os.path.exists(tmp_path / "up" / "blobs") or os.listdir(tmp_path / "up" / "blobs") == []


def test_a_truncated_real_workbook_is_refused(tmp_path, good):
    assert code_of(tmp_path, good["sales"][: len(good["sales"]) // 2]) == "corrupt"


def test_a_zip_without_a_workbook_part_is_refused(tmp_path):
    assert code_of(tmp_path, zip_bytes([("[Content_Types].xml", "<Types/>"), ("word/document.xml", "<d/>")])) \
        == "not_xlsx"


@pytest.mark.parametrize("name", ["../evil.xml", "/abs.xml", "xl\\..\\x.xml", "C:/x.xml", "xl/../../x.xml"])
def test_unsafe_part_names_are_refused(tmp_path, name):
    assert code_of(tmp_path, zip_bytes(MINIMAL + [(name, "<x/>")])) == "unsafe_archive"


def test_dtd_and_entity_declarations_are_refused(tmp_path):
    lol = '<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><sst>&b;</sst>'
    assert code_of(tmp_path, zip_bytes(MINIMAL + [("xl/sharedStrings.xml", lol)])) == "unsafe_archive"


def test_a_compression_bomb_is_refused_before_anything_inflates_it(tmp_path):
    bomb = zip_bytes(MINIMAL + [("xl/worksheets/sheet1.xml", b"0" * (40 * 1024 * 1024))])
    assert len(bomb) < 200_000
    assert code_of(tmp_path, bomb) == "expands_too_much"


def test_total_uncompressed_size_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(UF, "MAX_UNCOMPRESSED", 10_000)
    data = zip_bytes(MINIMAL + [("xl/worksheets/sheet1.xml", os.urandom(20_000).hex())])
    assert code_of(tmp_path, data) == "expands_too_much"


def test_encrypted_parts_and_unknown_compression_are_refused(tmp_path):
    raw = bytearray(zip_bytes(MINIMAL, compression=zipfile.ZIP_STORED))
    # set the "encrypted" flag bit on the first entry in the central directory
    cd = raw.rfind(b"PK\x01\x02", 0, len(raw))
    first = raw.find(b"PK\x01\x02")
    raw[first + 8] |= 0x01
    assert code_of(tmp_path, bytes(raw)) == "encrypted"
    assert cd >= first


def test_too_many_parts_are_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(UF, "MAX_PARTS", 5)
    assert code_of(tmp_path, zip_bytes(MINIMAL + [(f"xl/p{i}.xml", "<x/>") for i in range(6)])) == "too_many_parts"


def test_oversized_uploads_are_stopped_while_streaming(tmp_path, monkeypatch, good):
    monkeypatch.setattr(UF, "MAX_BYTES", 1000)
    assert code_of(tmp_path, good["sales"]) == "too_large"
    assert code_of(tmp_path, b"x", declared_length=5000) == "too_large"
    assert os.listdir(tmp_path / "up" / "incoming") == []


def test_verify_detects_a_changed_stored_file(tmp_path, good):
    st = UF.BlobStore(str(tmp_path / "up"))
    s = st.receive(chunks(good["stock"]))
    assert st.verify(s.sha256)
    with open(s.path, "ab") as fh:
        fh.write(b"x")
    assert not st.verify(s.sha256)
