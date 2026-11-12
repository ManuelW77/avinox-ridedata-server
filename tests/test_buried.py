from datetime import UTC, datetime

from avinox.buried import device_info, match_ride, parse_buried, parse_device_time
from avinox.ride_rec import parse_ride
from ridebuilder import ride

END = "2026-11-5-22:58:43"  # UTC+8 -> 14:58:43 UTC
LINES = f"""
{{"general_msg":{{"Product_type":"EB100","boot_times":7}}}}
{{"event_id":"single_ride","time_now":"{END}","avg_speed":"6.3","elev_gain":"73.4","duration":"7643.0000"}}
{{"event_id":"single_ride","time_now":"{END}","distance":"18.368","battery":"26","elev_start":"133.1"}}
{{"event_id":"rider_cadence","time_now":"{END}","average":"13.7","max":"156.9"}}
{{"event_id":"gear_shift","time_now":"{END}","avg_speed":"6.3","distance":"18.368","duration":"7643.0000"}}
{{"event_id":"gear_shift","time_now":"{END}","gear_shift_1":"1","gear_shift_2":"29"}}
{{"event_id":"gear_shift","time_now":"{END}","gear_shift_10":"1124"}}
{{"event_id":"assist_mode_usage","time_now":"{END}","eco_duration":"0","trail_duration":"4933"}}
{{"event_id":"assist_mode_usage","time_now":"{END}","turbo_duration":"0","auto_duration":"1841","change_number":"13"}}
{{"event_id":"motor_torque","time_now":"{END}","torque_less_50":"7361","torque_50":"103"}}
{{"event_id":"Firmware_version","time_now":"2026-11-5-23:13:15","Package":"01.03.02.29","Motor":"01.00.11.00"}}
{{"event_id":"battery_usage","time_now":"2026-11-5-23:13:15","start_battery":"70","end_battery":"43"}}
{{"event_id":"battery_usage","time_now":"2026-11-5-23:13:15","max_current":"-46268","max_delta_cell_voltage":"217"}}
{{"event_id":"bike_pitch_attitude","time_now":"2026-11-5-23:13:15","slope_less_negative_10":"0.5","slope_less_10":"11.9","slope_more_40":"0"}}
{{"event_id":"ass_mode_para","time_now":"2026-11-5-21:13:49","mode":"trail","status":"1","ass_max":"14"}}
{{"event_id":"ass_mode_para","time_now":"2026-11-5-21:13:49","max_power":"1300"}}
{{"event_id":"ass_mode_para","time_now":"2026-11-5-22:36:0","mode":"auto","status":"1","ass_max":"10"}}
kaputte Zeile
{{"general_msg":{{"Product_type":"EB100","boot_times":8}}}}
{{"event_id":"battery_circle","time_now":"2026-11-6-15:0:54","number":"3"}}
"""


def test_device_time_is_utc_plus_8_without_rollover():
    assert parse_device_time("2026-10-30-30:45:52") == datetime(2026, 10, 30, 22, 45, 52, tzinfo=UTC)
    assert parse_device_time("1970-1-1-8:0:16") is None
    assert parse_device_time("") is None


def test_parse_sessions_and_ride():
    sessions = parse_buried(LINES)
    assert [s.boot_count for s in sessions] == [7, 8]
    r = sessions[0].rides[0]
    assert r.end_time == datetime(2026, 11, 5, 14, 58, 43, tzinfo=UTC)
    assert (r.duration_s, r.distance_km, r.battery_used_pct, r.elev_gain_m) == (7643, 18.368, 26, 73.4)
    assert r.avg_cadence_rpm == 13.7
    assert r.seconds_per_gear == {1: 1, 2: 29, 10: 1124}
    assert r.seconds_per_assist_mode == {"eco": 0, "trail": 4933, "turbo": 0, "auto": 1841}
    assert r.assist_mode_changes == 13
    assert r.motor_torque_s == {"<50": 7361, "50": 103}


def test_session_values():
    s = parse_buried(LINES)[0]
    assert s.firmware == {"package": "01.03.02.29", "motor": "01.00.11.00"}
    assert s.battery["max_current_a"] == 46.268 and s.battery["start_pct"] == 70 and s.battery["max_cell_delta_mv"] == 217
    assert s.pitch_km == {"<-10": 0.5, "<10": 11.9, ">40": 0}
    assert [m["mode"] for m in s.assist_mode_settings] == ["trail", "auto"]
    assert s.assist_mode_settings[0]["max_power"] == 1300
    assert device_info(parse_buried(LINES))["charge_cycles"] == 3


def test_match_by_end_time_and_by_duration():
    sessions = parse_buried(LINES)
    end = int(datetime(2026, 11, 5, 14, 58, 43, tzinfo=UTC).timestamp())
    with_clock = parse_ride(ride([], start=end - 7712, end=end, count=7643, distance=18368))
    assert match_ride(with_clock, sessions) is sessions[0].rides[0]
    no_clock = parse_ride(ride([], start=100, end=7812, count=7643, distance=18350))
    assert match_ride(no_clock, sessions) is sessions[0].rides[0]
    assert match_ride(parse_ride(ride([], start=100, end=200, count=100, distance=500)), sessions) is None
