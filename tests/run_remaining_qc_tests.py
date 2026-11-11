"""RivaPlan v2.1 synthetic regression tests for remaining migration QC flags.

Run from the QGIS Python Console while the seven-tool RivaPlan provider is enabled.
This script uses temporary synthetic datasets and does not modify project layers.
Runtime PASS results must be obtained in QGIS; Python syntax checks alone are insufficient.
"""
import os
import tempfile
import traceback

from qgis.core import QgsFeature, QgsGeometry, QgsPointXY, QgsVectorLayer
import processing

OUT_DIR = tempfile.mkdtemp(prefix='rivaplan_remaining_qc_')
results = []


def line(name, coords):
    layer = QgsVectorLayer('LineString?crs=EPSG:32644', name, 'memory')
    assert layer.isValid(), 'Could not create memory layer'
    f = QgsFeature(layer.fields())
    f.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(x, y) for x, y in coords]))
    assert layer.dataProvider().addFeatures([f])[0]
    layer.updateExtents()
    return layer


def run(alg_id, label, params):
    path = os.path.join(OUT_DIR, label + '.gpkg')
    result = processing.run(alg_id, dict(params, OUTPUT=path))
    layer = QgsVectorLayer(result['OUTPUT'], label, 'ogr')
    assert layer.isValid() and layer.featureCount() >= 5, '{}: invalid/short output'.format(label)
    assert 'QC_Flags' in layer.fields().names(), '{}: no QC_Flags'.format(label)
    return layer


def flagged(layer, name):
    return [f for f in layer.getFeatures() if name in str(f['QC_Flags']).split(';')]


def test_centerline_spike():
    # Reference (new) centerline is straight; the old centerline normally differs
    # by 10 m, but one measurement at x=500 differs by 65 m.
    new = line('new_cl_spike', [(0, 0), (1000, 0)])
    old = line('old_cl_spike', [(0, 10), (400, 10), (500, 65),
                               (600, 10), (1000, 10)])
    out = run('rivaplan:centerline_shifting_distance', 'centerline_spike',
              {'NEWCL': new, 'OLDCL': old, 'INTERVAL': 100,
               'SPIKE_MULTIPLIER': 2.0, 'BEND_THRESHOLD': 175.0})
    hits = flagged(out, 'SHIFT_SPIKE')
    assert hits, 'Expected a centerline SHIFT_SPIKE flag near x=500'
    assert any(abs(float(f['Chainage']) - 500) <= 101 for f in hits), \
        'Spike flags did not occur near the exaggerated displacement'
    assert all(f['QC_Status'] == 'REVIEW' for f in hits), 'Spike should remain a REVIEW measurement'
    print('    centerline spike chainages:', [f['Chainage'] for f in hits])


def test_centerline_progression():
    # Old path moves forward only in x but takes a long zigzag between 400 and
    # 500 m. Its along-line progression therefore far exceeds a 100 m step.
    new = line('new_cl_progression', [(0, 0), (1000, 0)])
    old = line('old_cl_progression', [(0, 20), (400, 20), (425, 80),
                                     (450, 20), (475, 80), (500, 20),
                                     (1000, 20)])
    out = run('rivaplan:centerline_shifting_distance', 'centerline_progression',
              {'NEWCL': new, 'OLDCL': old, 'INTERVAL': 100,
               'PROGRESSION_MIN': 0.5, 'PROGRESSION_MAX': 1.5,
               'BEND_THRESHOLD': 175.0})
    hits = flagged(out, 'IRREGULAR_PROGRESSION')
    assert hits, 'Expected IRREGULAR_PROGRESSION on zigzag old centerline'
    assert all(f['QC_Status'] == 'REVIEW' for f in hits), 'Progression should be flagged, not removed'
    print('    centerline progression chainages:', [f['Chainage'] for f in hits])


def test_bankline_spike():
    reference = line('ref_bank_spike', [(0, 0), (1000, 0)])
    old = line('old_bank_spike', [(0, 25), (1000, 25)])
    new = line('new_bank_spike', [(0, 30), (400, 30), (500, 85),
                                 (600, 30), (1000, 30)])
    out = run('rivaplan:shifting_distance', 'bankline_spike',
              {'CENTERLINE': reference, 'OLDBANKLINE': old, 'NEWBANKLINE': new,
               'INTERVAL': 100, 'BANK': 0, 'SPIKE_MULTIPLIER': 2.0,
               'BEND_THRESHOLD': 175.0})
    hits = flagged(out, 'SHIFT_SPIKE')
    assert hits, 'Expected bankline SHIFT_SPIKE for exaggerated new-bank excursion'
    assert all(f['QC_Status'] == 'REVIEW' for f in hits), 'Spike values should be retained for review'
    print('    bankline spike chainages:', [f['Chainage'] for f in hits])


def test_bankline_progression():
    reference = line('ref_bank_progression', [(0, 0), (1000, 0)])
    old = line('old_bank_progression', [(0, 25), (400, 25), (425, 80),
                                       (450, 25), (475, 80), (500, 25),
                                       (1000, 25)])
    new = line('new_bank_progression', [(0, 35), (1000, 35)])
    out = run('rivaplan:shifting_distance', 'bankline_progression',
              {'CENTERLINE': reference, 'OLDBANKLINE': old, 'NEWBANKLINE': new,
               'INTERVAL': 100, 'BANK': 0, 'BANK_LOWER': 0.5,
               'BANK_UPPER': 1.5, 'BEND_THRESHOLD': 175.0})
    old_hits = flagged(out, 'IRREGULAR_PROGRESSION_OLD')
    assert old_hits, 'Expected IRREGULAR_PROGRESSION_OLD for zigzag old bankline'
    assert all(f['QC_Status'] == 'REVIEW' for f in old_hits), 'Progression should be retained for review'
    print('    bankline old-progression chainages:', [f['Chainage'] for f in old_hits])


def check(label, fn):
    try:
        fn()
        results.append(True)
        print('PASS:', label)
    except Exception as exc:
        results.append(False)
        print('FAIL:', label, '-', exc)
        traceback.print_exc()


print('RivaPlan remaining QC regression checks; outputs:', OUT_DIR)
check('Centerline migration: SHIFT_SPIKE', test_centerline_spike)
check('Centerline migration: IRREGULAR_PROGRESSION', test_centerline_progression)
check('Bankline migration: SHIFT_SPIKE', test_bankline_spike)
check('Bankline migration: IRREGULAR_PROGRESSION_OLD', test_bankline_progression)
print('RESULT: {}/{} tests passed'.format(sum(results), len(results)))
print('Outputs retained at:', OUT_DIR)
print('A PASS count is not a measured code-coverage percentage.')
