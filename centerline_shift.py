# -*- coding: utf-8 -*-
"""RivaPlan | Centerline shifting.

Perpendiculars originate at regular chainages on the NEW centerline.
The nearest OLD centerline crossing is selected by distance ALONG that
perpendicular, not by a neighbourhood search. QC flags are diagnostic only.
"""

import math
import statistics
import time
from collections import Counter

from qgis.PyQt.QtCore import QCoreApplication, QMetaType
from qgis.core import (
    QgsFeature, QgsFeatureSink, QgsField, QgsFields, QgsGeometry, QgsPointXY,
    QgsProcessing, QgsProcessingAlgorithm, QgsProcessingException,
    QgsProcessingFeedback, QgsProcessingMultiStepFeedback,
    QgsProcessingParameterCrs, QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink, QgsProcessingParameterNumber,
    QgsProcessingParameterVectorLayer, QgsProcessingUtils,
    QgsProcessingParameterDefinition,
    QgsUnitTypes, QgsWkbTypes,
)
from qgis import processing


class _QuietFeedback(QgsProcessingFeedback):
    def __init__(self, parent):
        super().__init__()
        self.parent = parent

    def isCanceled(self):
        return self.parent.isCanceled()

    def reportError(self, error, fatalError=False):
        self.parent.reportError(error, fatalError)

    def pushInfo(self, message):
        pass

    def pushCommandInfo(self, message):
        pass

    def pushDebugInfo(self, message):
        pass

    def pushConsoleInfo(self, message):
        pass

    def pushWarning(self, message):
        self.parent.pushWarning(message)


class centerlineshifting(QgsProcessingAlgorithm):
    NEWCL = 'NEWCL'
    OLDCL = 'OLDCL'
    INTERVAL = 'INTERVAL'
    OUTPUT_CRS = 'CRS'
    SEARCH_METHOD = 'SEARCH_METHOD'
    SEARCH_DISTANCE = 'SEARCH_DISTANCE'
    BEND_THRESHOLD = 'BEND_THRESHOLD'
    SPIKE_MULTIPLIER = 'SPIKE_MULTIPLIER'
    PROGRESSION_MIN = 'PROGRESSION_MIN'
    PROGRESSION_MAX = 'PROGRESSION_MAX'
    OUTPUT = 'OUTPUT'

    def tr(self, value):
        return QCoreApplication.translate('Processing', value)

    def createInstance(self):
        return centerlineshifting()

    def name(self):
        return 'centerline_shifting_distance'

    def displayName(self):
        return self.tr('Centerline shifting')

    def group(self):
        return self.tr(self.groupId())

    def groupId(self):
        return ''

    def shortHelpString(self):
        return self.tr("""
        <h3>Centerline Shifting</h3>
        <p>Measures centerline shifting between historical and recent
        centerlines using perpendicular transects.</p>

        <h4>Parameters</h4>
        <ul>
          <li><b>New centerline:</b> Centerline representing the recent period.</li>
          <li><b>Old centerline:</b> Centerline representing the historical period.</li>
          <li><b>Chainage interval:</b> Distance between consecutive transects (m).</li>
          <li><b>Working CRS:</b> Projected coordinate system in metres.</li>
          <li><b>Transect search method:</b> Automatic or manual search distance.</li>
        </ul>

        <h4>Advanced Parameters</h4>
        <ul>
          <li><b>Sharp-bend threshold:</b> Flags abrupt changes in centerline direction.</li>
          <li><b>Shift-spike multiplier:</b> Flags unusually large local shifts.</li>
          <li><b>Intersection progression limits:</b> Flag unusual distances between successive intersections along the older centerline.</li>
        </ul>

        <h4>Output</h4>
        <p>Produces centerline-shifting lines with displacement, direction,
        signed shift and quality-control flags.</p>

        <p>For detailed methodology and documentation, visit the
        <a href="https://github.com/Rohit-Sharma-21/RivaPlan">RivaPlan GitHub page</a>.</p>
        """)

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.NEWCL, self.tr('New Centerline'),
            types=[QgsProcessing.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.OLDCL, self.tr('Old Centerline'),
            types=[QgsProcessing.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterNumber(
            self.INTERVAL, self.tr('Chainage interval (m)'),
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=100, minValue=1))
        self.addParameter(QgsProcessingParameterCrs(
            self.OUTPUT_CRS,
            self.tr('Working CRS (optional; defaults to new centerline CRS)'),
            defaultValue=None, optional=True))
        self.addParameter(QgsProcessingParameterEnum(
            self.SEARCH_METHOD, self.tr('Transect search method'),
            options=['Automatic: full extent diagonal on EACH side',
                     'Manual: search distance on EACH side'], defaultValue=0))
        self.addParameter(QgsProcessingParameterNumber(
            self.SEARCH_DISTANCE, self.tr('Manual search distance on EACH side (m)'),
            type=QgsProcessingParameterNumber.Double,
            defaultValue=500.0, minValue=0.001, optional=True))
        # QGIS automatically shows flagged parameters under Advanced Parameters.
        advanced_specs = [
            (self.BEND_THRESHOLD, 'Sharp-bend threshold (degrees)', 10.0, 0.001, 180.0),
            (self.SPIKE_MULTIPLIER, 'Shift-spike multiplier', 2.0, 1.0, None),
            (self.PROGRESSION_MIN, 'Centerline progression lower factor', 0.5, 0.0, None),
            (self.PROGRESSION_MAX, 'Centerline progression upper factor', 1.5, 0.0, None),
        ]
        for key, label, default, minimum, maximum in advanced_specs:
            kwargs = dict(type=QgsProcessingParameterNumber.Double,
                          defaultValue=default, minValue=minimum)
            if maximum is not None:
                kwargs['maxValue'] = maximum
            parameter = QgsProcessingParameterNumber(key, self.tr(label), **kwargs)
            parameter.setFlags(parameter.flags() |
                               QgsProcessingParameterDefinition.FlagAdvanced)
            self.addParameter(parameter)
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr('Centerline Shifting (Hub Lines)'),
            type=QgsProcessing.TypeVectorLine,
            createByDefault=True, supportsAppend=False))

    @staticmethod
    def _valid_line(layer, label):
        if layer is None or not layer.isValid() or not layer.crs().isValid():
            raise QgsProcessingException('{} layer or its CRS is invalid.'.format(label))
        if layer.featureCount() != 1:
            raise QgsProcessingException(
                '{} must contain exactly one continuous line feature.'.format(label))
        f = next(layer.getFeatures(), None)
        if f is None or not f.hasGeometry() or f.geometry().isEmpty():
            raise QgsProcessingException('{} has no usable geometry.'.format(label))
        g = f.geometry()
        if (g.type() != QgsWkbTypes.LineGeometry or g.length() <= 0
                or not g.isGeosValid()):
            raise QgsProcessingException(
                '{} must be a valid nonzero-length line.'.format(label))
        if g.isMultipart():
            parts = g.asMultiPolyline()
            if len(parts) != 1:
                raise QgsProcessingException(
                    '{} contains disconnected line parts.'.format(label))
            g = QgsGeometry.fromPolylineXY(parts[0])
        if len(g.asPolyline()) < 2:
            raise QgsProcessingException('{} needs at least two vertices.'.format(label))
        return g

    @staticmethod
    def _point_hits(geometry):
        """Extract point intersections; overlaps contribute their endpoints."""
        if geometry.isEmpty():
            return []
        if geometry.type() == QgsWkbTypes.PointGeometry:
            values = (geometry.asMultiPoint() if geometry.isMultipart()
                      else [geometry.asPoint()])
            return [QgsPointXY(p) for p in values]
        if geometry.type() == QgsWkbTypes.LineGeometry:
            parts = (geometry.asMultiPolyline() if geometry.isMultipart()
                     else [geometry.asPolyline()])
            result = []
            for part in parts:
                if part:
                    result.extend([QgsPointXY(part[0]), QgsPointXY(part[-1])])
            return result
        result = []
        for child in geometry.asGeometryCollection():
            result.extend(centerlineshifting._point_hits(child))
        return result

    @staticmethod
    def _bend_angle(line, position, interval, total_length):
        """Largest successive heading change inside +/- one chainage interval.

        Segment sampling step = interval/3 (internal, not user configurable).
        Coordinates are sampled by distance along the NEW centerline.
        """
        lower = max(0.0, position - interval)
        upper = min(total_length, position + interval)
        if upper - lower <= 1e-8:
            return None
        spacing = interval / 3.0
        positions = [lower]
        index = 1
        while lower + index * spacing < upper - 1e-8:
            positions.append(lower + index * spacing)
            index += 1
        positions.append(upper)
        # Add the actual chainage position, for a more local check.
        if lower + 1e-8 < position < upper - 1e-8:
            positions.append(position)
        positions = sorted(set(round(p, 9) for p in positions))
        coords = []
        for chainage in positions:
            point_geometry = line.interpolate(chainage)
            if point_geometry.isEmpty():
                return None
            coords.append(QgsPointXY(point_geometry.asPoint()))
        headings = []
        for a, b in zip(coords, coords[1:]):
            dx, dy = b.x() - a.x(), b.y() - a.y()
            if math.hypot(dx, dy) <= 1e-8:
                continue
            headings.append(math.atan2(dx, dy))
        if len(headings) < 2:
            return None
        return max(abs(math.degrees(math.atan2(
            math.sin(b - a), math.cos(b - a))))
                   for a, b in zip(headings, headings[1:]))

    def processAlgorithm(self, parameters, context, feedback):
        started = time.perf_counter()
        newer = self.parameterAsVectorLayer(parameters, self.NEWCL, context)
        older = self.parameterAsVectorLayer(parameters, self.OLDCL, context)
        interval = self.parameterAsInt(parameters, self.INTERVAL, context)
        mode = self.parameterAsEnum(parameters, self.SEARCH_METHOD, context)
        manual_distance = self.parameterAsDouble(parameters, self.SEARCH_DISTANCE, context)
        bend_threshold = self.parameterAsDouble(parameters, self.BEND_THRESHOLD, context)
        spike_multiplier = self.parameterAsDouble(parameters, self.SPIKE_MULTIPLIER, context)
        progression_min = self.parameterAsDouble(parameters, self.PROGRESSION_MIN, context)
        progression_max = self.parameterAsDouble(parameters, self.PROGRESSION_MAX, context)

        new_geom = self._valid_line(newer, 'New centerline')
        self._valid_line(older, 'Old centerline')
        if interval <= 0:
            raise QgsProcessingException('Chainage interval must be positive.')
        if mode not in (0, 1):
            raise QgsProcessingException('Invalid transect search mode.')
        if mode == 1 and (not math.isfinite(manual_distance) or manual_distance <= 0):
            raise QgsProcessingException('Manual search distance must be positive and finite.')
        if not math.isfinite(bend_threshold) or not (0 < bend_threshold <= 180):
            raise QgsProcessingException('Bend threshold must be between 0 and 180 degrees.')
        if not math.isfinite(spike_multiplier) or spike_multiplier < 1:
            raise QgsProcessingException('Spike multiplier must be finite and at least 1.')

        if (not math.isfinite(progression_min) or not math.isfinite(progression_max)
                or progression_min < 0 or progression_min >= progression_max):
            raise QgsProcessingException(
                'Centerline progression factors must satisfy 0 <= lower < upper.')

        specified = parameters.get(self.OUTPUT_CRS)
        if specified is None or not str(specified).strip():
            working_crs = newer.crs()
        else:
            working_crs = self.parameterAsCrs(parameters, self.OUTPUT_CRS, context)
        if (not working_crs.isValid() or working_crs.isGeographic()
                or working_crs.mapUnits() != QgsUnitTypes.DistanceMeters):
            raise QgsProcessingException(
                'Working CRS must be a valid projected CRS with metre units.')

        feedback.pushInfo('RivaPlan | Centerline shifting')
        feedback.pushInfo('Working CRS: {}'.format(
            working_crs.authid() or working_crs.description()))
        feedback.pushInfo('Chainage interval: {} m'.format(interval))
        feedback.pushInfo('Sharp-bend threshold: {:.3f} degrees'.format(bend_threshold))
        feedback.pushInfo('Shift-spike multiplier: {:.3f}'.format(spike_multiplier))
        feedback.pushInfo('Old-centerline progression limits: {:.2f}-{:.2f} x chainage interval'.format(
            progression_min, progression_max))
        fb = QgsProcessingMultiStepFeedback(5, feedback)
        child = _QuietFeedback(fb)

        fb.setCurrentStep(0)
        prepared = {}
        for name, layer in [('new', newer), ('old', older)]:
            if fb.isCanceled():
                return {}
            if layer.crs() == working_crs:
                prepared[name] = layer
            else:
                output = processing.run('native:reprojectlayer', {
                    'INPUT': layer, 'TARGET_CRS': working_crs,
                    'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT},
                    context=context, feedback=child, is_child_algorithm=True)['OUTPUT']
                prepared[name] = QgsProcessingUtils.mapLayerFromString(output, context)
            prepared[name + '_geom'] = self._valid_line(
                prepared[name], name.capitalize() + ' centerline')
        new_geom = prepared['new_geom']
        old_geom = prepared['old_geom']
        total_length = new_geom.length()
        # Align the OLD centerline's distance coordinate with the NEW centerline.
        # Only the reference direction is reversed, never the input layer or
        # the original intersection geometry.
        old_start = QgsPointXY(old_geom.interpolate(0).asPoint())
        old_end = QgsPointXY(old_geom.interpolate(old_geom.length()).asPoint())
        new_start = QgsPointXY(new_geom.interpolate(0).asPoint())
        reversed_old = (new_start.distance(old_end) < new_start.distance(old_start))
        old_total = old_geom.length()
        preparation_seconds = time.perf_counter() - started
        feedback.pushInfo('Input geometry validation: PASS')

        fb.setCurrentStep(1)
        bounds = new_geom.boundingBox()
        bounds.combineExtentWith(old_geom.boundingBox())
        diagonal = math.hypot(bounds.width(), bounds.height())
        search_half = diagonal if mode == 0 else manual_distance
        if not math.isfinite(search_half) or search_half <= 0:
            raise QgsProcessingException('Transect search distance is invalid.')
        feedback.pushInfo('Transect method: {}'.format(
            'Automatic (full diagonal on each side)' if mode == 0
            else 'Manual (distance on each side)'))
        feedback.pushInfo('Search distance on each side: {:.3f} m'.format(search_half))
        feedback.pushInfo('Total search transect length: {:.3f} m'.format(2 * search_half))

        points_path = processing.run('native:pointsalonglines', {
            'INPUT': prepared['new'], 'DISTANCE': interval,
            'START_OFFSET': 0, 'END_OFFSET': 0,
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT},
            context=context, feedback=child, is_child_algorithm=True)['OUTPUT']
        points_layer = QgsProcessingUtils.mapLayerFromString(points_path, context)
        if points_layer is None:
            raise QgsProcessingException('Could not load generated chainage points.')
        points = list(points_layer.getFeatures())
        if not points:
            raise QgsProcessingException('No chainage points were generated.')
        transect_seconds = time.perf_counter() - started - preparation_seconds

        fb.setCurrentStep(2)
        # Store intermediate records to perform spike QC with +/- five
        # adjacent chainage positions, excluding the current observation.
        records = []
        for feature in points:
            if fb.isCanceled():
                return {}
            geometry = feature.geometry()
            if geometry.isEmpty():
                raise QgsProcessingException('A generated chainage point has empty geometry.')
            origin = QgsPointXY(geometry.asPoint())
            chainage = float(feature['distance'])
            angle = float(feature['angle'])
            if not math.isfinite(angle):
                raise QgsProcessingException(
                    'Invalid transect angle at {:.3f} m.'.format(chainage))

            # QGIS pointsalonglines angle is used in the original script.
            # The same angle - 90 expression is retained for perpendiculars.
            direction = math.radians(angle - 90.0)
            ux, uy = math.sin(direction), math.cos(direction)
            minus = QgsPointXY(origin.x() - search_half * ux,
                               origin.y() - search_half * uy)
            plus = QgsPointXY(origin.x() + search_half * ux,
                              origin.y() + search_half * uy)
            transect = QgsGeometry.fromPolylineXY([minus, plus])
            intersect = transect.intersection(old_geom) if transect.intersects(old_geom) else None
            candidates = []
            if intersect is not None and not intersect.isEmpty():
                for hit in self._point_hits(intersect):
                    signed = (hit.x() - origin.x()) * ux + (hit.y() - origin.y()) * uy
                    if abs(signed) <= search_half + 1e-6:
                        if not any(abs(signed - old[0]) <= 1e-6 for old in candidates):
                            candidates.append((signed, hit))
            # Nearest intersection ALONG the same perpendicular transect.
            # Multiple intersections are normal and are not a QC flag.
            selected = min(candidates, key=lambda item: abs(item[0])) if candidates else None
            max_bend = self._bend_angle(new_geom, chainage, interval, total_length)
            bend_flag = max_bend is not None and max_bend > bend_threshold

            if selected is None:
                out_geom = transect
                shift = None
                direction_label = 'No Intersection'
                signed_shift = None
            else:
                signed_distance, hit = selected
                old_pos = old_geom.lineLocatePoint(QgsGeometry.fromPointXY(hit))
                if old_pos >= 0 and reversed_old:
                    old_pos = old_total - old_pos
                out_geom = QgsGeometry.fromPolylineXY([origin, hit])
                shift = float(abs(signed_distance))
                if shift <= 1e-8:
                    shift = 0.0
                    direction_label = 'No Shift'
                    signed_shift = 0.0
                else:
                    # Preserve original QGIS direction convention:
                    # sin(azimuth(new->old) - radians(NewAngle)) > 0 => Left.
                    azimuth_to_old = math.atan2(hit.x() - origin.x(), hit.y() - origin.y())
                    is_left = math.sin(azimuth_to_old - math.radians(angle)) > 0
                    direction_label = 'Left' if is_left else 'Right'
                    signed_shift = -shift if is_left else shift
            records.append({
                'chainage': chainage, 'angle': angle,
                'geometry': out_geom, 'shift': shift,
                'direction': direction_label, 'signed': signed_shift,
                'sharp_bend': bend_flag, 'bend_angle': max_bend,
                'spike': False,
                'old_pos': old_pos if selected is not None and old_pos >= 0 else None,
                'old_step': None, 'progression_flag': False,
            })

        intersection_seconds = time.perf_counter() - started - preparation_seconds - transect_seconds
        fb.setCurrentStep(3)
        records.sort(key=lambda record: record['chainage'])
        counts = Counter()
        # The nearest transect intersection stays selected. Only its
        # longitudinal progression on the OLD centerline is assessed.
        # A missing previous intersection prevents a step assessment;
        # no distant chainage is substituted.
        for index, record in enumerate(records):
            if index > 0:
                previous = records[index - 1]
                if (record['old_pos'] is not None and previous['old_pos'] is not None):
                    step = record['old_pos'] - previous['old_pos']
                    record['old_step'] = step
                    reference_step = record['chainage'] - previous['chainage']
                    if reference_step > 0 and not (progression_min * reference_step <= step <= progression_max * reference_step):
                        record['progression_flag'] = True
                else:
                    counts['progression_not_evaluated'] += 1

            if record['shift'] is not None:
                neighbour_shifts = [records[j]['shift']
                    for j in range(max(0, index - 5), min(len(records), index + 6))
                    if j != index and records[j]['shift'] is not None]
                if len(neighbour_shifts) >= 3:
                    local_median = statistics.median(neighbour_shifts)
                    if (record['shift'] > spike_multiplier * local_median
                            and record['shift'] - local_median > 0.01):
                        record['spike'] = True
                else:
                    counts['spike_not_evaluated'] += 1

            flags = []
            if record['sharp_bend']:
                flags.append('SHARP_BEND')
            if record['spike']:
                flags.append('SHIFT_SPIKE')
            if record['progression_flag']:
                flags.append('IRREGULAR_PROGRESSION')
            record['flags'] = ';'.join(flags)
            record['qc'] = ('NO_INTERSECTION' if record['shift'] is None
                            else 'REVIEW' if flags else 'VALID')
            counts[record['qc']] += 1
            for flag in flags:
                counts[flag] += 1

        fields = QgsFields()
        fields.append(QgsField('Chainage', QMetaType.Type.Double))
        fields.append(QgsField('Shift', QMetaType.Type.Double))
        fields.append(QgsField('Direction', QMetaType.Type.QString, len=20))
        fields.append(QgsField('SignedShift', QMetaType.Type.Double))
        fields.append(QgsField('QC_Status', QMetaType.Type.QString, len=20))
        fields.append(QgsField('QC_Flags', QMetaType.Type.QString, len=120))
        fields.append(QgsField('OldStep', QMetaType.Type.Double))
        fields.append(QgsField('SearchLen', QMetaType.Type.Int))
        sink, output_id = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields,
            QgsWkbTypes.LineString, working_crs)
        if sink is None:
            raise QgsProcessingException('Could not create centerline-shifting output.')
        for record in records:
            feature = QgsFeature(fields)
            feature.setGeometry(record['geometry'])
            feature.setAttributes([
                round(record['chainage'], 3),
                round(record['shift'], 3) if record['shift'] is not None else None,
                record['direction'],
                round(record['signed'], 3) if record['signed'] is not None else None,
                record['qc'], record['flags'],
                round(record['old_step'], 3) if record['old_step'] is not None else None,
                int(round(2 * search_half)),
            ])
            if not sink.addFeature(feature, QgsFeatureSink.FastInsert):
                raise QgsProcessingException('Cannot write output at {:.3f} m.'.format(
                    record['chainage']))
        del sink
        qc_seconds = time.perf_counter() - started - preparation_seconds - transect_seconds - intersection_seconds
        fb.setCurrentStep(4)
        details = context.layerToLoadOnCompletionDetails(output_id)
        details.name = 'Centerline_Shifting'
        total_seconds = time.perf_counter() - started
        feedback.pushInfo('--- RivaPlan centerline shifting quality checks ---')
        feedback.pushInfo('Chainage points: {}'.format(len(records)))
        for status in ['VALID', 'REVIEW', 'NO_INTERSECTION', 'SHARP_BEND', 'SHIFT_SPIKE', 'IRREGULAR_PROGRESSION']:
            feedback.pushInfo('{}: {}'.format(status, counts[status]))
        if counts['spike_not_evaluated']:
            feedback.pushInfo('Spike QC not evaluated (insufficient neighbours): {}'.format(
                counts['spike_not_evaluated']))
        if counts['progression_not_evaluated']:
            feedback.pushInfo('Progression QC unavailable at {} adjacent positions (missing intersections).'.format(
                counts['progression_not_evaluated']))
        if counts['NO_INTERSECTION']:
            feedback.pushWarning(
                '{} transects did not meet the old centerline; full search lines '
                'were retained with NULL shift.'.format(counts['NO_INTERSECTION']))
            for record in records:
                if record['qc'] == 'NO_INTERSECTION':
                    feedback.pushWarning('No intersection at chainage {:.3f} m'.format(
                        record['chainage']))
        feedback.pushInfo('--- RivaPlan centerline shifting processing ---')
        feedback.pushInfo('1. Input preparation: {:.3f} s'.format(preparation_seconds))
        feedback.pushInfo('2. Transect setup: {:.3f} s'.format(transect_seconds))
        feedback.pushInfo('3. Intersection and shift calculation: {:.3f} s'.format(intersection_seconds))
        feedback.pushInfo('4. QC and output preparation: {:.3f} s'.format(qc_seconds))
        feedback.pushInfo('Total centerline shifting: {:.3f} s'.format(total_seconds))
        feedback.pushInfo('Centerline shifting completed successfully.')
        return {self.OUTPUT: output_id}
