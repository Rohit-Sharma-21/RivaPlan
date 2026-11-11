"""RivaPlan v2.1 smoke/regression tests.

Run from the QGIS Python Console, with the installed RivaPlan plugin enabled:
exec(open(r'F:\\...\\RivaPlan\\tests\\run_smoke_tests.py', encoding='utf-8').read())

Uses synthetic geometries only; writes outputs into a temporary directory.
No third-party pytest dependency is required for this first stage.
"""
import math
import os
import tempfile
import traceback

from qgis.core import (
    QgsFeature, QgsGeometry, QgsPointXY, QgsProject,
    QgsVectorLayer, QgsField, QgsWkbTypes, QgsApplication,
)
from qgis.PyQt.QtCore import QVariant
import processing

CRS = 'EPSG:32644'  # synthetic coordinates in metres
results = []


def line_layer(name, coordinates):
    layer = QgsVectorLayer('LineString?crs=' + CRS, name, 'memory')
    assert layer.isValid(), 'Memory layer failed to initialize'
    layer.dataProvider().addAttributes([QgsField('test_id', QVariant.Int)])
    layer.updateFields()
    f = QgsFeature(layer.fields())
    f.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(x, y) for x, y in coordinates]))
    f['test_id'] = 7
    assert layer.dataProvider().addFeatures([f])[0]
    layer.updateExtents()
    return layer


def run_result(algorithm, params, tag):
    output = os.path.join(folder, tag + '.gpkg')
    params = dict(params, OUTPUT=output)
    processing.run(algorithm, params)
    layer = QgsVectorLayer(output, tag, 'ogr')
    assert layer.isValid(), '%s produced no valid result' % algorithm
    assert layer.featureCount() > 0, '%s returned no features' % algorithm
    return layer


def measured(layer, chainage=500):
    candidates = [f for f in layer.getFeatures() if f['Shift'] is not None]
    assert candidates, 'No valid displacement measurements'
    return min(candidates, key=lambda f: abs(f['Chainage'] - chainage))


def check(label, fn):
    try:
        fn()
        results.append((label, 'PASS', ''))
        print('PASS:', label)
    except Exception as exc:
        results.append((label, 'FAIL', str(exc)))
        print('FAIL:', label, '-', exc)
        traceback.print_exc()


folder = tempfile.mkdtemp(prefix='rivaplan_smoke_')
print('RivaPlan smoke tests; outputs:', folder)
ids = ['rivaplan:make_centerline', 'rivaplan:rivaplan_reverse_line_direction',
       'rivaplan:width_of_river', 'rivaplan:centerline_shifting_distance',
       'rivaplan:shifting_distance', 'rivaplan:line_sinuosity_windows',
       'rivaplan:local_curvature_simple']


def test_registration():
    for algorithm in ids:
        assert QgsApplication.processingRegistry().algorithmById(algorithm), 'Not registered: ' + algorithm


def test_reverse():
    layer = line_layer('reverse_input', [(0, 0), (100, 20), (200, 0)])
    out = run_result(ids[1], {'INPUT': layer}, 'reversed')
    original = next(layer.getFeatures())
    f = next(out.getFeatures())
    old_pts = original.geometry().asPolyline()
    new_pts = f.geometry().asPolyline()
    assert [(p.x(), p.y()) for p in new_pts] == [(p.x(), p.y()) for p in reversed(old_pts)]
    assert math.isclose(f.geometry().length(), original.geometry().length(), abs_tol=1e-8)
    assert f['test_id'] == 7 and out.crs() == layer.crs()


def test_width():
    center = line_layer('center_width', [(0, 0), (1000, 0)])
    left = line_layer('left_width', [(0, 20), (1000, 20)])
    right = line_layer('right_width', [(0, -20), (1000, -20)])
    out = run_result(ids[2], {'LEFTBANK': left, 'RIGHTBANK': right,
              'CENTERLINE': center, 'CHAINAGE_INTERVAL': 100}, 'width')
    vals = [float(f['Width']) for f in out.getFeatures() if f['Width'] is not None]
    assert vals, 'Width output empty'
    assert all(abs(w - 40) <= 0.001 for w in vals), vals[:10]


def test_centerline_sign():
    # River flows east (first vertex -> last); north is downstream-left.
    old = line_layer('old_center', [(0, 0), (1000, 0)])
    new = line_layer('new_center', [(0, 5), (1000, 5)])
    out = run_result(ids[3], {'OLDCL': old, 'NEWCL': new, 'INTERVAL': 100}, 'centerline_sign')
    f = measured(out)
    assert abs(float(f['Shift']) - 5) <= 0.001
    assert f['Direction'] == 'Left' and float(f['SignedShift']) < 0, dict(f.attributes())


def test_bank_signs():
    center = line_layer('reference', [(0, 0), (1000, 0)])
    # Left bank moves north: outward, downstream-left.
    old_l = line_layer('left_old', [(0, 20), (1000, 20)])
    new_l = line_layer('left_new', [(0, 25), (1000, 25)])
    out_l = run_result(ids[4], {'CENTERLINE': center, 'OLDBANKLINE': old_l,
                'NEWBANKLINE': new_l, 'INTERVAL': 100, 'BANK': 0}, 'left_outward')
    a = measured(out_l)
    assert abs(float(a['Shift']) - 5) <= 0.001
    assert a['Direction'] == 'Left' and float(a['SignedShift']) < 0
    assert a['BankStatus'] == 'OUTWARD' and float(a['BankChange']) < 0
    # Right bank moves south: outward, downstream-right.
    old_r = line_layer('right_old', [(0, -20), (1000, -20)])
    new_r = line_layer('right_new', [(0, -25), (1000, -25)])
    out_r = run_result(ids[4], {'CENTERLINE': center, 'OLDBANKLINE': old_r,
                'NEWBANKLINE': new_r, 'INTERVAL': 100, 'BANK': 1}, 'right_outward')
    b = measured(out_r)
    assert abs(float(b['Shift']) - 5) <= 0.001
    assert b['Direction'] == 'Right' and float(b['SignedShift']) > 0
    assert b['BankStatus'] == 'OUTWARD' and float(b['BankChange']) < 0


for name, func in [
    ('Seven algorithms registered', test_registration),
    ('Line reversal preserves geometry length and attributes', test_reverse),
    ('Straight channel: width = 40 m', test_width),
    ('Centerline shift: downstream-left = negative', test_centerline_sign),
    ('Both banks: outward = negative BankChange', test_bank_signs),
]:
    check(name, func)

passed = sum(status == 'PASS' for _, status, _ in results)
print('\nRESULT: %s/%s tests passed' % (passed, len(results)))
print('Outputs retained at:', folder)
if passed != len(results):
    print('Please share the FAILED lines and traceback before releasing v2.1.0.')
else:
    print('Initial regression checks passed; other algorithms still require further tests.')
