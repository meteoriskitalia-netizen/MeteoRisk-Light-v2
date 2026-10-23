# -*- coding: utf-8 -*-
"""Test del banco di validazione del rilevamento convettivo (event-based).

I numeri attesi sono calcolati A MANO nel test (non copiati dal modulo):
  - tabella 2x2 nota hits=7, misses=3, false_alarms=2, cn=88 ->
    POD 0.70, FAR 2/9, CSI 7/12, bias 0.9, HSS dalla formula (H-E)/(1-E);
  - matching 1-a-1: due allarmi vicini a UN solo evento -> 1 hit + 1 FA;
  - finestra temporale NSSL: -25 min = miss, -20 min = hit (+7 = miss, +6 = hit);
  - stratificazione per distanza: eventi a 50/100/150/200 km nelle fasce giuste;
  - bootstrap: seed fisso -> intervalli identici fra due esecuzioni;
  - esclusione degli esempi fittizi (--exclude-examples).
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

_SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from validation import supercell_validation as sv  # noqa: E402

UTC = timezone.utc
T0 = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
TEMPLATE = os.path.join(_SCRIPTS, "validation", "truth_template.json")


# ---------------------------------------------------------------------------
# Helper di costruzione
# ---------------------------------------------------------------------------
def truth_event(event_id, when=None, lon=10.0, lat=45.0, **kwargs):
    return sv.TruthEvent(id=event_id, time_utc=when or T0, lon=lon, lat=lat, **kwargs)


def detection(det_id, when=None, lon=10.0, lat=45.0, **kwargs):
    return sv.Detection(id=det_id, time_utc=when or T0, lon=lon, lat=lat, **kwargs)


def _scenario(hits=0, misses=0, false_alarms=0, nulls=0):
    """Scenario sintetico con hit/miss/FA/CN separati spazialmente."""
    truth, detected = [], []
    for i in range(hits):
        lon = 10.0 + 0.6 * i
        truth.append(truth_event("TH%02d" % i, lon=lon))
        detected.append(detection("DH%02d" % i, when=T0 + timedelta(minutes=3),
                                  lon=lon + 0.03))
    for i in range(misses):
        truth.append(truth_event("TM%02d" % i, lon=20.0 + 0.6 * i))
    for i in range(false_alarms):
        detected.append(detection("DF%02d" % i, when=T0, lon=30.0 + 0.6 * i))
    for i in range(nulls):
        truth.append(truth_event("TN%02d" % i, lon=40.0 + 0.4 * i, lat=44.0,
                                 category="null", null_case=True))
    return detected, truth


def _write_supercells(path, candidates):
    payload = {
        "generated_at": "2026-05-01T12:05:00Z",
        "radar_timestamp": "2026-05-01T12:00:00Z",
        "status": "ok",
        "summary": {"tracks_evaluated": 10, "candidates": len(candidates),
                    "births": 0, "possible": 0, "marked": 0, "ssi_max": 70},
        "candidates": candidates,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# 1) Tabella 2x2 nota e metriche
# ---------------------------------------------------------------------------
def test_contingency_2x2_metrics_hand_computed():
    detected, truth = _scenario(hits=7, false_alarms=2, nulls=88)
    truth += [truth_event("TM%02d" % i, lon=20.0 + 0.6 * i) for i in range(3)]

    result = sv.match_events(detected, truth)
    counts = result.counts()

    assert (counts.hits, counts.misses, counts.false_alarms,
            counts.correct_negatives) == (7, 3, 2, 88)
    assert counts.total == 100
    assert [e.id for e in result.unmatched_truth] == ["TM00", "TM01", "TM02"]

    metrics = sv.compute_metrics(counts)
    assert metrics["pod"] == pytest.approx(7 / 10)
    assert metrics["far"] == pytest.approx(2 / 9)
    assert metrics["csi"] == pytest.approx(7 / 12)
    assert metrics["bias"] == pytest.approx(9 / 10)

    # HSS calcolato a mano con la formula 2x2: H = (a+d)/N (proporzione
    # corretta), E = [(a+b)(a+c) + (c+d)(b+d)] / N^2, HSS = (H - E) / (1 - E).
    n = 100
    observed = (7 + 88) / n
    expected = ((7 + 2) * (7 + 3) + (3 + 88) * (2 + 88)) / float(n * n)
    hss = (observed - expected) / (1.0 - expected)
    assert expected == pytest.approx(0.828)
    assert metrics["hss"] == pytest.approx(hss)
    assert metrics["hss"] == pytest.approx(0.7093023255813953)
    assert metrics["hss_expected_agreement"] == pytest.approx(0.828)

    # HSS NON e' l'accuracy (che qui e' 0.95): il test lo blinda.
    assert metrics["accuracy"] == pytest.approx(0.95)
    assert metrics["hss"] != pytest.approx(metrics["accuracy"])


def test_hss_none_when_no_alarm_and_no_event():
    # Solo correct negatives: accordo casuale atteso E = 1 -> HSS indefinito.
    counts = sv.Contingency(hits=0, misses=0, false_alarms=0, correct_negatives=12)
    metrics = sv.compute_metrics(counts)
    assert metrics["hss"] is None
    assert "hss_note" in metrics
    assert metrics["pod"] is None
    assert metrics["far"] is None
    assert metrics["bias"] is None


def test_hss_extremes_perfect_and_random():
    # Previsione perfetta: a=10, d=10, N=20 -> H=1, E=0.25 -> HSS=1.
    perfect = sv.compute_metrics(sv.Contingency(hits=10, misses=0,
                                               false_alarms=0, correct_negatives=10))
    assert perfect["hss"] == pytest.approx(1.0)
    # Previsione senza skill (a=b=c=d=5): H=0.5, E=0.5 -> HSS=0.
    random = sv.compute_metrics(sv.Contingency(hits=5, misses=5,
                                               false_alarms=5, correct_negatives=5))
    assert random["hss"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 2) Matching uno-a-uno greedy
# ---------------------------------------------------------------------------
def test_one_to_one_greedy_two_detections_one_event():
    event = truth_event("T1")
    near = detection("D-near", when=T0 + timedelta(minutes=1), lon=10.05)
    far = detection("D-far", when=T0 + timedelta(minutes=1), lon=10.10)

    result = sv.match_events([far, near], [event])
    counts = result.counts()

    assert counts.hits == 1
    assert counts.false_alarms == 1
    assert counts.misses == 0
    assert counts.n_detections == 2
    assert result.matches[0].detection.id == "D-near"
    assert [d.id for d in result.unmatched_detections] == ["D-far"]

    # La distanza della coppia accettata e' quella minima fra le due candidate.
    assert result.matches[0].distance_km < 4.0


def test_no_match_beyond_max_distance():
    event = truth_event("T1")
    too_far = detection("D1", lon=10.30)  # ~23.6 km a lat 45
    result = sv.match_events([too_far], [event])
    assert result.counts().misses == 1
    assert result.counts().false_alarms == 1
    assert result.counts().hits == 0

    relaxed = sv.match_events([too_far], [event], sv.MatchParams(max_distance_km=25.0))
    assert relaxed.counts().hits == 1


# ---------------------------------------------------------------------------
# 3) Finestra temporale NSSL [-20, +6] min
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("offset_min,expected_hits", [
    (-25, 0),
    (-20, 1),
    (-5, 1),
    (0, 1),
    (6, 1),
    (7, 0),
])
def test_time_window_boundaries(offset_min, expected_hits):
    event = truth_event("T1")
    alarm = detection("D1", when=T0 + timedelta(minutes=offset_min), lon=10.05)
    counts = sv.match_events([alarm], [event]).counts()
    assert counts.hits == expected_hits
    assert counts.misses == 1 - expected_hits


def test_track_interval_is_used_when_present():
    # Track viva da -40 a -5 min: l'istante rappresentativo (last_seen) cade
    # dentro la finestra, quindi e' un hit anche se first_seen e' fuori.
    event = truth_event("T1")
    track = sv.Detection(
        id="TRACK-1", time_utc=T0 - timedelta(minutes=5), lon=10.05, lat=45.0,
        first_seen_utc=T0 - timedelta(minutes=40),
        last_seen_utc=T0 - timedelta(minutes=5))
    assert sv.match_events([track], [event]).counts().hits == 1

    # Track chiusa 25 minuti prima dell'evento: fuori finestra -> miss.
    stale = sv.Detection(
        id="TRACK-2", time_utc=T0 - timedelta(minutes=40), lon=10.05, lat=45.0,
        first_seen_utc=T0 - timedelta(minutes=60),
        last_seen_utc=T0 - timedelta(minutes=25))
    assert sv.match_events([stale], [event]).counts().misses == 1


# ---------------------------------------------------------------------------
# 4) Stratificazione per distanza dal radar
# ---------------------------------------------------------------------------
def test_distance_band_stratification():
    specs = [("<60", 50.0), ("60-120", 100.0), ("120-180", 150.0), (">180", 200.0)]
    truth, detected = [], []
    for index, (_label, km) in enumerate(specs):
        lon = 10.0 + 0.6 * index
        truth.append(truth_event("T%d" % index, lon=lon, radar_distance_km=km))
        detected.append(detection("D%d" % index, when=T0 + timedelta(minutes=1),
                                  lon=lon + 0.03))

    strata = sv.stratify(sv.match_events(detected, truth))
    for index, (label, km) in enumerate(specs):
        assert sv.distance_band(km) == label
        bucket = strata["by_distance_band"][label]
        assert bucket["counts"]["hits"] == 1
        assert bucket["counts"]["misses"] == 0
        assert bucket["pod"] == pytest.approx(1.0)

    assert sv.distance_band(59.999) == "<60"
    assert sv.distance_band(60.0) == "60-120"
    assert sv.distance_band(119.999) == "60-120"
    assert sv.distance_band(120.0) == "120-180"
    assert sv.distance_band(180.0) == "120-180"
    assert sv.distance_band(180.001) == ">180"
    assert sv.distance_band(None) == "unknown"


def test_stratification_by_season_and_category():
    truth = [
        truth_event("T-winter", when=datetime(2026, 1, 10, 9, 0, tzinfo=UTC),
                    category="supercell", radar_distance_km=40.0),
        truth_event("T-summer", when=datetime(2026, 8, 10, 15, 0, tzinfo=UTC),
                    category="large_hail", radar_distance_km=90.0),
    ]
    detected = [
        detection("D1", when=datetime(2026, 1, 10, 9, 1, tzinfo=UTC), lon=10.03),
        detection("D2", when=datetime(2026, 8, 10, 15, 1, tzinfo=UTC), lon=10.03),
    ]
    strata = sv.stratify(sv.match_events(detected, truth))
    assert strata["by_season"]["DJF"]["counts"]["hits"] == 1
    assert strata["by_season"]["JJA"]["counts"]["hits"] == 1
    assert strata["by_month"]["2026-01"]["counts"]["hits"] == 1
    assert strata["by_month"]["2026-08"]["counts"]["hits"] == 1
    assert strata["by_category"]["supercell"]["counts"]["hits"] == 1
    assert strata["by_category"]["large_hail"]["counts"]["hits"] == 1


def test_unmatched_detections_are_reported_not_attributed_to_category():
    truth = [truth_event("T1", category="supercell")]
    detected = [
        detection("D1", when=T0 + timedelta(minutes=1), lon=10.03),
        detection("D2", when=T0 + timedelta(minutes=1), lon=30.0),
    ]
    strata = sv.stratify(sv.match_events(detected, truth))
    assert strata["by_category"]["supercell"]["counts"]["hits"] == 1
    assert strata["by_category"]["unattributed_detections"]["counts"]["false_alarms"] == 1


# ---------------------------------------------------------------------------
# 5) Bootstrap deterministico
# ---------------------------------------------------------------------------
def test_bootstrap_deterministic_with_fixed_seed():
    detected, truth = _scenario(hits=6, misses=4, false_alarms=3, nulls=20)
    params = sv.MatchParams(seed=777, bootstrap=2000)

    first = sv.evaluate(detected, truth, params)["metrics"]["bootstrap"]
    second = sv.evaluate(detected, truth, params)["metrics"]["bootstrap"]

    assert first == second
    assert first["resamples"] == 2000
    assert first["seed"] == 777

    intervals = first["intervals"]
    point = sv.compute_metrics(sv.match_events(detected, truth).counts())
    for key in ("pod", "far", "csi", "bias"):
        low, high = intervals[key]
        assert low <= point[key] <= high, key
    assert set(first["undefined_replicates"]) == {"pod", "far", "csi", "hss", "bias"}

    # Seed diverso => campione diverso (nessun valore identico per caso).
    other = sv.evaluate(detected, truth, sv.MatchParams(seed=778, bootstrap=2000))
    assert other["metrics"]["bootstrap"]["intervals"]["pod"] != intervals["pod"]


def test_bootstrap_zero_cases_is_declared_not_invented():
    report = sv.evaluate([], [], sv.MatchParams(bootstrap=2000))
    bootstrap = report["metrics"]["bootstrap"]
    assert bootstrap["resamples"] == 0
    assert bootstrap["intervals"]["pod"] is None
    assert "note" in bootstrap
    assert report["metrics"]["counts"]["n_cases"] == 0


# ---------------------------------------------------------------------------
# 6) Template di verita' ed esclusione degli esempi
# ---------------------------------------------------------------------------
def test_truth_template_loads_three_fictional_examples():
    events = sv.load_truth(TEMPLATE)
    assert len(events) == 3
    assert all(event.example for event in events)
    assert {event.category for event in events} == {"supercell", "large_hail", "tornado"}
    bands = {sv.distance_band(event.radar_distance_km) for event in events}
    assert bands == {"<60", "60-120", "120-180"}

    payload = json.loads(open(TEMPLATE, encoding="utf-8").read())
    assert "_instructions" in payload
    assert payload["schema_version"] == sv.TRUTH_SCHEMA_VERSION


def test_cli_excludes_example_events(tmp_path):
    detected_path = _write_supercells(tmp_path / "supercells.json", [
        {"supercell_id": "SC-1", "track_id": 1, "track_type": "cell", "ssi": 71,
         "first_seen": "2026-05-12T14:10:00Z", "last_seen": "2026-05-12T14:22:00Z",
         "position": [9.19, 45.46]},
        {"supercell_id": "SC-2", "track_id": 2, "track_type": "storm_object", "ssi": 68,
         "first_seen": "2026-08-21T18:30:00Z", "last_seen": "2026-08-21T18:42:00Z",
         "position": [16.87, 41.12]},
    ])

    with_examples = tmp_path / "with_examples.json"
    assert sv.main(["--detected", detected_path, "--truth", TEMPLATE,
                    "--out", str(with_examples), "--bootstrap", "200"]) == 0
    report = json.loads(with_examples.read_text(encoding="utf-8"))
    assert report["inputs"]["truth"]["n_used"] == 3
    assert report["metrics"]["counts"]["misses"] == 1  # EX-001 resta scoperto
    assert report["metrics"]["counts"]["hits"] == 2
    # EX-002 (large_hail, 12.49/41.9) non ha allarmi: miss dichiarato visibile.
    assert report["misses"]["by_reason"]["visible_but_missed"] == 1
    assert report["misses"]["events"][0]["id"] == "EX-002"
    assert report["unmatched_detections"]["count"] == 0

    without_examples = tmp_path / "without_examples.json"
    assert sv.main(["--detected", detected_path, "--truth", TEMPLATE,
                    "--out", str(without_examples), "--exclude-examples",
                    "--bootstrap", "200"]) == 0
    clean = json.loads(without_examples.read_text(encoding="utf-8"))
    assert clean["inputs"]["truth"]["n_used"] == 0
    assert clean["metrics"]["counts"]["n_truth_events"] == 0
    assert clean["metrics"]["counts"]["false_alarms"] == 2
    assert any("example" in warning for warning in clean["warnings"])


# ---------------------------------------------------------------------------
# 7) Adattatori di input
# ---------------------------------------------------------------------------
def test_supercells_adapter_uses_last_seen_and_track_type(tmp_path):
    path = _write_supercells(tmp_path / "supercells.json", [
        {"supercell_id": "SC-2026AB-c001", "track_id": 1, "track_type": "cell",
         "ssi": 77, "level": "possible", "phase": "sustained",
         "first_seen": "2026-05-01T11:30:00Z", "last_seen": "2026-05-01T12:02:00Z",
         "position": [10.05, 45.0]},
    ])
    read = sv.read_detections(path)
    assert read["format"] == "supercells"
    assert len(read["detections"]) == 1
    alarm = read["detections"][0]
    assert alarm.id == "SC-2026AB-c001"
    assert alarm.category == "cell"
    assert alarm.time_utc == datetime(2026, 5, 1, 12, 2, tzinfo=UTC)
    assert alarm.window_start == datetime(2026, 5, 1, 11, 30, tzinfo=UTC)
    assert alarm.extras["ssi"] == 77
    assert read["payload_meta"]["radar_timestamp"] == "2026-05-01T12:00:00Z"

    # L'intervallo della track rende valido il matching a 12:02 (+2 min).
    counts = sv.match_events(read["detections"], [truth_event("T1")]).counts()
    assert counts.hits == 1


def test_malformed_entries_are_skipped_with_warning(tmp_path):
    path = _write_supercells(tmp_path / "supercells.json", [
        "non-un-oggetto",
        {"supercell_id": "OK-1", "position": [10.0, 45.0],
         "last_seen": "2026-05-01T12:00:00Z"},
        {"supercell_id": "NO-POS", "last_seen": "2026-05-01T12:00:00Z"},
    ])
    read = sv.read_detections(path)
    assert [d.id for d in read["detections"]] == ["OK-1"]
    assert len(read["warnings"]) == 2


def test_storms_geojson_adapter(tmp_path):
    payload = {
        "type": "FeatureCollection",
        "generated_at": "2026-05-01T12:05:00Z",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [10.03, 45.01]},
             "properties": {"cell_id": "2026-05-01T12:00:00Z-004",
                            "timestamp": "2026-05-01T12:00:00Z",
                            "max_dbz": 52.5, "track_id": 4}},
        ],
    }
    path = tmp_path / "storms.geojson"
    path.write_text(json.dumps(payload), encoding="utf-8")

    read = sv.read_detections(str(path))
    assert read["format"] == "storms"
    alarm = read["detections"][0]
    assert alarm.id == "2026-05-01T12:00:00Z-004"
    assert (alarm.lon, alarm.lat) == pytest.approx((10.03, 45.01))
    assert alarm.time_utc == datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    assert alarm.extras["max_dbz"] == 52.5


def test_truth_csv_loader(tmp_path):
    path = tmp_path / "truth.csv"
    path.write_text(
        "id,time_utc,lon,lat,category,quality,miss_reason,source,radar_distance_km,example\n"
        "A1,2026-05-01T12:00:00Z,10.0,45.0,supercell,reported,,ESWD,55.0,false\n"
        "A2,2026-05-01T13:00:00Z,11.0,45.5,large_hail,unverified,visible_but_missed,ESWD,130.0,false\n",
        encoding="utf-8")
    read = sv.read_truth(str(path))
    assert read["format"] == "csv"
    assert len(read["events"]) == 2
    assert read["events"][0].radar_distance_km == 55.0
    assert read["events"][1].miss_reason == "visible_but_missed"
    assert read["events"][1].category == "large_hail"


def test_invalid_miss_reason_is_a_schema_error(tmp_path):
    payload = {"events": [{"id": "X1", "time_utc": "2026-05-01T12:00:00Z",
                           "lon": 10.0, "lat": 45.0,
                           "miss_reason": "non_era_visibile_boh"}]}
    path = tmp_path / "truth.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="miss_reason"):
        sv.load_truth(str(path))


def test_missing_required_field_is_an_error(tmp_path):
    payload = {"events": [{"id": "X1", "time_utc": "2026-05-01T12:00:00Z",
                           "lon": 10.0}]}
    path = tmp_path / "truth.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="lat"):
        sv.load_truth(str(path))


# ---------------------------------------------------------------------------
# 8) Contratto del report
# ---------------------------------------------------------------------------
def test_report_has_required_sections(tmp_path):
    detected, truth = _scenario(hits=4, misses=2, false_alarms=1, nulls=5)
    report = sv.build_report(detected, truth, sv.MatchParams(bootstrap=100))

    for key in ("generated_at", "params", "metrics", "stratified", "misses",
                "unmatched_detections"):
        assert key in report, key
    assert report["params"]["max_distance_km"] == 15.0
    assert report["params"]["pre_window_min"] == 20.0
    assert report["params"]["post_window_min"] == 6.0
    assert report["metrics"]["counts"]["hits"] == 4
    assert report["metrics"]["bootstrap"]["resamples"] == 100
    assert set(report["stratified"]) >= {"by_distance_band", "by_month",
                                         "by_season", "by_category"}
    assert report["misses"]["count"] == 2
    assert report["misses"]["by_reason"]["unknown"] == 2
    assert "disclaimer" in report

    out = tmp_path / "report.json"
    sv.write_report(report, str(out))
    text = out.read_text(encoding="utf-8")
    assert not text.startswith("\ufeff")
    assert "\r" not in text
    assert json.loads(text)["metrics"]["counts"]["misses"] == 2


def test_summary_is_readable():
    detected, truth = _scenario(hits=3, misses=1, false_alarms=1, nulls=4)
    summary = sv.format_summary(sv.build_report(detected, truth,
                                                sv.MatchParams(bootstrap=50)))
    assert "POD" in summary
    assert "HSS" in summary
    assert "contingenza 2x2" in summary
    assert "hits=3" in summary
