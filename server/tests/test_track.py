import xml.dom.minidom

from avinox.ride_rec import parse_ride
from avinox.track import haversine_m, simplify, to_geojson, to_gpx, track_points
from ridebuilder import ride

T0 = 1_800_000_000
LAT, LON = 47.0, 11.0
M_LAT = 1 / 111_195  # Grad je Meter Breite


def gps_sample(t, north_m, east_m=0.0, alt=500.0, power=150):
    return {72: T0 + t, 31: LAT + north_m * M_LAT, 32: LON + east_m * M_LAT / 0.682, 39: int(alt * 100), 7: 1800, 13: power * 100, 9: 8000, 51: 130}


def straight_ride(n=60):
    return parse_ride(ride([gps_sample(t, 5 * t) for t in range(n)], start=T0, end=T0 + n))


def test_haversine():
    assert abs(haversine_m(LAT, LON, LAT + 1000 * M_LAT, LON) - 1000) < 1


def test_track_points_drop_outliers_duplicates_and_missing_fix():
    samples = [gps_sample(0, 0), gps_sample(1, 5), gps_sample(2, 5), {72: T0 + 3}, gps_sample(4, 5000), gps_sample(5, 15)]
    pts = track_points(parse_ride(ride(samples)))
    assert [p.index for p in pts] == [0, 1, 5]


def test_simplify_straight_line_keeps_ends():
    pts = track_points(straight_ride())
    simple = simplify(pts, 1.0)
    assert [p.index for p in simple] == [0, 59]


def test_simplify_keeps_corner():
    samples = [gps_sample(t, 5 * t) for t in range(20)] + [gps_sample(20 + t, 95, 5 * (t + 1)) for t in range(20)]
    simple = simplify(track_points(parse_ride(ride(samples))), 1.0)
    assert len(simple) == 3 and simple[1].index == 19


def test_geojson():
    g = to_geojson(straight_ride(), tolerance_m=1.0)
    line, start, end = g["features"]
    assert line["geometry"]["type"] == "LineString" and len(line["geometry"]["coordinates"]) == 2
    assert line["properties"]["points_total"] == 60 and abs(line["properties"]["length_m"] - 295) < 2
    assert len(line["properties"]["samples"]) == 2 and line["properties"]["samples"][0][4] == 150
    assert (start["properties"]["kind"], end["properties"]["kind"]) == ("start", "end")
    assert g["bbox"][1] == LAT


def test_geojson_falls_back_to_preview():
    g = to_geojson(parse_ride(ride([{6: 1}], preview=[(47.0, 11.0), (47.01, 11.01)])))
    assert g["features"][0]["properties"]["kind"] == "preview"
    assert to_geojson(parse_ride(ride([])))["features"] == []


def test_gpx_is_valid_xml_with_extensions():
    doc = xml.dom.minidom.parseString(to_gpx(straight_ride(5)))
    pts = doc.getElementsByTagName("trkpt")
    assert len(pts) == 5
    assert pts[0].getElementsByTagName("time")[0].firstChild.data == "2027-01-15T08:00:00Z"
    assert pts[0].getElementsByTagName("power")[0].firstChild.data == "150"
    assert pts[0].getElementsByTagName("gpxtpx:hr")[0].firstChild.data == "130"


def test_gpx_without_wallclock_has_no_times():
    r = parse_ride(ride([gps_sample(t, 5 * t) for t in range(3)], start=100, end=103))
    assert "<time>" not in to_gpx(r)
