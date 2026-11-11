# -*- coding: utf-8 -*-
# RivaPlan: QGIS Processing tools for river planform morphology analysis
# Copyright (C) 2026 Rohit Sharma
# River width: perpendicular transects with validated bank-to-bank endpoints.

import math
import time
from collections import Counter

from qgis.PyQt.QtCore import QCoreApplication, QMetaType
from qgis.core import (
    QgsProcessingParameterDefinition,
    QgsProcessing, QgsProcessingAlgorithm, QgsProcessingParameterCrs,
    QgsProcessingParameterNumber, QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSource, QgsProcessingParameterFeatureSink,
    QgsProcessingUtils, QgsProcessingMultiStepFeedback, QgsProcessingFeedback,
    QgsFeature, QgsField, QgsFields, QgsGeometry, QgsPointXY, QgsWkbTypes,
    QgsProcessingException, QgsUnitTypes, QgsFeatureSink
)
from qgis import processing


class QuietFeedback(QgsProcessingFeedback):
    """Suppress routine child messages but keep errors and cancellation."""
    def __init__(self, parent_feedback):
        super().__init__()
        self._fb = parent_feedback

    def isCanceled(self):
        return self._fb.isCanceled()

    def reportError(self, msg, fatalError=False):
        self._fb.reportError(msg, fatalError)

    def pushInfo(self, msg):
        pass

    def pushCommandInfo(self, msg):
        pass

    def pushDebugInfo(self, msg):
        pass

    def pushConsoleInfo(self, msg):
        pass


class WidthOfRiver(QgsProcessingAlgorithm):
    LEFTBANK = 'LEFTBANK'
    RIGHTBANK = 'RIGHTBANK'
    CENTERLINE = 'CENTERLINE'
    CHAINAGE_INTERVAL = 'CHAINAGE_INTERVAL'
    OUTPUT_CRS = 'CRS'
    SEARCH_METHOD = 'SEARCH_METHOD'
    SEARCH_DISTANCE = 'SEARCH_DISTANCE'
    OUTPUT = 'OUTPUT'

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return WidthOfRiver()

    def name(self):
        return 'width_of_river'

    def displayName(self):
        return self.tr('River Width')

    def group(self):
        return self.tr('')

    def groupId(self):
        return ''

    def shortHelpString(self):
        return self.tr("""<h3>River Width</h3>
<p>Measures river width between the left and right banks along perpendicular transects.</p>
<h4>Parameters</h4><ul>
<li><b>Left bank / Right bank:</b> Riverbank boundary lines.</li>
<li><b>Centerline:</b> Reference line for transects.</li>
<li><b>Chainage interval:</b> Distance between transects (m).</li>
<li><b>Transect search method:</b> Automatic or manual search distance.</li>
<li><b>Manual search distance:</b> Search distance in metres when manual mode is selected.</li>
</ul><h4>Advanced Parameters</h4><ul>
<li><b>Working CRS:</b> Optional projected CRS in metres; defaults to the centerline CRS.</li>
</ul><h4>Output</h4><p>Width lines with width measurements, remarks and QC status.</p>
<p>For detailed documentation, see the <a href="https://github.com/Rohit-Sharma-21/RivaPlan">RivaPlan GitHub page</a>.</p>""")

    def initAlgorithm(self, config=None):
        for key, label in (
            (self.LEFTBANK, 'Input Left Bank Layer'),
            (self.RIGHTBANK, 'Input Right Bank Layer'),
            (self.CENTERLINE, 'Input Centerline Layer'),
        ):
            self.addParameter(QgsProcessingParameterFeatureSource(
                key, self.tr(label), [QgsProcessing.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterCrs(
            self.OUTPUT_CRS,
            self.tr('Working CRS (optional; defaults to centerline CRS)'),
            defaultValue=None, optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.CHAINAGE_INTERVAL, self.tr('Chainage interval (m)'),
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=50, minValue=1))
        self.addParameter(QgsProcessingParameterEnum(
            self.SEARCH_METHOD, self.tr('Transect search method'),
            options=[
                'Automatic: full extent diagonal on EACH side',
                'Manual: user-defined search distance on EACH side',
            ], defaultValue=0))
        self.addParameter(QgsProcessingParameterNumber(
            self.SEARCH_DISTANCE, self.tr('Manual search distance on EACH side (m)'),
            type=QgsProcessingParameterNumber.Double,
            defaultValue=500.0, minValue=0.001, optional=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr('Widthlines'),
            type=QgsProcessing.TypeVectorLine,
            createByDefault=True, supportsAppend=True))

        # Optional settings appear under the collapsible Advanced Parameters section.
        parameter = self.parameterDefinition(self.OUTPUT_CRS)
        parameter.setFlags(parameter.flags() | QgsProcessingParameterDefinition.FlagAdvanced)

    def _run(self, name, args, context, child_feedback):
        return processing.run(
            name, args, context=context, feedback=child_feedback,
            is_child_algorithm=True)['OUTPUT']

    @staticmethod
    def _read_layer(value, context, label):
        layer = QgsProcessingUtils.mapLayerFromString(value, context)
        if layer is None:
            raise QgsProcessingException('Cannot load {}.'.format(label))
        return layer

    @staticmethod
    def _validate_layer(layer, label, require_one=False):
        if layer is None or not layer.isValid():
            raise QgsProcessingException('{} layer is missing or invalid.'.format(label))
        if not layer.crs().isValid():
            raise QgsProcessingException('{} has no valid CRS.'.format(label))
        if layer.featureCount() < 1:
            raise QgsProcessingException('{} has no features.'.format(label))
        if require_one and layer.featureCount() != 1:
            raise QgsProcessingException(
                '{} must contain exactly one continuous feature (found {}).'.format(
                    label, layer.featureCount()))
        for feat in layer.getFeatures():
            if not feat.hasGeometry() or feat.geometry().isEmpty():
                raise QgsProcessingException('{} contains empty geometry.'.format(label))
            geom = feat.geometry()
            if geom.length() <= 0 or not geom.isGeosValid():
                raise QgsProcessingException(
                    '{} contains zero-length or GEOS-invalid geometry.'.format(label))
            if geom.type() != QgsWkbTypes.LineGeometry:
                raise QgsProcessingException('{} must contain line geometry.'.format(label))
            if geom.isMultipart() and len(geom.asMultiPolyline()) != 1:
                raise QgsProcessingException(
                    '{} contains a disconnected multipart feature.'.format(label))

    @staticmethod
    def _as_single_line_geometry(layer, label):
        # Bank and centerline geometry is expected to be one feature and one part.
        geom = QgsGeometry(next(layer.getFeatures()).geometry())
        if geom.isMultipart():
            geom = QgsGeometry.fromPolylineXY(geom.asMultiPolyline()[0])
        if geom.isEmpty():
            raise QgsProcessingException('Empty {} geometry.'.format(label))
        return geom

    @staticmethod
    def _intersection_points(geometry):
        """Flatten Point/MultiPoint or linear overlaps to relevant endpoints."""
        points = []
        if geometry.isEmpty():
            return points
        if geometry.type() == QgsWkbTypes.PointGeometry:
            pts = geometry.asMultiPoint() if geometry.isMultipart() else [geometry.asPoint()]
            return [QgsPointXY(p) for p in pts]
        if geometry.type() == QgsWkbTypes.LineGeometry:
            parts = geometry.asMultiPolyline() if geometry.isMultipart() else [geometry.asPolyline()]
            for part in parts:
                if part:
                    points.extend((QgsPointXY(part[0]), QgsPointXY(part[-1])))
            return points
        # Geometry collections (rare, e.g. contact plus overlap).
        try:
            for subgeom in geometry.asGeometryCollection():
                points.extend(WidthOfRiver._intersection_points(subgeom))
        except (AttributeError, TypeError):
            pass
        return points

    @staticmethod
    def _hits_on_transect(ray, bank_geometry, origin, ux, uy, search_half):
        """Signed distances along the transect, not distances in the neighbourhood."""
        if not ray.intersects(bank_geometry):
            return []
        points = WidthOfRiver._intersection_points(ray.intersection(bank_geometry))
        hits = []
        for pt in points:
            signed = (pt.x() - origin.x()) * ux + (pt.y() - origin.y()) * uy
            # Ignore remote contacts, and intersections at the centerline itself.
            if 1e-7 < abs(signed) <= search_half + 1e-6:
                if not any(abs(signed - old[0]) <= 1e-6 for old in hits):
                    hits.append((signed, pt))
        return hits

    @staticmethod
    def _nearest_enclosing_pair(left_hits, right_hits):
        """Select one left/right pair bracketing the chainage along this transect.

        Multiple intersections are normal: choose the closest enclosing pair,
        never the closest arbitrary geometry elsewhere on the river.
        """
        pairs = []
        for left_distance, left_point in left_hits:
            for right_distance, right_point in right_hits:
                if left_distance * right_distance < 0:
                    pairs.append((abs(left_distance) + abs(right_distance),
                                  left_point, right_point))
        if not pairs:
            return None
        return min(pairs, key=lambda item: item[0])

    def processAlgorithm(self, parameters, context, feedback):
        start = time.perf_counter()
        left = self.parameterAsVectorLayer(parameters, self.LEFTBANK, context)
        right = self.parameterAsVectorLayer(parameters, self.RIGHTBANK, context)
        center = self.parameterAsVectorLayer(parameters, self.CENTERLINE, context)
        interval = self.parameterAsInt(parameters, self.CHAINAGE_INTERVAL, context)
        method = self.parameterAsEnum(parameters, self.SEARCH_METHOD, context)

        for layer, label in ((left, 'Left bank'), (right, 'Right bank'),
                             (center, 'Centerline')):
            self._validate_layer(layer, label, require_one=True)
        if interval <= 0:
            raise QgsProcessingException('Chainage interval must be positive.')

        provided_crs = parameters.get(self.OUTPUT_CRS)
        if provided_crs is None or not str(provided_crs).strip():
            working_crs = center.crs()
        else:
            working_crs = self.parameterAsCrs(parameters, self.OUTPUT_CRS, context)
        if (not working_crs.isValid() or working_crs.isGeographic() or
                working_crs.mapUnits() != QgsUnitTypes.DistanceMeters):
            raise QgsProcessingException(
                'Working CRS must be a valid projected CRS with metre units.')

        if method not in (0, 1):
            raise QgsProcessingException('Invalid transect search method.')
        manual_half_length = self.parameterAsDouble(
            parameters, self.SEARCH_DISTANCE, context)
        if method == 1 and (not math.isfinite(manual_half_length) or
                            manual_half_length <= 0):
            raise QgsProcessingException(
                'Manual search distance on each side must be positive and finite.')

        fb = QgsProcessingMultiStepFeedback(6, feedback)
        qf = QuietFeedback(fb)
        feedback.pushInfo('RivaPlan | River Width')
        feedback.pushInfo('Working CRS: {}'.format(working_crs.authid()))
        feedback.pushInfo('Chainage interval: {} m'.format(interval))

        # Prepare all geometries in the same metric coordinate space.
        fb.setCurrentStep(0)
        prepared = {}
        for key, layer in (('left', left), ('right', right), ('center', center)):
            if fb.isCanceled():
                return {}
            if layer.crs() == working_crs:
                prepared[key] = layer
            else:
                result = self._run('native:reprojectlayer', {
                    'INPUT': layer, 'TARGET_CRS': working_crs,
                    'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT}, context, qf)
                prepared[key] = self._read_layer(result, context, key)
            self._validate_layer(prepared[key], key, require_one=True)
        left_geom = self._as_single_line_geometry(prepared['left'], 'left bank')
        right_geom = self._as_single_line_geometry(prepared['right'], 'right bank')
        if left_geom.intersects(right_geom):
            raise QgsProcessingException('Input banklines intersect or overlap.')
        preparation_time = time.perf_counter() - start
        feedback.pushInfo('Input geometry validation: PASS')

        # Full extent diagonal is the reach-scale search length ON EACH SIDE.
        fb.setCurrentStep(1)
        bounds = left_geom.boundingBox()
        bounds.combineExtentWith(right_geom.boundingBox())
        extent_diagonal = math.hypot(bounds.width(), bounds.height())
        if method == 0:
            half_length = extent_diagonal
            feedback.pushInfo('Transect method: Automatic (full extent diagonal on each side)')
        else:
            half_length = manual_half_length
            feedback.pushInfo('Transect method: Manual (distance on each side)')
        if not math.isfinite(half_length) or half_length <= 0:
            raise QgsProcessingException('Computed transect search distance is invalid.')
        feedback.pushInfo('Search distance on each side: {:.3f} m'.format(half_length))
        feedback.pushInfo('Total transect search length: {:.3f} m'.format(half_length * 2))

        # Keep original pointsalonglines/angle convention of the existing tool.
        fb.setCurrentStep(2)
        points_path = self._run('native:pointsalonglines', {
            'INPUT': prepared['center'], 'DISTANCE': interval,
            'START_OFFSET': 0, 'END_OFFSET': 0,
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT}, context, qf)
        points_layer = self._read_layer(points_path, context, 'chainage points')
        if points_layer.featureCount() == 0:
            raise QgsProcessingException('No chainage points were generated.')
        points = list(points_layer.getFeatures())
        transect_setup_time = time.perf_counter() - start - preparation_time

        # Extend each ORIGINAL bank separately, preserving its input-side identity.
        # This remains the existing endpoint-extension fallback (not a buffer).
        fb.setCurrentStep(3)
        extended = {}
        for side in ('left', 'right'):
            result = self._run('native:extendlines', {
                'INPUT': prepared[side], 'START_DISTANCE': half_length,
                'END_DISTANCE': half_length,
                'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT}, context, qf)
            lyr = self._read_layer(result, context, 'extended ' + side + ' bank')
            extended[side] = self._as_single_line_geometry(lyr, side + ' extended bank')

        fields = QgsFields()
        fields.append(QgsField('Chainage', QMetaType.Type.Double))
        fields.append(QgsField('angle', QMetaType.Type.Double))
        fields.append(QgsField('Width', QMetaType.Type.Double))
        fields.append(QgsField('Remark', QMetaType.Type.QString, len=120))
        fields.append(QgsField('QC_Status', QMetaType.Type.QString, len=20))
        fields.append(QgsField('SearchLen', QMetaType.Type.Int))
        sink, dest_id = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields,
            QgsWkbTypes.LineString, working_crs)
        if sink is None:
            raise QgsProcessingException('Could not create the widthline output sink.')

        counts = Counter()
        failures = []
        fallback_count = 0
        fb.setCurrentStep(4)

        def save_unmeasured_transect(chainage, angle, search_geometry, reason):
            # Preserve the full geometric search line; do not equate its length
            # with a measured bank-to-bank width.
            nonlocal fallback_count
            feature = QgsFeature(fields)
            feature.setGeometry(search_geometry)
            feature.setAttributes([
                chainage, angle, None, reason, 'NO_WIDTH',
                int(round(search_geometry.length()))
            ])
            if not sink.addFeature(feature, QgsFeatureSink.FastInsert):
                raise QgsProcessingException(
                    'Could not write unmeasured transect at chainage {:.3f} m.'.format(chainage))
            fallback_count += 1

        for pt_feature in points:
            if fb.isCanceled():
                return {}
            pt_geom = pt_feature.geometry()
            if pt_geom.isEmpty():
                counts['missing_point'] += 1
                continue
            origin = QgsPointXY(pt_geom.asPoint())
            chainage = float(pt_feature['distance'])
            angle = float(pt_feature['angle'])
            if not math.isfinite(angle):
                failures.append((chainage, 'Invalid transect angle'))
                continue

            # Match original geometrybyexpression: angle - 90 degrees.
            azimuth = math.radians(angle - 90.0)
            ux = math.sin(azimuth)
            uy = math.cos(azimuth)
            end_positive = QgsPointXY(origin.x() + ux * half_length,
                                      origin.y() + uy * half_length)
            end_negative = QgsPointXY(origin.x() - ux * half_length,
                                      origin.y() - uy * half_length)
            transect = QgsGeometry.fromPolylineXY([end_negative, end_positive])

            orig_l = self._hits_on_transect(
                transect, left_geom, origin, ux, uy, half_length)
            orig_r = self._hits_on_transect(
                transect, right_geom, origin, ux, uy, half_length)
            selected = self._nearest_enclosing_pair(orig_l, orig_r)
            left_extended = right_extended = False

            if selected is None:
                # Retry using extended banks only where needed. Testing all
                # original/extended pairs also handles near-reach-end misses.
                ext_l = self._hits_on_transect(
                    transect, extended['left'], origin, ux, uy, half_length)
                ext_r = self._hits_on_transect(
                    transect, extended['right'], origin, ux, uy, half_length)
                fallback_options = [
                    (False, False, orig_l, orig_r),
                    (True, False, ext_l, orig_r),
                    (False, True, orig_l, ext_r),
                    (True, True, ext_l, ext_r),
                ]
                found = []
                for ex_l, ex_r, hits_l, hits_r in fallback_options:
                    pair = self._nearest_enclosing_pair(hits_l, hits_r)
                    if pair is not None:
                        # Prefer fewer extended banks; then shortest width.
                        found.append((int(ex_l) + int(ex_r), pair[0],
                                      ex_l, ex_r, pair))
                if found:
                    _, _, left_extended, right_extended, selected = min(
                        found, key=lambda item: (item[0], item[1]))

            if selected is None:
                # A geometrically present contact on the wrong side cannot
                # form a complete width across the chainage point.
                ext_l = self._hits_on_transect(
                    transect, extended['left'], origin, ux, uy, half_length)
                ext_r = self._hits_on_transect(
                    transect, extended['right'], origin, ux, uy, half_length)
                missing = []
                if not ext_l:
                    missing.append('left bank')
                if not ext_r:
                    missing.append('right bank')
                reason = ('{} intersection not found'.format(' and '.join(missing))
                          if missing else
                          'No left/right bank intersection pair encloses the chainage point')
                failures.append((chainage, reason))
                save_unmeasured_transect(chainage, angle, transect, reason)
                continue

            width, left_point, right_point = selected
            if not math.isfinite(width) or width <= 0:
                reason = 'Non-positive or invalid width'
                failures.append((chainage, reason))
                save_unmeasured_transect(chainage, angle, transect, reason)
                continue
            # The actual line is between selected bank contacts, NOT the
            # full-length search transect and NOT a point-to-feature distance.
            geom = QgsGeometry.fromPolylineXY([left_point, right_point])
            if left_extended and right_extended:
                remark = 'Width by extended both banks'
            elif left_extended:
                remark = 'Width by extended left bank'
            elif right_extended:
                remark = 'Width by extended right bank'
            else:
                remark = 'Width by banklines'
            out_feat = QgsFeature(fields)
            out_feat.setGeometry(geom)
            out_feat.setAttributes([chainage, angle, width, remark, 'VALID',
                                    float(transect.length())])
            if not sink.addFeature(out_feat, QgsFeatureSink.FastInsert):
                raise QgsProcessingException(
                    'Could not write widthline at chainage {:.3f} m.'.format(chainage))
            counts[remark] += 1

        processing_time = time.perf_counter() - start - preparation_time - transect_setup_time
        fb.setCurrentStep(5)
        details = context.layerToLoadOnCompletionDetails(dest_id)
        details.name = 'Widthlines'
        feedback.pushInfo('--- RivaPlan width quality checks ---')
        feedback.pushInfo('Chainage points: {}'.format(len(points)))
        feedback.pushInfo('Widths successfully computed: {}'.format(sum(
            counts[label] for label in (
                'Width by banklines', 'Width by extended left bank',
                'Width by extended right bank', 'Width by extended both banks'))))
        for label in (
            'Width by banklines', 'Width by extended left bank',
            'Width by extended right bank', 'Width by extended both banks'
        ):
            feedback.pushInfo('{}: {}'.format(label, counts[label]))
        feedback.pushInfo('Widths not computed: {}'.format(len(failures)))
        feedback.pushInfo('Unmeasured search transects retained (QC_Status=NO_WIDTH): {}'.format(
            fallback_count))
        feedback.pushInfo('For NO_WIDTH records, Width is NULL and SearchLen is the full search transect length (m).')
        for chainage, reason in failures:
            feedback.pushWarning(
                'Width not computed at chainage {:.3f} m: {}. Full search transect retained where available.'.format(
                    chainage, reason))
        feedback.pushInfo('--- RivaPlan width processing ---')
        feedback.pushInfo('1. Input preparation: {:.3f} s'.format(preparation_time))
        feedback.pushInfo('2. Transect setup: {:.3f} s'.format(transect_setup_time))
        feedback.pushInfo('3. Intersections and width calculation: {:.3f} s'.format(
            processing_time))
        feedback.pushInfo('Total width generation: {:.3f} s'.format(
            time.perf_counter() - start))
        feedback.pushInfo('Width generation completed.')
        return {self.OUTPUT: dest_id}
