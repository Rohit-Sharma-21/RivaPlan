# -*- coding: utf-8 -*-
"""RivaPlan | Bankline Shifting, advanced controls and longitudinal QC.

Run as a QGIS Processing Script. All metric computations are planar in the
selected projected/metre CRS. QC is diagnostic; valid measured shifts are
never removed due to a sharp bend or a local spike.
"""

import math
import statistics
import time
from collections import Counter

from qgis.PyQt.QtCore import QCoreApplication, QMetaType
from qgis.core import (
    QgsCoordinateTransform, QgsFeature, QgsFeatureSink, QgsField, QgsFields,
    QgsGeometry, QgsPointXY, QgsProcessing, QgsProcessingAlgorithm,
    QgsProcessingException, QgsProcessingParameterCrs,
    QgsProcessingParameterDefinition, QgsProcessingParameterEnum, QgsProcessingParameterFeatureSink,
    QgsProcessingParameterNumber, QgsProcessingParameterVectorLayer,
    QgsUnitTypes, QgsWkbTypes,
)


class banklineshifting(QgsProcessingAlgorithm):
    NEWBANKLINE = 'NEWBANKLINE'
    OLDBANKLINE = 'OLDBANKLINE'
    INTERVAL = 'INTERVAL'
    CENTERLINE = 'CENTERLINE'
    OUTPUT_CRS = 'CRS'
    BANK = 'BANK'
    SEARCH_METHOD = 'SEARCH_METHOD'
    SEARCH_DISTANCE = 'SEARCH_DISTANCE'
    BEND_THRESHOLD = 'BEND_THRESHOLD'
    SPIKE_MULTIPLIER = 'SPIKE_MULTIPLIER'
    BANK_LOWER = 'BANK_LOWER'
    BANK_UPPER = 'BANK_UPPER'
    OUTPUT = 'OUTPUT'

    def tr(self, value):
        return QCoreApplication.translate('Processing', value)

    def createInstance(self):
        return banklineshifting()

    def name(self):
        return 'shifting_distance'

    def displayName(self):
        return self.tr('Bankline Shifting')

    def group(self):
        return self.tr(self.groupId())

    def groupId(self):
        return ''

    def shortHelpString(self):
        return self.tr("""<h3>RivaPlan | Bankline Shifting</h3>
<p>Measures historical-to-recent bankline displacement along perpendicular transects drawn at regular chainages on a reference centerline. Results are <b>LINE features</b> joining the selected old and new bankline intersections. Select <b>Left Bank</b> or <b>Right Bank</b> for the corresponding output layer name.</p>
<h4>Input parameters</h4>
<ul>
<li><b>Chainage interval (m):</b> Spacing between reference centerline sampling points. Must be greater than zero. Default: 100 m.</li>
<li><b>Reference centerline:</b> One valid, continuous line with clear upstream-to-downstream orientation. Chainages and perpendicular directions are measured along this line.</li>
<li><b>New bankline:</b> Bank position for the more recent observation date. Select only the bank corresponding to the chosen Left/Right Bank setting.</li>
<li><b>Old bankline:</b> Bank position for the historical observation date. Select the same side as the new bankline.</li>
<li><b>Working CRS:</b> Optional projected CRS with metre units. When not supplied, the reference centerline's CRS is used. Other inputs are transformed into that CRS.</li>
<li><b>Select the bank:</b> Left Bank or Right Bank (looking downstream). This choice identifies the bank and controls the inward/outward sign convention and output layer name.</li>
<li><b>Transect search method:</b> Automatic computes the full bounding-box diagonal of all inputs as the search distance <i>on each side</i> of each chainage. Manual uses the entered distance on each side.</li>
<li><b>Manual search distance on EACH side (m):</b> Used only in Manual mode. For example, 500 m creates a 1,000 m full-length transect.</li>
<li><b>Bankline Shifting (Displacement Lines):</b> Destination for output lines. The displayed layer name is Left Bank Shifting or Right Bank Shifting.</li>
</ul>
<h4>Advanced Parameters (quality control)</h4>
<ul>
<li><b>Sharp-bend threshold (degrees), default 10:</b> The reference centerline is inspected one chainage interval upstream and downstream. Local directions are sampled internally at one-third of the interval. A change greater than the threshold adds SHARP_BEND.</li>
<li><b>Shift-spike multiplier, default 2:</b> The measured shift is compared with the median of up to five adjacent chainages on each side, excluding the current chainage. Values exceeding the selected multiplier are flagged SHIFT_SPIKE.</li>
<li><b>Bankline progression lower factor, default 0.5:</b> Minimum expected forward distance along either bankline between consecutive selected intersections, relative to the actual centerline chainage step.</li>
<li><b>Bankline progression upper factor, default 1.5:</b> Maximum expected forward distance along either bankline between consecutive selected intersections. Values outside either limit add IRREGULAR_PROGRESSION.</li>
</ul>
<h4>Intersection selection and quality checks</h4>
<ul>
<li>All historical and recent crossings along each perpendicular transect are examined. The nearest crossing to the originating chainage point <b>along that transect</b> is selected independently for each bankline; QC never changes the selected crossings.</li>
<li>Multiple crossings are normal and are counted for diagnosis. If two distinct crossings are equally near within numerical tolerance, the result is INVALID_PAIR with NULL shift.</li>
<li>SHARP_BEND, SHIFT_SPIKE and IRREGULAR_PROGRESSION are <b>review flags only</b>. They never alter displacement measurements.</li>
<li>IRREGULAR_PROGRESSION compares the progression of intersections <b>along actual old and new bankline geometry</b>, not their straight-line distance. The tool attempts to orient bankline routes consistently with the reference centerline.</li>
<li>When a bankline cannot be assembled into one continuous line, longitudinal QC is unavailable for that bankline and IRREGULAR_PROGRESSION_UNAVAILABLE is recorded in QC_Flags; shifting can still be calculated.</li>
<li>A missing old or new crossing returns NO_INTERSECTION and a NULL shift, preserving the complete search transect for visual inspection.</li>
</ul>
<h4>Output attributes and interpretation</h4>
<ul>
<li><b>Chainage:</b> Reference centerline distance (m).</li>
<li><b>Shift / SignedShift:</b> Absolute displacement and signed lateral displacement (m). Negative indicates downstream-left and positive indicates downstream-right movement from the old to the new bankline, relative to an upstream-to-downstream centerline.</li>
<li><b>BankChange:</b> Signed bank adjustment (m): negative is outward (away from the channel interior), positive is inward (toward the channel interior). This is determined using the selected left/right bank, not simply by comparing unsigned distances to the centerline.</li>
<li><b>BankStatus:</b> OUTWARD, INWARD or NO_CHANGE. These geometric labels do not independently establish erosion or deposition.</li>
<li><b>Direction:</b> Downstream-left, downstream-right (displayed Left/Right), or No Shift. Verify the reference centerline flows from upstream to downstream before analysis.</li>
<li><b>QC_Status:</b> VALID, REVIEW, NO_INTERSECTION or INVALID_PAIR; <b>QC_Flags</b> lists individual review reasons.</li>
<li><b>SearchLen:</b> Total full-transect search length (m); <b>N_Old</b> and <b>N_New</b> count all candidate intersections.</li>
<li><b>BendDeg / NeighMed:</b> Maximum local direction change (degrees) and local median shift (m).</li>
<li><b>OldStep / NewStep:</b> Along-bankline progression (m) from the preceding sampled chainage where available.</li>
</ul>
<p><b>Important:</b> Verify that the reference centerline is oriented upstream to downstream, and that the Old/New bankline inputs correspond to the selected bank and dates. A QC flag does not establish that a geomorphic change is erroneous. Inspect flagged transects and banklines before scientific interpretation.</p>""")

    def _add_advanced(self, parameter):
        parameter.setFlags(parameter.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(parameter)

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterNumber(
            self.INTERVAL, self.tr('Chainage interval (m)'),
            type=QgsProcessingParameterNumber.Integer, defaultValue=100,
            minValue=1))
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.CENTERLINE, self.tr('Reference centerline'),
            types=[QgsProcessing.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.NEWBANKLINE, self.tr('New bankline'),
            types=[QgsProcessing.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.OLDBANKLINE, self.tr('Old bankline'),
            types=[QgsProcessing.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterCrs(
            self.OUTPUT_CRS,
            self.tr('Working CRS (optional; defaults to reference centerline CRS)'),
            defaultValue=None, optional=True))
        self.addParameter(QgsProcessingParameterEnum(
            self.BANK, self.tr('Select the bank'),
            options=['Left Bank', 'Right Bank'], defaultValue=0))
        self.addParameter(QgsProcessingParameterEnum(
            self.SEARCH_METHOD, self.tr('Transect search method'),
            options=['Automatic: full extent diagonal on EACH side',
                     'Manual: search distance on EACH side'], defaultValue=0))
        self.addParameter(QgsProcessingParameterNumber(
            self.SEARCH_DISTANCE,
            self.tr('Manual search distance on EACH side (m)'),
            type=QgsProcessingParameterNumber.Double, defaultValue=500.0,
            minValue=0.001, optional=True))
        self._add_advanced(QgsProcessingParameterNumber(
            self.BEND_THRESHOLD, self.tr('Sharp-bend threshold (degrees)'),
            type=QgsProcessingParameterNumber.Double, defaultValue=10.0,
            minValue=0.001, maxValue=180.0))
        self._add_advanced(QgsProcessingParameterNumber(
            self.SPIKE_MULTIPLIER, self.tr('Shift-spike multiplier'),
            type=QgsProcessingParameterNumber.Double, defaultValue=2.0,
            minValue=1.0))
        self._add_advanced(QgsProcessingParameterNumber(
            self.BANK_LOWER, self.tr('Bankline progression lower factor'),
            type=QgsProcessingParameterNumber.Double, defaultValue=0.5,
            minValue=0.0))
        self._add_advanced(QgsProcessingParameterNumber(
            self.BANK_UPPER, self.tr('Bankline progression upper factor'),
            type=QgsProcessingParameterNumber.Double, defaultValue=1.5,
            minValue=0.001))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr('Bankline Shifting (Displacement Lines)'),
            type=QgsProcessing.TypeVectorLine,
            createByDefault=True, supportsAppend=False))

    @staticmethod
    def _load_lines(layer, label, working_crs, context, one_continuous=False):
        if layer is None or not layer.isValid() or not layer.crs().isValid():
            raise QgsProcessingException('{} layer or CRS is invalid.'.format(label))
        transform = None
        if layer.crs() != working_crs:
            transform = QgsCoordinateTransform(layer.crs(), working_crs,
                                               context.transformContext())
        geometries = []
        for feature in layer.getFeatures():
            if not feature.hasGeometry() or feature.geometry().isEmpty():
                raise QgsProcessingException('{} contains empty geometry (feature {}).'.format(
                    label, feature.id()))
            geom = QgsGeometry(feature.geometry())
            if geom.type() != QgsWkbTypes.LineGeometry or not geom.isGeosValid():
                raise QgsProcessingException('{} contains invalid line geometry (feature {}).'.format(
                    label, feature.id()))
            if transform is not None:
                try:
                    geom.transform(transform)
                except Exception as exc:
                    raise QgsProcessingException(
                        '{} could not be reprojected: {}'.format(label, exc))
            if geom.length() <= 1e-9 or not geom.isGeosValid():
                raise QgsProcessingException('{} has an invalid/zero-length metric geometry.'.format(label))
            if one_continuous:
                if geom.isMultipart():
                    parts = geom.asMultiPolyline()
                    if len(parts) != 1:
                        raise QgsProcessingException(
                            '{} must be one continuous line; disconnected parts detected.'.format(label))
                    geom = QgsGeometry.fromPolylineXY(parts[0])
                if len(geom.asPolyline()) < 2:
                    raise QgsProcessingException('{} requires at least two vertices.'.format(label))
            geometries.append(geom)
        if not geometries:
            raise QgsProcessingException('{} contains no line features.'.format(label))
        if one_continuous and len(geometries) != 1:
            raise QgsProcessingException(
                '{} must contain exactly one continuous feature with unambiguous chainage.'.format(label))
        return geometries

    @staticmethod
    def _intersection_points(geom):
        """Return all point crossings, with overlap endpoints retained for review."""
        if geom.isNull() or geom.isEmpty():
            return []
        if geom.type() == QgsWkbTypes.PointGeometry:
            pts = geom.asMultiPoint() if geom.isMultipart() else [geom.asPoint()]
            return [QgsPointXY(p) for p in pts]
        if geom.type() == QgsWkbTypes.LineGeometry:
            parts = geom.asMultiPolyline() if geom.isMultipart() else [geom.asPolyline()]
            points = []
            for part in parts:
                if part:
                    points.extend((QgsPointXY(part[0]), QgsPointXY(part[-1])))
            return points
        points = []
        for subgeom in geom.asGeometryCollection():
            points.extend(banklineshifting._intersection_points(subgeom))
        return points

    @staticmethod
    def _bank_route(banks, reference):
        """One oriented continuous route for along-bankline progress QC.

        Disconnected source features do not prevent displacement measurements;
        they only make longitudinal distance QC unavailable.
        """
        try:
            merged = QgsGeometry.unaryUnion(banks)
            if merged.isMultipart():
                merged = merged.mergeLines()
            if merged.isNull() or merged.isEmpty() or merged.type() != QgsWkbTypes.LineGeometry:
                return None
            if merged.isMultipart():
                parts = merged.asMultiPolyline()
                if len(parts) != 1:
                    return None
                route = QgsGeometry.fromPolylineXY(parts[0])
            else:
                route = QgsGeometry.fromPolylineXY(merged.asPolyline())
            if route.length() <= 1e-9:
                return None
            pts = route.asPolyline()
            cl_start = reference.interpolate(0).asPoint()
            cl_end = reference.interpolate(reference.length()).asPoint()
            # Choose the orientation with the smaller combined endpoint error.
            forward = math.hypot(pts[0].x()-cl_start.x(), pts[0].y()-cl_start.y()) + \
                      math.hypot(pts[-1].x()-cl_end.x(), pts[-1].y()-cl_end.y())
            reverse = math.hypot(pts[-1].x()-cl_start.x(), pts[-1].y()-cl_start.y()) + \
                      math.hypot(pts[0].x()-cl_end.x(), pts[0].y()-cl_end.y())
            if reverse < forward:
                route = QgsGeometry.fromPolylineXY(list(reversed(pts)))
            return route
        except Exception:
            return None

    @staticmethod
    def _candidates(transect, banks, origin, ux, uy, length_each_side, bank_route=None):
        """All distinct crossings, ordered by offset ALONG perpendicular.

        Each candidate retains the selected point and its location along the
        full oriented bankline when a continuous bankline route is available.
        """
        offsets = []
        for bank in banks:
            if not bank.boundingBox().intersects(transect.boundingBox()):
                continue
            if not bank.intersects(transect):
                continue
            for p in bankline_shifting_intersection_points(bank.intersection(transect)):
                dx, dy = p.x() - origin.x(), p.y() - origin.y()
                offset = dx * ux + dy * uy
                cross = abs(dx * uy - dy * ux)
                if cross <= 0.001 and abs(offset) <= length_each_side + 0.001:
                    if not any(abs(offset - v['offset']) < 0.001 for v in offsets):
                        measure = None
                        if bank_route is not None:
                            located = bank_route.lineLocatePoint(QgsGeometry.fromPointXY(p))
                            if math.isfinite(located) and located >= 0:
                                measure = float(located)
                        offsets.append({'offset':offset,'point':p,'measure':measure})
        return sorted(offsets, key=lambda v: (abs(v['offset']), v['offset']))

    @staticmethod
    def _pick(candidates):
        if not candidates:
            return None, 'MISSING'
        if len(candidates) > 1 and abs(abs(candidates[0]['offset']) - abs(candidates[1]['offset'])) < 0.001:
            return None, 'TIED_NEAREST'
        return candidates[0], None

    @staticmethod
    def _bend_angle(line, chainage, interval, length):
        low = max(0.0, chainage - interval)
        high = min(length, chainage + interval)
        if high - low < 1e-8:
            return None
        step = interval / 3.0  # Internal method, not exposed to user.
        positions = [low, high, chainage]
        d = low + step
        while d < high - 1e-8:
            positions.append(d)
            d += step
        positions = sorted(set(round(p, 9) for p in positions))
        headings = []
        prior = None
        for distance in positions:
            point_geom = line.interpolate(distance)
            if point_geom.isEmpty():
                return None
            pt = point_geom.asPoint()
            if prior is not None:
                dx, dy = pt.x() - prior.x(), pt.y() - prior.y()
                if math.hypot(dx, dy) > 1e-8:
                    headings.append(math.atan2(dx, dy))
            prior = pt
        if len(headings) < 2:
            return None
        return max(abs(math.degrees(math.atan2(
            math.sin(b - a), math.cos(b - a))))
                   for a, b in zip(headings, headings[1:]))

    @staticmethod
    def _tangent(line, d, length, interval):
        # Short symmetric sampling about chainage, with endpoint fallback.
        eps = min(0.1, interval / 100.0, max(length / 10000.0, 1e-5))
        lo, hi = max(0.0, d - eps), min(length, d + eps)
        a, b = line.interpolate(lo), line.interpolate(hi)
        if a.isEmpty() or b.isEmpty():
            return None
        pa, pb = a.asPoint(), b.asPoint()
        dx, dy = pb.x() - pa.x(), pb.y() - pa.y()
        norm = math.hypot(dx, dy)
        if norm <= 1e-10:
            return None
        return dx / norm, dy / norm

    def processAlgorithm(self, parameters, context, feedback):
        start = time.perf_counter()
        interval = self.parameterAsInt(parameters, self.INTERVAL, context)
        bank_select = self.parameterAsEnum(parameters, self.BANK, context)
        search_method = self.parameterAsEnum(parameters, self.SEARCH_METHOD, context)
        manual = self.parameterAsDouble(parameters, self.SEARCH_DISTANCE, context)
        bend_threshold = self.parameterAsDouble(parameters, self.BEND_THRESHOLD, context)
        spike_factor = self.parameterAsDouble(parameters, self.SPIKE_MULTIPLIER, context)
        progress_low = self.parameterAsDouble(parameters, self.BANK_LOWER, context)
        progress_high = self.parameterAsDouble(parameters, self.BANK_UPPER, context)
        center_layer = self.parameterAsVectorLayer(parameters, self.CENTERLINE, context)
        new_layer = self.parameterAsVectorLayer(parameters, self.NEWBANKLINE, context)
        old_layer = self.parameterAsVectorLayer(parameters, self.OLDBANKLINE, context)

        if interval <= 0:
            raise QgsProcessingException('Chainage interval must be positive.')
        if bank_select not in (0, 1) or search_method not in (0, 1):
            raise QgsProcessingException('Invalid bank or search method.')
        if search_method == 1 and (not math.isfinite(manual) or manual <= 0):
            raise QgsProcessingException('Manual distance on each side must be positive and finite.')
        if not math.isfinite(bend_threshold) or not 0 < bend_threshold <= 180:
            raise QgsProcessingException('Bend threshold must be between 0 and 180 degrees.')
        if not math.isfinite(spike_factor) or spike_factor < 1:
            raise QgsProcessingException('Spike multiplier must be finite and >= 1.')
        if (not math.isfinite(progress_low) or not math.isfinite(progress_high)
                or progress_low < 0 or progress_high <= progress_low):
            raise QgsProcessingException('Bankline progression factors require 0 <= lower < upper.')
        if center_layer is None or not center_layer.crs().isValid():
            raise QgsProcessingException('Reference centerline CRS is invalid.')

        selected_crs = self.parameterAsCrs(parameters, self.OUTPUT_CRS, context)
        explicit_crs = parameters.get(self.OUTPUT_CRS)
        working = selected_crs if explicit_crs and selected_crs.isValid() else center_layer.crs()
        if not working.isValid() or working.isGeographic() or working.mapUnits() != QgsUnitTypes.DistanceMeters:
            raise QgsProcessingException(
                'Working CRS must be a valid projected CRS with metre units.')

        center = self._load_lines(center_layer, 'Reference centerline', working,
                                  context, one_continuous=True)[0]
        new_banks = self._load_lines(new_layer, 'New bankline', working, context)
        old_banks = self._load_lines(old_layer, 'Old bankline', working, context)
        old_route = self._bank_route(old_banks, center)
        new_route = self._bank_route(new_banks, center)
        length = center.length()
        bounds = center.boundingBox()
        for g in new_banks + old_banks:
            bounds.combineExtentWith(g.boundingBox())
        diagonal = math.hypot(bounds.width(), bounds.height())
        half_search = diagonal if search_method == 0 else manual
        if not math.isfinite(half_search) or half_search <= 0:
            raise QgsProcessingException('Transect search distance is invalid.')
        prep_time = time.perf_counter() - start
        feedback.pushInfo('RivaPlan | Bankline Shifting')
        feedback.pushInfo('Bank: {}'.format('Left' if bank_select == 0 else 'Right'))
        feedback.pushInfo('Working CRS: {}'.format(working.authid() or working.description()))
        feedback.pushInfo('Chainage interval: {} m'.format(interval))
        feedback.pushInfo('Search on EACH side: {:.3f} m ({})'.format(
            half_search, 'Automatic' if search_method == 0 else 'Manual'))
        feedback.pushInfo('Sharp-bend threshold: {:.2f} degrees; spike multiplier: {:.2f}'.format(
            bend_threshold, spike_factor))
        feedback.pushInfo('Bankline progression limits: {:.2f} to {:.2f} x chainage step'.format(
            progress_low, progress_high))
        feedback.pushInfo('Geometry validation: PASS')
        if old_route is None or new_route is None:
            feedback.pushWarning('Bankline-distance QC unavailable for {} bankline(s) because '
                'a single continuous route could not be reconstructed.'.format(
                ', '.join(label for label, route in [('old', old_route), ('new', new_route)]
                          if route is None)))

        tick = time.perf_counter()
        chainages = []
        p = 0.0
        while p < length - 1e-8:
            chainages.append(p)
            p += interval
        if not chainages:
            chainages.append(0.0)
        # Include endpoint if it lies exactly on a regular interval; this
        # mirrors regular points-along-lines sampling, no incomplete interval.
        if abs(length - round(length / interval) * interval) < 1e-8 and length > 0:
            if abs(length - chainages[-1]) > 1e-8:
                chainages.append(length)
        if len(chainages) > 2000000:
            raise QgsProcessingException('Too many chainages; increase interval.')
        rows = []
        for index, d in enumerate(chainages):
            if feedback.isCanceled():
                return {}
            origin_geom = center.interpolate(d)
            if origin_geom.isEmpty():
                raise QgsProcessingException('Cannot interpolate centerline at {:.3f} m.'.format(d))
            origin = QgsPointXY(origin_geom.asPoint())
            tangent = self._tangent(center, d, length, interval)
            if tangent is None:
                raise QgsProcessingException('Cannot determine centerline tangent at {:.3f} m.'.format(d))
            tx, ty = tangent
            ux, uy = -ty, tx  # positive side: left of direction of digitisation
            p1 = QgsPointXY(origin.x() - half_search * ux, origin.y() - half_search * uy)
            p2 = QgsPointXY(origin.x() + half_search * ux, origin.y() + half_search * uy)
            transect = QgsGeometry.fromPolylineXY([p1, p2])
            rows.append({'chainage': d, 'origin': origin, 'transect': transect,
                         'ux': ux, 'uy': uy})
            if index % 200 == 0:
                feedback.setProgress(5 + 20 * (index + 1) / len(chainages))
        transect_time = time.perf_counter() - tick

        tick = time.perf_counter()
        for index, row in enumerate(rows):
            old_hits = self._candidates(row['transect'], old_banks, row['origin'],
                                        row['ux'], row['uy'], half_search, old_route)
            new_hits = self._candidates(row['transect'], new_banks, row['origin'],
                                        row['ux'], row['uy'], half_search, new_route)
            row['n_old'], row['n_new'] = len(old_hits), len(new_hits)
            old_offset, old_error = self._pick(old_hits)
            new_offset, new_error = self._pick(new_hits)
            row['flags'] = []
            row['old_step'] = None
            row['new_step'] = None
            row['old_measure'] = None
            row['new_measure'] = None
            row['shift'] = None
            row['signed'] = None
            row['direction'] = None
            row['bank_change'] = None
            row['bank_status'] = None
            if old_error == 'MISSING' or new_error == 'MISSING':
                row['base_qc'] = 'NO_INTERSECTION'
                if old_error == 'MISSING':
                    row['flags'].append('MISSING_OLD')
                if new_error == 'MISSING':
                    row['flags'].append('MISSING_NEW')
            elif old_error == 'TIED_NEAREST' or new_error == 'TIED_NEAREST':
                row['base_qc'] = 'INVALID_PAIR'
                if old_error == 'TIED_NEAREST':
                    row['flags'].append('TIED_OLD')
                if new_error == 'TIED_NEAREST':
                    row['flags'].append('TIED_NEW')
            else:
                row['base_qc'] = 'VALID'
                # ux,uy = -ty,tx points to downstream-left when the
                # reference centerline is digitized upstream -> downstream.
                # Delta offset is therefore positive for physical LEFTWARD
                # movement (old bank -> new bank). Report left-negative,
                # right-positive SignedShift, matching centerline migration.
                delta_offset = new_offset['offset'] - old_offset['offset']
                row['shift'] = abs(delta_offset)
                row['signed'] = -delta_offset
                row['old_offset'] = old_offset['offset']
                row['new_offset'] = new_offset['offset']
                row['old_measure'] = old_offset['measure']
                row['new_measure'] = new_offset['measure']
                row['direction'] = ('No Shift' if row['shift'] <= 1e-9 else
                                    'Left' if delta_offset > 0 else 'Right')
                # Negative BankChange = outward bank retreat (geometric),
                # positive = inward advance, for BOTH selected banks.
                # Left bank: outward is downstream-left (+offset).
                # Right bank: outward is downstream-right (-offset).
                row['bank_change'] = (-delta_offset if bank_select == 0
                                      else delta_offset)
                row['bank_status'] = ('NO_CHANGE' if row['shift'] <= 1e-9 else
                                      'OUTWARD' if row['bank_change'] < 0
                                      else 'INWARD')
                if old_offset['offset'] * new_offset['offset'] < -1e-9:
                    # Real erosion/accretion can cross the reference line.
                    # Keep the measurement but flag for manual inspection.
                    row['flags'].append('CROSSES_REFERENCE')
            if index % 200 == 0:
                feedback.setProgress(25 + 35 * (index + 1) / len(rows))
        intersection_time = time.perf_counter() - tick

        tick = time.perf_counter()
        for i, row in enumerate(rows):
            # Compare only immediately successive chainages, never skip across
            # missing observations. Use each bankline's actual along-line step.
            if i > 0 and row['base_qc'] == 'VALID' and rows[i-1]['base_qc'] == 'VALID':
                previous = rows[i-1]
                actual_step = row['chainage'] - previous['chainage']
                for label in ('old', 'new'):
                    m0, m1 = previous[label+'_measure'], row[label+'_measure']
                    if m0 is not None and m1 is not None:
                        delta = m1 - m0
                        row[label+'_step'] = delta
                        if not (progress_low * actual_step <= delta <= progress_high * actual_step):
                            row['flags'].append('IRREGULAR_PROGRESSION_'+label.upper())
            if row['base_qc'] == 'VALID' and (old_route is None or new_route is None):
                row['flags'].append('IRREGULAR_PROGRESSION_UNAVAILABLE')
            bend = self._bend_angle(center, row['chainage'], interval, length)
            row['bend'] = bend
            sharp = bend is not None and bend > bend_threshold
            neighbours = [rows[j]['shift'] for j in range(max(0, i - 5), min(len(rows), i + 6))
                          if j != i and rows[j]['shift'] is not None]
            median = statistics.median(neighbours) if len(neighbours) >= 3 else None
            row['median'] = median
            spike = (row['shift'] is not None and median is not None and
                     row['shift'] > spike_factor * median and
                     row['shift'] - median >= 0.01)
            if sharp:
                row['flags'].append('SHARP_BEND')
            if spike:
                row['flags'].append('SHIFT_SPIKE')
            if row['base_qc'] != 'VALID':
                row['qc'] = row['base_qc']
            elif any(flag in row['flags'] for flag in (
                    'SHARP_BEND','SHIFT_SPIKE','IRREGULAR_PROGRESSION_OLD','IRREGULAR_PROGRESSION_NEW')):
                row['qc'] = 'REVIEW'
            else:
                row['qc'] = 'VALID'
        qc_time = time.perf_counter() - tick

        tick = time.perf_counter()
        fields = QgsFields()
        for name, field_type in (
            ('Chainage', QMetaType.Type.Double),
            ('Shift', QMetaType.Type.Double),
            ('Direction', QMetaType.Type.QString),
            ('SignedShift', QMetaType.Type.Double),
            ('BankChange', QMetaType.Type.Double),
            ('BankStatus', QMetaType.Type.QString),
            ('QC_Status', QMetaType.Type.QString),
            ('QC_Flags', QMetaType.Type.QString),
            ('SearchLen', QMetaType.Type.Int),
            ('N_Old', QMetaType.Type.Int),
            ('N_New', QMetaType.Type.Int),
            ('BendDeg', QMetaType.Type.Double),
            ('NeighMed', QMetaType.Type.Double),
            ('OldStep', QMetaType.Type.Double),
            ('NewStep', QMetaType.Type.Double),
        ):
            fields.append(QgsField(name, field_type))
        sink, destination = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields, QgsWkbTypes.LineString, working)
        if sink is None:
            raise QgsProcessingException('Could not create bankline shifting output.')
        statuses = Counter()
        count_flags = Counter()
        for i, row in enumerate(rows):
            feature = QgsFeature(fields)
            if row['shift'] is None:
                # Retain the full search transect for unmeasurable chainages.
                feature.setGeometry(row['transect'])
            else:
                oldp = QgsPointXY(
                    row['origin'].x() + row['old_offset'] * row['ux'],
                    row['origin'].y() + row['old_offset'] * row['uy'])
                newp = QgsPointXY(
                    row['origin'].x() + row['new_offset'] * row['ux'],
                    row['origin'].y() + row['new_offset'] * row['uy'])
                feature.setGeometry(QgsGeometry.fromPolylineXY([oldp, newp]))
            feature.setAttributes([
                round(row['chainage'], 3),
                None if row['shift'] is None else round(row['shift'], 3),
                row['direction'],
                None if row['signed'] is None else round(row['signed'], 3),
                None if row['bank_change'] is None else round(row['bank_change'], 3),
                row['bank_status'],
                row['qc'], ';'.join(row['flags']),
                int(round(2 * half_search)), row['n_old'], row['n_new'],
                None if row['bend'] is None else round(row['bend'], 3),
                None if row['median'] is None else round(row['median'], 3),
                None if row['old_step'] is None else round(row['old_step'], 3),
                None if row['new_step'] is None else round(row['new_step'], 3),
            ])
            if not sink.addFeature(feature, QgsFeatureSink.FastInsert):
                raise QgsProcessingException('Failed writing output feature.')
            statuses[row['qc']] += 1
            count_flags.update(row['flags'])
            if i % 200 == 0:
                feedback.setProgress(65 + 35 * (i + 1) / len(rows))
        del sink
        output_time = time.perf_counter() - tick

        feedback.pushInfo('--- RivaPlan bankline shifting QC ---')
        feedback.pushInfo('Total chainages: {}'.format(len(rows)))
        for status in ('VALID', 'REVIEW', 'NO_INTERSECTION', 'INVALID_PAIR'):
            feedback.pushInfo('{}: {}'.format(status, statuses[status]))
        for flag in ('SHARP_BEND','SHIFT_SPIKE','IRREGULAR_PROGRESSION_OLD','IRREGULAR_PROGRESSION_NEW',
                     'IRREGULAR_PROGRESSION_UNAVAILABLE'):
            feedback.pushInfo('{}: {}'.format(flag, count_flags[flag]))
        feedback.pushInfo('Multiple old/new crossings (diagnostic only): {}/{}'.format(
            sum(r['n_old'] > 1 for r in rows), sum(r['n_new'] > 1 for r in rows)))
        feedback.pushInfo('Reference-centerline crossing flags: {}'.format(
            count_flags['CROSSES_REFERENCE']))
        problem_rows = [r for r in rows if r['qc'] in ('NO_INTERSECTION', 'INVALID_PAIR')]
        if problem_rows:
            feedback.pushWarning('No/invalid intersection pairs at chainages (m): {}'.format(
                ', '.join('{:.0f} [{}]'.format(r['chainage'], ','.join(r['flags']))
                          for r in problem_rows[:30]) +
                (' ... ({} additional)'.format(len(problem_rows) - 30)
                 if len(problem_rows) > 30 else '')))
        flagged = [r for r in rows if 'IRREGULAR_PROGRESSION_OLD' in r['flags'] or 'IRREGULAR_PROGRESSION_NEW' in r['flags']]
        if flagged:
            feedback.pushInfo('Bankline progression QC chainages (m): {}'.format(
                ', '.join('{:.0f}'.format(r['chainage']) for r in flagged[:35]) +
                (' ... ({} more)'.format(len(flagged)-35) if len(flagged)>35 else '')))
        feedback.pushInfo('--- RivaPlan processing times ---')
        feedback.pushInfo('Input preparation: {:.3f} s'.format(prep_time))
        feedback.pushInfo('Transect generation: {:.3f} s'.format(transect_time))
        feedback.pushInfo('Intersection processing: {:.3f} s'.format(intersection_time))
        feedback.pushInfo('QC assessment: {:.3f} s'.format(qc_time))
        feedback.pushInfo('Output writing: {:.3f} s'.format(output_time))
        feedback.pushInfo('Total: {:.3f} s'.format(time.perf_counter() - start))
        feedback.pushInfo('Bankline shifting completed.')
        try:
            details = context.layerToLoadOnCompletionDetails(destination)
            details.name = ('Left Bank Shifting' if bank_select == 0
                            else 'Right Bank Shifting')
        except (KeyError, AttributeError):
            pass
        return {self.OUTPUT: destination}


def bankline_shifting_intersection_points(geometry):
    """Small wrapper kept separate for straightforward debugging in QGIS."""
    return banklineshifting._intersection_points(geometry)
