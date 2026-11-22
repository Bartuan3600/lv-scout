"""GAEB acceptance through the public import and HTTP boundaries."""
from pathlib import Path
from fastapi.testclient import TestClient
from app.lv.importer import importiere
from app.main import app

FIXTURES = Path(__file__).parent / "fixtures/gaeb"


def test_synthetic_offer_preserves_original_values_and_readable_text():
    raw = (FIXTURES / "offer.x84").read_bytes()
    lv = importiere("offer.x84", raw)
    p, = lv.positionen
    assert (p.oz, p.menge, p.einheit, p.ep, p.gp) == ("01.0010A", 2, "m²", 12.5, 25)
    assert p.kurztext == "Überbau"
    assert p.langtext == "First wall.\nSecond\nline."
    assert p.titel_pfad == ["01 Walls"]
    assert lv.summen == {"01 Walls": 25}
    client = TestClient(app)
    result = client.post("/api/lv/import", files={"datei": ("offer.x84", raw)})
    assert result.status_code == 200
    report = client.post("/api/lv/pruefen", json=result.json())
    assert report.status_code == 200
    assert report.json()["lv"] == result.json()


def test_duplicate_oz_missing_and_zero_values_are_not_repaired():
    xml = b'<GAEB><Award><DP>83</DP><BoQ><BoQBody><Itemlist><Item RNoPart="7"><Qty>0</Qty></Item><Item RNoPart="7"/></Itemlist></BoQBody></BoQ></Award></GAEB>'
    lv = importiere("empty.x83", xml)
    assert [p.oz for p in lv.positionen] == ["7", "7"]
    assert [p.menge for p in lv.positionen] == [0, None]
    assert all(p.ep is None and p.gp is None and p.einheit is None for p in lv.positionen)


def test_invalid_xml_and_numbers_return_safe_http_errors():
    import pytest
    bad = [b"secret-document-text", b"<wrong/>", b"<GAEB/>",
           b'<!DOCTYPE GAEB [<!ENTITY x "secret-document-text">]><GAEB>&x;</GAEB>']
    raw = (FIXTURES / "offer.x84").read_bytes()
    bad += [raw.replace(b"2.000", value) for value in (b"NaN", b"Infinity", b"bad")]
    for content in bad:
        with pytest.raises(ValueError) as error:
            importiere("broken.x83", content)
        assert "secret-document-text" not in str(error.value)
        result = TestClient(app).post("/api/lv/import", files={"datei": ("broken.x83", content)})
        assert result.status_code == 422
        assert "secret-document-text" not in result.text


def test_legacy_gaeb_preserves_scaled_values_and_original_lines():
    from app.lv.gaeb import parse_gaeb
    def record(kind, fields):
        line = list(" " * 80)
        line[:2] = kind
        for start, text in fields:
            line[start:start + len(text)] = text
        return "".join(line)
    raw = "\r\n".join([
        record("00", [(10, "84"), (62, "1122PPPPI"), (72, "90")]),
        record("21", [(2, "01010001 "), (11, "NN"), (23, "00000002000"), (34, "m2")]),
        record("23", [(2, "01010001 "), (12, "00000012500"), (24, "000000002500")]),
        record("25", [(2, "Synthetic wall")]),
        record("26", [(2, "Überbau with no lost text")]),
        record("99", []),
    ]).encode("cp850")
    doc = parse_gaeb("offer.d84", raw)
    p, = doc.positions
    assert (p.oz.value, p.quantity.value, p.unit_price.value, p.total_price.value) == ("01.01.0001", "2.000", "12.500", "25.00")
    assert p.unit_price.original == "00000012500"
    assert p.unit_price.sources[0].line == 3
    assert p.detailed_text.value == "Überbau with no lost text"


def _legacy_bytes():
    def record(kind, fields):
        line = list(" " * 80)
        line[:2] = kind
        for start, text in fields:
            line[start:start + len(text)] = text
        return "".join(line)
    return "\r\n".join([
        record("00", [(10, "83"), (62, "1122PPPPI"), (72, "90")]),
        record("21", [(2, "01010001 "), (11, "NN"), (23, "00000002000"), (34, "m2")]),
        record("25", [(2, "Synthetic wall")]),
        record("99", []),
    ]).encode("cp850")


def test_d83_and_d86_are_accepted_by_dispatcher_and_http_route():
    raw = _legacy_bytes()
    client = TestClient(app)
    for name in ("offer.d83", "OFFER.D86"):
        lv = importiere(name, raw)
        assert [p.oz for p in lv.positionen] == ["01.01.0001"]
        result = client.post("/api/lv/import", files={"datei": (name, raw)})
        assert result.status_code == 200
        assert result.json()["positionen"][0]["oz"] == "01.01.0001"


def test_import_route_runs_importer_in_threadpool(monkeypatch):
    import threading
    import app.main as main
    seen = {}
    real = main.importiere
    def spy(name, content):
        seen["thread"] = threading.current_thread()
        return real(name, content)
    monkeypatch.setattr(main, "importiere", spy)
    result = TestClient(app).post("/api/lv/import", files={"datei": ("a.d83", _legacy_bytes())})
    assert result.status_code == 200
    assert seen["thread"] is not threading.main_thread()
