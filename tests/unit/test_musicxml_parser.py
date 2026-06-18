from __future__ import annotations

import hashlib
import io
import zipfile
from types import SimpleNamespace
from typing import cast

import pytest

from piano_hand.errors import ErrorCode, PianoHandError
from piano_hand.models import FingerSource
from piano_hand.parsers import musicxml_parser, parse_score
from piano_hand.parsers.musicxml_parser import parse_musicxml

MUSICXML = """\
<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0">
  <part-list>
    <score-part id="P1"><part-name>Piano</part-name></score-part>
  </part-list>
  <part id="P1">
    <measure number="1">
      <attributes>
        <divisions>2</divisions>
        <time><beats>3</beats><beat-type>4</beat-type></time>
        <staves>2</staves>
      </attributes>
      <direction><sound tempo="60"/></direction>
      <note>
        <pitch><step>C</step><octave>4</octave></pitch>
        <duration>6</duration><voice>1</voice><staff>1</staff>
        <tie type="start"/>
        <notations><technical><fingering>1</fingering></technical></notations>
      </note>
      <note>
        <chord/><pitch><step>E</step><octave>4</octave></pitch>
        <duration>6</duration><voice>1</voice><staff>1</staff>
      </note>
      <backup><duration>6</duration></backup>
      <note>
        <pitch><step>C</step><octave>3</octave></pitch>
        <duration>6</duration><voice>2</voice><staff>2</staff>
      </note>
    </measure>
    <measure number="2">
      <direction>
        <direction-type>
          <metronome><beat-unit>quarter</beat-unit><per-minute>120</per-minute></metronome>
        </direction-type>
      </direction>
      <note>
        <pitch><step>C</step><octave>4</octave></pitch>
        <duration>2</duration><voice>1</voice><staff>1</staff>
        <tie type="stop"/>
      </note>
      <note>
        <grace/><pitch><step>D</step><octave>5</octave></pitch>
        <voice>1</voice><staff>1</staff>
      </note>
      <note>
        <pitch><step>D</step><octave>4</octave></pitch>
        <duration>2</duration><voice>1</voice><staff>1</staff>
        <notations><ornaments><trill-mark/></ornaments></notations>
      </note>
    </measure>
  </part>
</score-partwise>
"""


def write_musicxml(path) -> None:
    path.write_text(MUSICXML, encoding="utf-8")


def test_musicxml_parses_chords_ties_voices_staff_tempo_and_fingering(tmp_path) -> None:
    source = tmp_path / "score.musicxml"
    write_musicxml(source)

    timeline = parse_musicxml(source)
    c4 = next(note for note in timeline.notes if note.pitch == 60)
    e4 = next(note for note in timeline.notes if note.pitch == 64)
    c3 = next(note for note in timeline.notes if note.pitch == 48)
    d4 = next(note for note in timeline.notes if note.pitch == 62)

    assert len(timeline.notes) == 4
    assert c4.duration_beat == pytest.approx(4.0)
    assert c4.duration_sec == pytest.approx(3.5)
    assert c4.finger == 1
    assert c4.finger_source == FingerSource.SCORE
    assert e4.onset_beat == c4.onset_beat == pytest.approx(0.0)
    assert c3.voice == "2"
    assert c3.staff == 2
    assert d4.onset_beat == pytest.approx(4.0)
    assert any("grace note" in item for item in d4.explanation)
    assert any("ornament" in item for item in d4.explanation)
    assert [(change.beat, change.bpm) for change in timeline.tempo_map] == [
        (0.0, 60.0),
        (3.0, 120.0),
    ]
    assert timeline.time_signatures[0].model_dump() == {
        "measure": 1,
        "numerator": 3,
        "denominator": 4,
    }


def test_mxl_container_is_supported_and_hashes_original_archive(tmp_path) -> None:
    source = tmp_path / "score.mxl"
    container = """\
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="scores/main.musicxml"/></rootfiles>
</container>
"""
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("scores/main.musicxml", MUSICXML)

    timeline = parse_score(source)

    assert timeline.source.type == "musicxml"
    assert timeline.source.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert len(timeline.notes) == 4


def test_mxl_rejects_path_traversal_entries(tmp_path) -> None:
    source = tmp_path / "traversal.mxl"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("../outside.musicxml", MUSICXML)

    with pytest.raises(PianoHandError) as error:
        parse_musicxml(source)

    assert error.value.code == ErrorCode.INPUT_ERROR
    assert "unsafe archive member path" in error.value.message


def test_mxl_rejects_encrypted_entries(tmp_path) -> None:
    source = tmp_path / "encrypted.mxl"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("score.musicxml", MUSICXML)

    payload = bytearray(source.read_bytes())
    for signature, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        header_offset = payload.index(signature)
        flag_start = header_offset + flag_offset
        flags = int.from_bytes(payload[flag_start : flag_start + 2])
        payload[header_offset + flag_offset : header_offset + flag_offset + 2] = (
            flags | 0x1
        ).to_bytes(2, "little")
    source.write_bytes(payload)

    with pytest.raises(PianoHandError) as error:
        parse_musicxml(source)

    assert error.value.code == ErrorCode.INPUT_ERROR
    assert "encrypted archive member" in error.value.message


def test_mxl_rejects_too_many_entries(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "too-many.mxl"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("score.musicxml", MUSICXML)
        archive.writestr("extra.txt", "extra")
    monkeypatch.setattr(musicxml_parser, "MXL_MAX_ENTRIES", 1)

    with pytest.raises(PianoHandError) as error:
        parse_musicxml(source)

    assert error.value.code == ErrorCode.INPUT_ERROR
    assert "entries" in error.value.message


def test_mxl_rejects_declared_size_and_abnormal_compression_ratio(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    oversized = tmp_path / "oversized.mxl"
    with zipfile.ZipFile(oversized, "w") as archive:
        archive.writestr("score.musicxml", MUSICXML)
    monkeypatch.setattr(
        musicxml_parser,
        "MXL_MAX_ENTRY_UNCOMPRESSED_BYTES",
        len(MUSICXML.encode("utf-8")) - 1,
    )

    with pytest.raises(PianoHandError) as size_error:
        parse_musicxml(oversized)

    assert size_error.value.code == ErrorCode.INPUT_ERROR
    assert "per-entry limit" in size_error.value.message

    compressed = tmp_path / "compression-bomb.mxl"
    with zipfile.ZipFile(compressed, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("score.musicxml", MUSICXML)
        archive.writestr("padding.bin", b"0" * 4096)
    monkeypatch.setattr(
        musicxml_parser,
        "MXL_MAX_ENTRY_UNCOMPRESSED_BYTES",
        32 * 1024 * 1024,
    )
    monkeypatch.setattr(musicxml_parser, "MXL_MAX_COMPRESSION_RATIO", 2.0)

    with pytest.raises(PianoHandError) as ratio_error:
        parse_musicxml(compressed)

    assert ratio_error.value.code == ErrorCode.INPUT_ERROR
    assert "compression ratio" in ratio_error.value.message


def test_mxl_streaming_read_rejects_actual_expansion_beyond_limit(tmp_path) -> None:
    source = tmp_path / "actual-size.mxl"
    info = zipfile.ZipInfo("score.musicxml")
    info.file_size = 1
    archive = cast(
        zipfile.ZipFile,
        SimpleNamespace(open=lambda *_args, **_kwargs: io.BytesIO(b"12345")),
    )

    with pytest.raises(PianoHandError) as error:
        musicxml_parser._read_mxl_member(
            source,
            archive,
            info,
            max_bytes=4,
        )

    assert error.value.code == ErrorCode.INPUT_ERROR
    assert "expands beyond" in error.value.message


def test_musicxml_parse_errors_use_piano_hand_error(tmp_path) -> None:
    source = tmp_path / "broken.xml"
    source.write_text("<score-partwise>", encoding="utf-8")

    with pytest.raises(PianoHandError) as error:
        parse_musicxml(source)

    assert error.value.code == ErrorCode.PARSE_ERROR
    assert str(source) in error.value.message


def test_parse_score_rejects_unsupported_extension(tmp_path) -> None:
    source = tmp_path / "score.txt"
    source.write_text("score", encoding="utf-8")

    with pytest.raises(PianoHandError) as error:
        parse_score(source)

    assert error.value.code == ErrorCode.INPUT_ERROR
