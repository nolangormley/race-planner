import pytest
import pandas as pd
import numpy as np
from src.metrics import calculate_ctl_atl, calculate_tsb, get_target_category, calculate_vo2max_from_df, clean_val, calculate_acwr

def test_calculate_ctl_atl():
    loads = [100.0, 50.0, 150.0]
    ctl, atl = calculate_ctl_atl(loads, ctl_decay=42, atl_decay=7)
    assert len(ctl) == 3
    assert len(atl) == 3
    # First load
    assert ctl[0] == 100.0 / 42.0
    assert atl[0] == 100.0 / 7.0

def test_calculate_tsb():
    ctl = [10.0, 20.0]
    atl = [15.0, 10.0]
    tsb = calculate_tsb(ctl, atl)
    assert tsb == [-5.0, 10.0]

def test_calculate_tsb_scalar():
    tsb = calculate_tsb(50.0, 70.0)
    assert tsb == -20.0

def test_calculate_acwr():
    # scalar division
    assert calculate_acwr(50.0, 70.0) == 1.4
    # zero ctl handles safely
    assert calculate_acwr(0.0, 50.0) == 0.0
    # list division
    acwr = calculate_acwr([50.0, 0.0], [70.0, 50.0])
    assert acwr == [1.4, 0.0]

def test_calculate_acwr_series():
    # pd.Series is the only branch production code actually calls
    # (calculate_training_status_logic in src/api/main.py passes
    # merged['CTL']/merged['ATL'], which are Series) — it previously had no
    # test coverage at all despite being the live code path.
    ctl = pd.Series([50.0, 0.0, 25.0])
    atl = pd.Series([70.0, 50.0, 0.0])
    result = calculate_acwr(ctl, atl)
    assert isinstance(result, np.ndarray)
    np.testing.assert_allclose(result, [1.4, 0.0, 0.0])

def test_get_target_category():
    assert get_target_category(10) == "Anaerobic"
    assert get_target_category(0) == "VO2Max"
    assert get_target_category(-20) == "Aerobic"
    assert get_target_category(-40) == "Recovery"

def test_calculate_vo2max_from_df():
    data = {'speed': [2.0, 3.0], 'hr': [130, 170]}
    df = pd.DataFrame(data)
    result = calculate_vo2max_from_df(df, hr_max=190, hr_rest=60)
    assert 'vo2_max_est' in result.columns
    assert len(result) == 2

def test_calculate_vo2max_zero_hrr():
    # If HR is at or below rest, pct_hrr <= 0, which gets filtered
    data = {'speed': [2.0], 'hr': [60]}
    df = pd.DataFrame(data)
    result = calculate_vo2max_from_df(df, hr_max=190, hr_rest=60)
    assert result is None or result.empty

def test_clean_val():
    assert clean_val(10.123, 1) == 10.1
    assert clean_val(pd.NA) is None
    assert clean_val(np.nan) is None
    assert clean_val(None) is None


def test_pace_zone_speeds_are_meters_per_second():
    from src.api.services.metrics import pace_zones_from_threshold_speed, METERS_PER_MILE
    # Threshold at 7:00/mile.
    t_speed = METERS_PER_MILE / 420
    labels, speeds = pace_zones_from_threshold_speed(t_speed)

    assert labels["Threshold"] == "6'36\"-7'24\""
    assert labels["Recovery"] == ">9'48\""
    # Threshold zone edges sit either side of the threshold speed itself.
    assert speeds["Threshold"]["min"] < t_speed < speeds["Threshold"]["max"]
    assert speeds["Threshold"]["max"] == pytest.approx(METERS_PER_MILE / (420 * 0.945))
    # Zones tile the speed axis without gaps.
    assert speeds["Recovery"]["max"] == speeds["Aerobic Endurance"]["min"]
    assert speeds["Anaerobic Endurance"]["max"] == speeds["Anaerobic Power"]["min"]


def test_steady_state_mask_drops_warmup_surges_and_hr_lag():
    from src.metrics import steady_state_mask
    samples = 900
    speed = np.full(samples, 3.0)
    hr = np.full(samples, 150.0)
    speed[500:520] = 4.5          # a surge
    hr[600:610] = 165.0           # heart rate jumps without a pace change
    frame = pd.DataFrame({'activity_id': 1, 'time_offset': np.arange(samples), 'speed': speed, 'hr': hr})

    mask = steady_state_mask(frame)

    assert not mask[:300].any()          # warm-up
    assert not mask[505]                 # inside the surge
    assert not mask[603]                 # heart rate off its minute mean
    assert mask[700] and mask[450]       # steady running


def test_grade_raises_oxygen_cost_uphill_and_lowers_it_less_downhill():
    from src.metrics import calculate_vo2max_from_df
    flat = calculate_vo2max_from_df(pd.DataFrame({'speed': [3.0], 'hr': [150], 'grade': [0.0]}), 196, 60)
    up = calculate_vo2max_from_df(pd.DataFrame({'speed': [3.0], 'hr': [150], 'grade': [0.05]}), 196, 60)
    down = calculate_vo2max_from_df(pd.DataFrame({'speed': [3.0], 'hr': [150], 'grade': [-0.05]}), 196, 60)
    assert up['vo2_max_est'].iloc[0] > flat['vo2_max_est'].iloc[0] > down['vo2_max_est'].iloc[0]
    assert (up['vo2_current'].iloc[0] - flat['vo2_current'].iloc[0]) == pytest.approx(2 * (flat['vo2_current'].iloc[0] - down['vo2_current'].iloc[0]))


def test_add_grade_uses_climb_over_distance():
    from src.metrics import add_grade
    samples = 100
    frame = pd.DataFrame({
        'activity_id': 1, 'time_offset': np.arange(samples), 'speed': np.full(samples, 3.0), 'hr': 150.0,
        'altitude': np.arange(samples) * 0.15,   # 5% grade: 0.15 m up per 3 m along
    })
    graded = add_grade(frame)
    assert graded['grade'].iloc[-1] == pytest.approx(0.05, abs=1e-6)
    assert graded['grade'].iloc[0] == 0.0


def test_vdot_race_predictions_match_daniels_tables():
    from src.metrics import race_time_for_vdot, vdot_from_race
    # Daniels' Running Formula, VDOT 50: 5K 19:57, 10K 41:21, half 1:31:35, marathon 3:10:49.
    table = {5000: 19 * 60 + 57, 10000: 41 * 60 + 21, 21097.5: 91 * 60 + 35, 42195: 190 * 60 + 49}
    for distance, expected in table.items():
        assert race_time_for_vdot(50, distance) == pytest.approx(expected, rel=0.015)
    assert vdot_from_race(5000, 19 * 60 + 57) == pytest.approx(50, rel=0.015)
    # A fitter runner is faster at every distance.
    assert race_time_for_vdot(55, 10000) < race_time_for_vdot(50, 10000)


def test_planned_distance_parsing_and_entry_load():
    from src.metrics import planned_distance_km, estimate_entry_load
    assert planned_distance_km("6 miles") == pytest.approx(9.656, abs=0.01)
    assert planned_distance_km("8 km") == 8.0
    assert planned_distance_km("10K") == 10.0
    assert planned_distance_km("Rest") is None
    assert estimate_entry_load("Rest", None, 14.0) == 0.0
    assert estimate_entry_load("Easy Run", 8.0, 14.0) == pytest.approx(112.0)
    assert estimate_entry_load("Tempo", 8.0, 14.0) > estimate_entry_load("Easy Run", 8.0, 14.0)
    # No distance falls back to a typical distance for the type.
    assert estimate_entry_load("Long Run", None, 14.0) == pytest.approx(14.0 * 1.1 * 16.0)


def test_projection_steps_the_same_ewma_as_the_history():
    from src.metrics import project_training_load, calculate_ctl_atl
    loads = [100.0, 0.0, 80.0, 0.0, 0.0, 120.0]
    history_ctl, history_atl = calculate_ctl_atl(loads)
    # From a zero start, stepping through every load lands on the history's final values.
    projected = project_training_load(0.0, 0.0, loads)
    assert projected[-1][0] == pytest.approx(history_ctl[-1])
    assert projected[-1][1] == pytest.approx(history_atl[-1])
    # Form going into a day: the rest day after the first hard day raises it,
    # the hard day after that drops it.
    assert projected[2][2] > projected[1][2] > projected[3][2]


def test_best_effort_finds_the_fastest_stretch():
    from src.metrics import best_effort_seconds
    times = np.arange(0, 1800)                   # 30 minutes at 1 Hz
    speeds = np.full(1800, 3.0)                  # 3 m/s everywhere ...
    speeds[600:1000] = 4.0                       # ... except a 400 s surge
    # A 1 km best should sit inside the surge: 1000 m / 4 m/s = 250 s.
    assert best_effort_seconds(times, speeds, 1000.0) == pytest.approx(250, abs=1)
    # Longer than the run: no effort.
    assert best_effort_seconds(times, speeds, 10000.0) is None
    # Paused watch: 600 moving seconds at 3 m/s is 1794 m. Counting the
    # 10-minute gap as moving would fake 3594 m, so 1800 m must not be found.
    paused_times = np.concatenate([np.arange(0, 300), np.arange(900, 1200)])
    assert best_effort_seconds(paused_times, np.full(600, 3.0), 1800.0) is None


def test_riegel_scales_time_with_distance():
    from src.metrics import riegel_prediction
    # A 20:00 5K predicts a 10K just over double the time.
    assert riegel_prediction(1200, 5000, 10000) == pytest.approx(2502, abs=2)
    assert riegel_prediction(1200, 5000, 5000) == 1200
