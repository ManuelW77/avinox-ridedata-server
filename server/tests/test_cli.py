import json

from avinox.cli import main
from ridebuilder import ride
from test_buried import LINES
from test_track import gps_sample

T0 = 1_800_000_000


def make_drive(tmp_path):
    (tmp_path / "ride_rec" / "4242").mkdir(parents=True)
    (tmp_path / "buried_rec").mkdir()
    path = tmp_path / "ride_rec" / "4242" / "ride_rec_1000.proto"
    path.write_bytes(ride([gps_sample(t, 5 * t) for t in range(30)], start=T0, end=T0 + 30))
    (tmp_path / "buried_rec" / "buried_file_cur.json").write_text(LINES)
    return path


def test_summary_and_analyze(tmp_path, capsys):
    path = make_drive(tmp_path)
    assert main(["summary", str(tmp_path / "ride_rec"), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["ride_id"] == 1
    out = tmp_path / "a.json"
    assert main(["analyze", str(path), "-o", str(out)]) == 0
    assert json.loads(out.read_text())["gps"]["points"] == 30


def test_gpx_geojson_device(tmp_path, capsys):
    path = make_drive(tmp_path)
    assert main(["gpx", str(path), "-o", str(tmp_path / "r.gpx")]) == 0
    assert (tmp_path / "r.gpx").read_text().count("<trkpt") == 30
    assert main(["geojson", str(path), "-o", str(tmp_path / "r.geojson")]) == 0
    assert json.loads((tmp_path / "r.geojson").read_text())["type"] == "FeatureCollection"
    assert main(["device", str(tmp_path / "buried_rec" / "buried_file_cur.json")]) == 0
    assert json.loads(capsys.readouterr().out)["boot_count"] == 8


def test_broken_file_returns_error(tmp_path):
    bad = tmp_path / "ride_rec_1.proto"
    bad.write_bytes(b"kaputt")
    assert main(["analyze", str(bad)]) == 1
