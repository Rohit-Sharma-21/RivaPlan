"""RivaPlan v2.1: centerline generation and migration-QC regression checks.

Run from the QGIS Python Console after enabling the installed RivaPlan plugin.
Uses synthetic line geometries; does not change existing project layers.
Runtime results must be verified in QGIS before claiming these tests pass.
"""
import math
import os
import tempfile
import traceback

from qgis.core import QgsFeature, QgsGeometry, QgsPointXY, QgsVectorLayer
import processing

BASE = tempfile.mkdtemp(prefix='rivaplan_generation_qc_')
checks = []


def make_line(name, coordinates, crs='EPSG:32644'):
    layer = QgsVectorLayer('LineString?crs=' + crs, name, 'memory')
    assert layer.isValid(), 'Cannot create input memory layer'
    feature = QgsFeature(layer.fields())
    feature.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(x, y) for x, y in coordinates]))
    assert layer.dataProvider().addFeatures([feature])[0]
    layer.updateExtents()
    return layer


def output_layer(algorithm, params, label, sink='OUTPUT'):
    path = os.path.join(BASE, label + '.gpkg')
    inputs = dict(params)
    inputs[sink] = path
    processing.run(algorithm, inputs)
    layer = QgsVectorLayer(path, label, 'ogr')
    assert layer.isValid() and layer.featureCount() > 0, '{} produced an empty/invalid output'.format(algorithm)
    return layer


def check(label, fn):
    try:
        fn()
        checks.append(True)
        print('PASS:', label)
    except Exception as exc:
        checks.append(False)
        print('FAIL:', label, '-', exc)
        traceback.print_exc()


def test_centerline_generation():
    # A 400 m long, 60 m wide straight channel: the generated path should
    # lie inside the bank corridor and have near-central elevation (y=0).
    left = make_line('left', [(0, 30), (400, 30)])
    right = make_line('right', [(0, -30), (400, -30)])
    out = output_layer(
        'rivaplan:make_centerline',
        {'LEFTBANK': left, 'RIGHTBANK': right, 'DENSIFY_INTERVAL': 10.0},
        'generated_centerline', sink='Centerline'
    )
    assert out.featureCount() == 1, 'Expected exactly one centerline feature'
    g = next(out.getFeatures()).geometry()
    assert g.length() > 300, 'Unexpectedly short centerline: {}'.format(g.length())
    assert g.isGeosValid(), 'Generated line geometry is invalid'
    coords = g.asMultiPolyline()[0] if g.isMultipart() else g.asPolyline()
    assert len(coords) >= 2, 'Insufficient centerline vertices'
    assert all(-30.001 <= p.y() <= 30.001 for p in coords), 'Generated line exits corridor'
    middle = g.interpolate(g.length() / 2).asPoint()
    assert abs(middle.y()) <= 5, 'Centerline midpoint is not near channel axis: {}'.format(middle.y())


def test_sharp_bend_flag():
    # River progresses east and turns sharply northeast before continuing east.
    # This should generate at least one SHARP_BEND review flag with a low
    # configured angular threshold. The old line is a small parallel shift.
    new = make_line('new_bent', [(0, 0), (200, 0), (280, 80), (480, 80)])
    old = make_line('old_bent', [(0, -12), (200, -12), (280, 68), (480, 68)])
    out = output_layer('rivaplan:centerline_shifting_distance',
                       {'NEWCL': new, 'OLDCL': old, 'INTERVAL': 40,
                        'BEND_THRESHOLD': 1.0}, 'sharp_bend_qc')
    flagged = [f for f in out.getFeatures() if 'SHARP_BEND' in str(f['QC_Flags'])]
    assert flagged, 'No SHARP_BEND flags detected near synthetic corner'
    assert all(f['QC_Status'] in ('REVIEW', 'NO_INTERSECTION') for f in flagged), \
        'Unexpected QC status for flagged measurements'


def test_invalid_qc_parameter_rejected():
    new = make_line('new_validation', [(0, 5), (500, 5)])
    old = make_line('old_validation', [(0, 0), (500, 0)])
    try:
        processing.run('rivaplan:centerline_shifting_distance',
                       {'NEWCL': new, 'OLDCL': old, 'INTERVAL': 50,
                        'PROGRESSION_MIN': 1.5, 'PROGRESSION_MAX': 0.5,
                        'OUTPUT': os.path.join(BASE, 'invalid_qc.gpkg')})
    except Exception as exc:
        msg = str(exc).lower()
        assert any(s in msg for s in ('progression', 'lower', 'upper')), str(exc)
        return
    raise AssertionError('Invalid progression factors were accepted')


print('RivaPlan centerline-generation and QC tests; outputs:', BASE)
check('Straight-bank input generates a central centerline', test_centerline_generation)
check('SHARP_BEND flags an abrupt reference-centerline turn', test_sharp_bend_flag)
check('Invalid QC progression parameter range is rejected', test_invalid_qc_parameter_rejected)
print('RESULT: {}/{} tests passed'.format(sum(checks), len(checks)))
print('Outputs retained at:', BASE)
print('These are additional regression checks, not a statement of measured code coverage.')
