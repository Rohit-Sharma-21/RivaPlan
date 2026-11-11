"""Additional RivaPlan v2.1 regression checks. Run in QGIS Python Console.
Uses installed RivaPlan processing provider, QGIS memory lines and temporary GPKGs.
Does not modify source data or the plugin. These tests have not been QGIS-runtime-verified.
"""
import math
import os
import tempfile
import traceback

from qgis.core import QgsFeature, QgsGeometry, QgsPointXY, QgsVectorLayer, QgsField
from qgis.PyQt.QtCore import QVariant
from qgis import processing

folder = tempfile.mkdtemp(prefix='rivaplan_additional_')
results = []


def line_layer(name, points, crs='EPSG:32644'):
    layer = QgsVectorLayer('LineString?crs=' + crs, name, 'memory')
    if not layer.isValid():
        raise AssertionError('Memory line invalid')
    f = QgsFeature(layer.fields())
    f.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(*p) for p in points]))
    assert layer.dataProvider().addFeatures([f])[0]
    layer.updateExtents()
    return layer


def run_layer(algorithm, params, filename):
    target = os.path.join(folder, filename + '.gpkg')
    processing.run(algorithm, dict(params, OUTPUT=target))
    out = QgsVectorLayer(target, filename, 'ogr')
    assert out.isValid() and out.featureCount() > 0, 'Invalid/empty result'
    return out


def check(name, test):
    try:
        test()
        print('PASS:', name)
        results.append(True)
    except Exception as error:
        print('FAIL:', name, ':', error)
        traceback.print_exc()
        results.append(False)


def test_fixed_sinuosity():
    # Exactly straight, 2500 m line, fixed 1 km windows -> final 500 m.
    line = line_layer('straight_2500m', [(0, 0), (2500, 0)])
    out = run_layer('rivaplan:line_sinuosity_windows',
                    {'INPUT': line, 'MODE': 0, 'WIN_KM': 1}, 'sinuosity_fixed')
    features = sorted(out.getFeatures(), key=lambda f: f.geometry().centroid().asPoint().x())
    assert len(features) == 3, 'Expected 3 windows, found %s' % len(features)
    for f in features:
        assert abs(float(f['Sinuosity']) - 1.0) <= 0.001, f.attributes()
    assert str(features[-1]['QC_Status']) == 'INCOMPLETE', features[-1].attributes()
    assert all(str(f['QC_Status']) == 'COMPLETE' for f in features[:-1])
    assert abs(features[-1].geometry().length() - 500) <= .01


def test_sliding_sinuosity():
    # Straight 2500 m line, window 1 km, step 500 m; all complete except
    # any terminal incomplete window explicitly retained by the algorithm.
    line = line_layer('straight_sliding', [(0, 0), (2500, 0)])
    out = run_layer('rivaplan:line_sinuosity_windows',
                    {'INPUT': line, 'MODE': 1, 'WIN_KM': 1, 'STEP_M': 500}, 'sinuosity_sliding')
    vals = list(out.getFeatures())
    assert len(vals) >= 4, 'Too few sliding windows'
    for f in vals:
        assert abs(float(f['Sinuosity']) - 1) <= .001, f.attributes()
        assert str(f['QC_Status']) in ('COMPLETE', 'INCOMPLETE')


def test_curvature_straight():
    line = line_layer('straight_curvature', [(0, 0), (1000, 0)])
    out = run_layer('rivaplan:local_curvature_simple',
                    {'INPUT': line, 'DIST_M': 100, 'MODE': 2}, 'curvature_straight')
    fs = list(out.getFeatures())
    assert fs, 'Missing curvature samples'
    for field in ('Kappa_Angle', 'Kappa_3pt'):
        values = [float(f[field]) for f in fs if f[field] is not None and str(f[field]) not in ('NULL', '')]
        assert values, 'No usable values in ' + field
        assert all(math.isfinite(k) and abs(k) < 1e-8 for k in values), (field, values)


def test_reject_geographic_width():
    # Geographic EPSG:4326 must be rejected as a metric working CRS;
    # all three inputs share geographic CRS.
    c = line_layer('center_geo', [(80, 20), (80.01, 20)], 'EPSG:4326')
    l = line_layer('left_geo', [(80, 20.001), (80.01, 20.001)], 'EPSG:4326')
    r = line_layer('right_geo', [(80, 19.999), (80.01, 19.999)], 'EPSG:4326')
    try:
        processing.run('rivaplan:width_of_river',
                       {'CENTERLINE': c, 'LEFTBANK': l, 'RIGHTBANK': r,
                        'CHAINAGE_INTERVAL': 100, 'OUTPUT': 'memory:'})
    except Exception as exc:
        message = str(exc).lower()
        assert any(word in message for word in ('crs', 'metre', 'meter', 'projected')), str(exc)
        return
    raise AssertionError('Geographic-CRS data were accepted for metric widths')


print('RivaPlan additional regression checks; output:', folder)
check('Fixed sinuosity: straight line and incomplete terminal window', test_fixed_sinuosity)
check('Sliding sinuosity: straight-line windows', test_sliding_sinuosity)
check('Curvature: zero for straight centerline, both methods', test_curvature_straight)
check('Input validation: reject geographic CRS for width', test_reject_geographic_width)
print('RESULT: %s/%s tests passed' % (sum(results), len(results)))
print('Outputs retained at:', folder)
print('Additional checks are preliminary; no measured coverage claim is implied.')
