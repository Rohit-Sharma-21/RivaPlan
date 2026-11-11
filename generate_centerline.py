# -*- coding: utf-8 -*-
"""
Bank lines to centerline (QGIS Processing Algorithm)

This tool creates a river centerline using left and right bank polylines.
Method:
1) Extract four bank endpoints and choose the shortest geometrically valid cross-bank pairing.
2) Merge the banks and end caps to create a closed river-channel polygon.
3) Merge and densify the banklines at a user-selected interval (5 m default).
4) Create Voronoi polygons from the bank vertices and convert them to Voronoi lines.
5) Clip the Voronoi lines to the river-channel polygon before exploding and cleaning them.
6) Create a dissolved exclusion buffer around both banks.
7) Retain only exploded Voronoi segments disjoint from the bank buffer.
8) Use the retained Voronoi segments directly as an undirected graph.
9) Use the graph node nearest to each end-cap midpoint as the start and end node.
10) Trace one continuous start-to-end route using Dijkstra's algorithm.

Notes:
- Densification defaults to 5 m and is adjustable.
- Intermediate layers are handled as temporary QGIS outputs.
- A fixed 0.50 metre bank-exclusion distance is used internally.
- The working CRS must be projected with metre units; no region-specific CRS is assumed.
"""

import heapq
import time
import math

from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (QgsProcessing,
                       QgsFeature,
                       QgsField,
                       QgsGeometry,
                       QgsPointXY,
                       QgsCoordinateTransform,
                       QgsProject,
                       QgsWkbTypes,
                       QgsFeatureSink,
                       QgsVectorLayer,
                       QgsExpression,
                       QgsProcessingException,
                       QgsProcessingAlgorithm,
    QgsProcessingParameterDefinition,
                       QgsProcessingParameterCrs,
                       QgsProcessingParameterFeatureSource,
                       QgsProcessingParameterVectorLayer,
                       QgsProcessingParameterFeatureSink,
                       QgsProcessingUtils,
                       QgsProcessingMultiStepFeedback,
                       QgsProcessingFeedback,
                       QgsProcessingParameterNumber,
                       QgsUnitTypes)
from qgis import processing


class _QuietChildFeedback(QgsProcessingFeedback):
    """Suppress verbose native child output while preserving cancellation/errors."""

    def __init__(self, parent):
        super().__init__()
        self._parent = parent

    def isCanceled(self):
        return self._parent.isCanceled()

    def pushInfo(self, info):
        pass

    def pushCommandInfo(self, info):
        pass

    def pushDebugInfo(self, info):
        pass

    def pushConsoleInfo(self, info):
        pass

    def reportError(self, error, fatalError=False):
        self._parent.reportError(error, fatalError)


class mergingbanklines(QgsProcessingAlgorithm):
    LEFTBANK = 'LEFTBANK'
    RIGHTBANK = 'RIGHTBANK'
    OUTPUT_CRS = 'CRS'
    OUTPUT = 'OUTPUT'
    DENSIFY_INTERVAL = 'DENSIFY_INTERVAL'

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return mergingbanklines()

    def name(self):
        return 'make_centerline'

    def displayName(self):
        return self.tr('Bank lines to centerline')

    def group(self):
        return self.tr(self.groupId())

    def groupId(self):
        return ''

    def shortHelpString(self):
        return self.tr("""<h3>Bank lines to centerline</h3>
<p>Generates a river centerline from the left and right banklines.</p>
<h4>Parameters</h4><ul>
<li><b>Left bank / Right bank:</b> Riverbank boundary lines.</li>
</ul><h4>Advanced Parameters</h4><ul>
<li><b>Working CRS:</b> Optional projected CRS in metres; defaults to the left bank CRS.</li>
<li><b>Bankline densification interval:</b> Spacing used to densify banklines (m); default 5 m.</li>
</ul><h4>Output</h4><p>Generated river centerline.</p>
<p>For detailed documentation, see the <a href="https://github.com/Rohit-Sharma-21/RivaPlan">RivaPlan GitHub page</a>.</p>""")

    def initAlgorithm(self, config=None):

        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.LEFTBANK,
                self.tr('Input Left Bank Layer'),
                [QgsProcessing.TypeVectorLine]))

        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.RIGHTBANK,
                self.tr('Input Right Bank Layer'),
                [QgsProcessing.TypeVectorLine]))

        self.addParameter(
            QgsProcessingParameterCrs(
                self.OUTPUT_CRS,
                self.tr('Working CRS (optional; defaults to left bank CRS)'),
                defaultValue=None, optional=True))

        self.addParameter(
            QgsProcessingParameterNumber(
                self.DENSIFY_INTERVAL,
                self.tr('Bankline densification interval (m)'),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=5.0, minValue=0.001))

        self.addParameter(
            QgsProcessingParameterFeatureSink(
                'Centerline',  # self.OUTPUT
                self.tr('Centerline'),
                type=QgsProcessing.TypeVectorAnyGeometry,
                createByDefault=True, supportsAppend=True, defaultValue=None))

        # Optional settings appear under the collapsible Advanced Parameters section.
        parameter = self.parameterDefinition(self.OUTPUT_CRS)
        parameter.setFlags(parameter.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        parameter = self.parameterDefinition(self.DENSIFY_INTERVAL)
        parameter.setFlags(parameter.flags() | QgsProcessingParameterDefinition.FlagAdvanced)

    # ------------------------- Main algorithm -------------------------
    def processAlgorithm(self, parameters, context, feedback):
        started_at = time.perf_counter()
        outputs = {}
        results = {}

        bank_exclusion_distance = 0.50  # metres; unchanged algorithm constant
        densify_interval = self.parameterAsDouble(
            parameters, self.DENSIFY_INTERVAL, context)
        if not math.isfinite(densify_interval) or densify_interval <= 0:
            raise QgsProcessingException(
                "Densification interval must be a positive number of metres.")

        leftBankLayer = self.parameterAsVectorLayer(
            parameters, self.LEFTBANK, context)
        rightBankLayer = self.parameterAsVectorLayer(
            parameters, self.RIGHTBANK, context)
        if leftBankLayer is None or rightBankLayer is None:
            raise QgsProcessingException(
                "Both left and right bank layers are required.")

        if not leftBankLayer.crs().isValid() or not rightBankLayer.crs().isValid():
            raise QgsProcessingException(
                "Both input bank layers must have a valid, defined CRS.")

        explicit_crs = parameters.get(self.OUTPUT_CRS)
        if explicit_crs is not None and str(explicit_crs).strip():
            output_crs = self.parameterAsCrs(parameters, self.OUTPUT_CRS, context)
        else:
            output_crs = leftBankLayer.crs()

        if not output_crs.isValid():
            raise QgsProcessingException(
                "Unable to establish a valid working CRS. Specify a projected CRS in metres.")
        if output_crs.isGeographic() or output_crs.mapUnits() != QgsUnitTypes.DistanceMeters:
            raise QgsProcessingException(
                "RivaPlan requires a projected working CRS with metre units. "
                "Select an appropriate local projected CRS (for example, the "
                "appropriate UTM zone) instead of latitude/longitude or feet.")

        crs_authid = output_crs.authid()
        if not crs_authid:
            raise QgsProcessingException(
                "Please select an identifiable projected working CRS with metre units.")

        feedback.pushInfo('RivaPlan | Bank lines to centerline')
        feedback.pushInfo('Working CRS: {} | Densification: {:.3f} m'.format(
            output_crs.authid(), densify_interval))
        fb = QgsProcessingMultiStepFeedback(25, feedback)
        child_fb = _QuietChildFeedback(fb)

        # Reproject BEFORE endpoint extraction, distance comparisons and caps.
        # The current implementation uses a single input feature per bank.
        for side, bank in [('left', leftBankLayer), ('right', rightBankLayer)]:
            if bank.featureCount() != 1:
                raise QgsProcessingException(
                    "The {} bank must contain exactly one continuous line feature "
                    "(found {}). Merge or select the intended reach first.".format(
                        side, bank.featureCount()))
            feat = next(bank.getFeatures(), None)
            if feat is None or not feat.hasGeometry() or feat.geometry().isEmpty():
                raise QgsProcessingException("The {} bank has no usable geometry.".format(side))
            geom = feat.geometry()
            if geom.isMultipart():
                parts = geom.asMultiPolyline()
                if len(parts) != 1:
                    raise QgsProcessingException(
                        "The {} bank must be a single continuous LineString, not "
                        "multiple disconnected parts.".format(side))
            if geom.length() <= 0 or not geom.isGeosValid():
                raise QgsProcessingException(
                    "The {} bank is zero-length or geometrically invalid. "
                    "Repair its geometry before running RivaPlan.".format(side))

        if leftBankLayer.crs() != output_crs:
            left_reproj = processing.run(
                'native:reprojectlayer',
                {'INPUT': leftBankLayer, 'TARGET_CRS': output_crs,
                 'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT},
                context=context, feedback=child_fb, is_child_algorithm=True)
            leftBankLayer = QgsProcessingUtils.mapLayerFromString(left_reproj['OUTPUT'], context)
        if rightBankLayer.crs() != output_crs:
            right_reproj = processing.run(
                'native:reprojectlayer',
                {'INPUT': rightBankLayer, 'TARGET_CRS': output_crs,
                 'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT},
                context=context, feedback=child_fb, is_child_algorithm=True)
            rightBankLayer = QgsProcessingUtils.mapLayerFromString(right_reproj['OUTPUT'], context)
        if leftBankLayer is None or rightBankLayer is None:
            raise QgsProcessingException('Could not prepare bank layers in the working CRS.')

        left_geom = next(leftBankLayer.getFeatures()).geometry()
        right_geom = next(rightBankLayer.getFeatures()).geometry()
        if left_geom.isMultipart():
            left_geom = QgsGeometry.fromPolylineXY(left_geom.asMultiPolyline()[0])
        if right_geom.isMultipart():
            right_geom = QgsGeometry.fromPolylineXY(right_geom.asMultiPolyline()[0])
        if left_geom.intersects(right_geom):
            raise QgsProcessingException(
                'Left and right banklines intersect or overlap. '
                'Prepare two distinct bank boundaries before centerline generation.')

        # Extract first and last vertices of both banks
        fb.setCurrentStep(0)
        alg_params = {
            'INPUT': leftBankLayer,
            'VERTICES': '0,-1',
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['LeftEnds'] = processing.run(
            'native:extractspecificvertices', alg_params,
            context=context, feedback=child_fb, is_child_algorithm=True)

        fb.setCurrentStep(1)
        alg_params = {
            'INPUT': rightBankLayer,
            'VERTICES': '0,-1',
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['RightEnds'] = processing.run(
            'native:extractspecificvertices', alg_params,
            context=context, feedback=child_fb, is_child_algorithm=True)

        left_end_layer = QgsProcessingUtils.mapLayerFromString(
            outputs['LeftEnds']['OUTPUT'], context)
        right_end_layer = QgsProcessingUtils.mapLayerFromString(
            outputs['RightEnds']['OUTPUT'], context)

        if left_end_layer is None or right_end_layer is None:
            raise QgsProcessingException(
                "Could not load the extracted bank endpoint layers.")

        left_end_features = [
            feature for feature in left_end_layer.getFeatures()
            if feature.hasGeometry() and not feature.geometry().isEmpty()
        ]
        right_end_features = [
            feature for feature in right_end_layer.getFeatures()
            if feature.hasGeometry() and not feature.geometry().isEmpty()
        ]

        if len(left_end_features) != 2 or len(right_end_features) != 2:
            raise QgsProcessingException(
                "Each bank layer must provide exactly two endpoints. "
                f"Left endpoints: {len(left_end_features)}; "
                f"right endpoints: {len(right_end_features)}.")

        left_points = [
            QgsPointXY(feature.geometry().asPoint())
            for feature in left_end_features
        ]
        right_points = [
            QgsPointXY(feature.geometry().asPoint())
            for feature in right_end_features
        ]

        # Both banks are already known edges (AB and CD), so only two
        # complete cross-bank matchings are possible: AC+BD or AD+BC.
        # Check BOTH before selecting the shortest geometrically valid one.
        candidates = [
            ((0, 0), (1, 1)),
            ((0, 1), (1, 0)),
        ]
        left_vertices = left_geom.asPolyline()
        right_vertices = right_geom.asPolyline()
        valid_pairings = []
        pairing_rejections = []

        def only_expected_endpoint_contact(cap, bank_geom, endpoint):
            if not cap.intersects(bank_geom):
                return False
            contact = cap.intersection(bank_geom)
            if contact.isEmpty() or contact.type() != QgsWkbTypes.PointGeometry:
                return False  # overlap or shared segment is not an end cap
            if contact.isMultipart():
                contact_points = contact.asMultiPoint()
            else:
                contact_points = [contact.asPoint()]
            return (len(contact_points) == 1 and
                    QgsPointXY(contact_points[0]).distance(endpoint) <= 0.001)

        for matching in candidates:
            cap_geoms = [QgsGeometry.fromPolylineXY(
                [left_points[li], right_points[ri]]) for li, ri in matching]
            label = '{} + {}'.format(
                'AC' if matching[0] == (0, 0) else 'AD',
                'BD' if matching[1] == (1, 1) else 'BC')
            if any(cap.length() <= 0 for cap in cap_geoms):
                pairing_rejections.append('{}: zero-length cap'.format(label))
                continue
            if cap_geoms[0].intersects(cap_geoms[1]):
                pairing_rejections.append('{}: intersecting caps'.format(label))
                continue
            contacts_ok = all(
                only_expected_endpoint_contact(cap_geoms[i], left_geom,
                                               left_points[li]) and
                only_expected_endpoint_contact(cap_geoms[i], right_geom,
                                               right_points[ri])
                for i, (li, ri) in enumerate(matching))
            if not contacts_ok:
                pairing_rejections.append(
                    '{}: cap intersects bankline away from its endpoint'.format(label))
                continue

            # Construct the single expected ring explicitly. This prevents
            # a wrong pairing from passing just because polygonize retains
            # some smaller internal polygon.
            right_at_left_end = matching[1][1]
            traversed_right = (list(reversed(right_vertices))
                               if right_at_left_end == 1 else list(right_vertices))
            ring = [QgsPointXY(p) for p in left_vertices]
            ring.extend(QgsPointXY(p) for p in traversed_right)
            ring.append(QgsPointXY(left_vertices[0]))
            polygon = QgsGeometry.fromPolygonXY([ring])
            if polygon.isEmpty() or not polygon.isGeosValid() or polygon.area() <= 0:
                pairing_rejections.append('{}: invalid channel polygon'.format(label))
                continue
            total_length = sum(cap.length() for cap in cap_geoms)
            valid_pairings.append((total_length, matching, label))

        if not valid_pairings:
            raise QgsProcessingException(
                'Neither cross-bank end-cap pairing gives a valid channel '
                'polygon. Check endpoints, bankline geometry and reach limits. '
                'Details: {}'.format('; '.join(pairing_rejections)))
        valid_pairings.sort(key=lambda item: item[0])
        _, selected_matching, selected_label = valid_pairings[0]
        if len(valid_pairings) > 1:
            feedback.pushWarning(
                'Both end-cap pairings are geometrically valid. '
                'Selected shorter {}. Inspect the closures for this folded reach.'.format(
                    selected_label))
        cap_pairs = [(left_points[li], right_points[ri])
                     for li, ri in selected_matching]
        feedback.pushInfo('End-cap pairing done')

        # Create and save the two river end caps
        fb.setCurrentStep(2)
        end_cap_layer = QgsVectorLayer(
            f'LineString?crs={crs_authid}', 'EndCaps', 'memory')

        if not end_cap_layer.isValid():
            raise QgsProcessingException(
                "Could not create the river end-cap layer.")

        end_cap_provider = end_cap_layer.dataProvider()
        end_cap_provider.addAttributes([
            QgsField('cap_id', QVariant.Int),
            QgsField('cap_len', QVariant.Double)
        ])
        end_cap_layer.updateFields()

        end_cap_features = []
        for cap_id, (left_point, right_point) in enumerate(cap_pairs, start=1):
            feature = QgsFeature(end_cap_layer.fields())
            geometry = QgsGeometry.fromPolylineXY([left_point, right_point])
            feature.setGeometry(geometry)
            feature['cap_id'] = cap_id
            feature['cap_len'] = float(geometry.length())
            end_cap_features.append(feature)

        end_cap_provider.addFeatures(end_cap_features)
        end_cap_layer.updateExtents()

        outputs['EndCaps'] = processing.run(
            'native:savefeatures',
            {
                'INPUT': end_cap_layer,
                'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
            },
            context=context, feedback=child_fb, is_child_algorithm=True)

        outputs['ClosureLines'] = outputs['EndCaps']

        # --------------------------------------------------------------
        # Assemble the banks and end caps into one boundary layer.
        #
        # This is intentionally created in memory and then saved because
        # native:mergevectorlayers may preserve duplicate source feature
        # IDs. When written to a GeoPackage, duplicate IDs can violate the
        # unique constraint on the provider-managed fid column.
        # --------------------------------------------------------------
        fb.setCurrentStep(3)

        channel_boundary_layer = QgsVectorLayer(
            f'LineString?crs={crs_authid}',
            'ChannelBoundary',
            'memory'
        )

        if not channel_boundary_layer.isValid():
            raise QgsProcessingException(
                "Could not create the channel-boundary layer.")

        channel_boundary_provider = (
            channel_boundary_layer.dataProvider()
        )

        channel_boundary_provider.addAttributes([
            QgsField('boundary_id', QVariant.Int),
            QgsField('source', QVariant.String)
        ])
        channel_boundary_layer.updateFields()

        boundary_features = []
        boundary_id = 1

        def append_line_layer(source_layer, source_name):
            nonlocal boundary_id

            if source_layer is None:
                raise QgsProcessingException(
                    f"Could not load boundary source: {source_name}")

            coordinate_transform = None

            if source_layer.crs() != output_crs:
                coordinate_transform = QgsCoordinateTransform(
                    source_layer.crs(),
                    output_crs,
                    QgsProject.instance()
                )

            for source_feature in source_layer.getFeatures():

                if (not source_feature.hasGeometry()
                        or source_feature.geometry().isEmpty()):
                    continue

                source_geometry = QgsGeometry(
                    source_feature.geometry()
                )

                if coordinate_transform is not None:
                    source_geometry.transform(
                        coordinate_transform
                    )

                if QgsWkbTypes.isCurvedType(
                        source_geometry.wkbType()):
                    source_geometry = source_geometry.segmentize()

                if source_geometry.isMultipart():
                    line_parts = source_geometry.asMultiPolyline()
                else:
                    single_line = source_geometry.asPolyline()
                    line_parts = (
                        [single_line] if single_line else []
                    )

                for line_part in line_parts:

                    if len(line_part) < 2:
                        continue

                    boundary_feature = QgsFeature(
                        channel_boundary_layer.fields()
                    )

                    boundary_feature.setGeometry(
                        QgsGeometry.fromPolylineXY([
                            QgsPointXY(point)
                            for point in line_part
                        ])
                    )

                    boundary_feature['boundary_id'] = boundary_id
                    boundary_feature['source'] = source_name

                    boundary_features.append(boundary_feature)
                    boundary_id += 1

        append_line_layer(leftBankLayer, 'left_bank')
        append_line_layer(rightBankLayer, 'right_bank')

        saved_end_caps_layer = QgsProcessingUtils.mapLayerFromString(
            outputs['EndCaps']['OUTPUT'],
            context
        )
        append_line_layer(saved_end_caps_layer, 'end_cap')

        if not boundary_features:
            raise QgsProcessingException(
                "No valid line features were available for the "
                "channel boundary.")

        channel_boundary_provider.addFeatures(boundary_features)
        channel_boundary_layer.updateExtents()

        outputs['ChannelBoundary'] = processing.run(
            'native:savefeatures',
            {
                'INPUT': channel_boundary_layer,
                'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
            },
            context=context,
            feedback=child_fb,
            is_child_algorithm=True
        )

        # Polygonize the closed bank-and-cap boundary
        fb.setCurrentStep(4)
        alg_params = {
            'INPUT': outputs['ChannelBoundary']['OUTPUT'],
            'KEEP_FIELDS': False,
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['RiverChannelPolygonRaw'] = processing.run(
            'native:polygonize', alg_params,
            context=context, feedback=child_fb, is_child_algorithm=True)

        raw_polygon_layer = QgsProcessingUtils.mapLayerFromString(
            outputs['RiverChannelPolygonRaw']['OUTPUT'], context)
        if raw_polygon_layer is None or raw_polygon_layer.featureCount() == 0:
            raise QgsProcessingException(
                "The banks and end caps did not form a valid closed "
                "river-channel polygon.")

        # Retain the largest polygon when polygonize creates more than one
        fb.setCurrentStep(5)
        alg_params = {
            'INPUT': outputs['RiverChannelPolygonRaw']['OUTPUT'],
            'EXPRESSION': '$area = maximum($area, group_by:=@layer)',
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['RiverChannelPolygon'] = processing.run(
            'native:extractbyexpression', alg_params,
            context=context, feedback=child_fb, is_child_algorithm=True)

        # Bank merge and adjustable densification
        preprocess_time = time.perf_counter() - started_at
        voronoi_started = time.perf_counter()
        fb.setCurrentStep(6)
        alg_params = {
            'LAYERS': [leftBankLayer, rightBankLayer],
            'CRS': output_crs,
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['MergeVectorLayers'] = processing.run(
            'native:mergevectorlayers', alg_params,
            context=context, feedback=child_fb, is_child_algorithm=True)

        fb.setCurrentStep(7)
        alg_params = {
            'INPUT': outputs['MergeVectorLayers']['OUTPUT'],
            'INTERVAL': densify_interval,
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['DensifyByInterval'] = processing.run(
            'native:densifygeometriesgivenaninterval', alg_params,
            context=context, feedback=child_fb, is_child_algorithm=True)

        fb.setCurrentStep(8)
        alg_params = {
            'INPUT': outputs['DensifyByInterval']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['ExtractVertices_01'] = processing.run(
            'native:extractvertices', alg_params,
            context=context, feedback=child_fb, is_child_algorithm=True)

        fb.setCurrentStep(9)
        alg_params = {
            'BUFFER': 0,
            'INPUT': outputs['ExtractVertices_01']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['VoronoiPolygons'] = processing.run(
            'qgis:voronoipolygons', alg_params,
            context=context, feedback=child_fb, is_child_algorithm=True)

        fb.setCurrentStep(10)
        alg_params = {
            'INPUT': outputs['VoronoiPolygons']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['PolygonsToLines'] = processing.run(
            'native:polygonstolines', alg_params,
            context=context, feedback=child_fb, is_child_algorithm=True)

        # Index the large Voronoi line input and polygon overlay BEFORE
        # native:clip; the original algorithm indexed only after clipping.
        try:
            processing.run('native:createspatialindex',
                           {'INPUT': outputs['PolygonsToLines']['OUTPUT']},
                           context=context, feedback=child_fb,
                           is_child_algorithm=True)
            processing.run('native:createspatialindex',
                           {'INPUT': outputs['RiverChannelPolygon']['OUTPUT']},
                           context=context, feedback=child_fb,
                           is_child_algorithm=True)
        except QgsProcessingException as exc:
            fb.pushWarning('Spatial index creation unavailable: {}'.format(exc))

        # Clip before exploding and cleaning the Voronoi linework
        fb.setCurrentStep(11)
        alg_params = {
            'INPUT': outputs['PolygonsToLines']['OUTPUT'],
            'OVERLAY': outputs['RiverChannelPolygon']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['ClippedVoronoiLines'] = processing.run(
            'native:clip', alg_params,
            context=context, feedback=child_fb, is_child_algorithm=True)

        fb.setCurrentStep(12)
        alg_params = {
            'INPUT': outputs['ClippedVoronoiLines']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['ExplodeLines'] = processing.run(
            'native:explodelines', alg_params,
            context=context, feedback=child_fb, is_child_algorithm=True)

        fb.setCurrentStep(13)
        alg_params = {
            'INPUT': outputs['ExplodeLines']['OUTPUT'],
            'TOLERANCE': 1e-06,
            'USE_Z_VALUE': False,
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['RemoveDuplicateVertices'] = processing.run(
            'native:removeduplicatevertices', alg_params,
            context=context, feedback=child_fb, is_child_algorithm=True)

        fb.setCurrentStep(14)
        alg_params = {
            'INPUT': outputs['RemoveDuplicateVertices']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['DeleteDuplicateGeometriesVoronoiPolylines'] = processing.run(
            'native:deleteduplicategeometries', alg_params,
            context=context, feedback=child_fb, is_child_algorithm=True)

        fb.setCurrentStep(15)
        alg_params = {
            'INPUT': outputs[
                'DeleteDuplicateGeometriesVoronoiPolylines']['OUTPUT']
        }
        outputs['CreateSpatialIndex_01'] = processing.run(
            'native:createspatialindex', alg_params,
            context=context, feedback=child_fb, is_child_algorithm=True)

        # --------------------------------------------------------------
        # Create one dissolved exclusion buffer around both bank lines.
        #
        # The buffer includes exact bank intersections and all geometry
        # within the fixed internal bank-exclusion distance. DISSOLVE=True is essential:
        # it produces a single multipart reference feature, so the
        # "disjoint" predicate means disjoint from both banks together.
        # --------------------------------------------------------------
        fb.setCurrentStep(16)
        alg_params = {
            'INPUT': outputs['MergeVectorLayers']['OUTPUT'],
            'DISTANCE': bank_exclusion_distance,
            'SEGMENTS': 5,
            'END_CAP_STYLE': 0,
            'JOIN_STYLE': 0,
            'MITER_LIMIT': 2,
            'DISSOLVE': True,
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['BankExclusionBuffer'] = processing.run(
            'native:buffer',
            alg_params,
            context=context,
            feedback=child_fb,
            is_child_algorithm=True
        )

        # Diagnostic layer: exploded Voronoi segments that touch or enter
        # the bank-exclusion buffer.
        fb.setCurrentStep(17)
        alg_params = {
            'INPUT': outputs['CreateSpatialIndex_01']['OUTPUT'],
            'INTERSECT': outputs['BankExclusionBuffer']['OUTPUT'],
            'PREDICATE': [0],  # intersects
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['VoronoiLinesWithinBankDistance'] = processing.run(
            'native:extractbylocation',
            alg_params,
            context=context,
            feedback=child_fb,
            is_child_algorithm=True
        )

        # Retained network: segments disjoint from the single dissolved
        # bank-buffer feature. This is feature filtering, not geometric
        # subtraction, and therefore avoids the expensive Difference step.
        fb.setCurrentStep(18)
        alg_params = {
            'INPUT': outputs['CreateSpatialIndex_01']['OUTPUT'],
            'INTERSECT': outputs['BankExclusionBuffer']['OUTPUT'],
            'PREDICATE': [2],  # disjoint
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['VoronoiLinesAwayFromBanks'] = processing.run(
            'native:extractbylocation',
            alg_params,
            context=context,
            feedback=child_fb,
            is_child_algorithm=True
        )

        # ------------------------------------------------------------------
        # DIRECT DIJKSTRA TRACING ON THE STEP 18 VORONOI NETWORK
        # ------------------------------------------------------------------
        voronoi_time = time.perf_counter() - voronoi_started
        dijkstra_started = time.perf_counter()
        closure_layer = QgsProcessingUtils.mapLayerFromString(
            outputs['EndCaps']['OUTPUT'],
            context
        )

        network_layer = QgsProcessingUtils.mapLayerFromString(
            outputs['VoronoiLinesAwayFromBanks']['OUTPUT'],
            context
        )

        if closure_layer is None:
            raise QgsProcessingException(
                "Could not load the two end-cap lines.")

        if network_layer is None:
            raise QgsProcessingException(
                "Could not load the Step 18 Voronoi network.")

        closure_features = [
            feature for feature in closure_layer.getFeatures()
            if feature.hasGeometry() and not feature.geometry().isEmpty()
        ]

        if len(closure_features) != 2:
            raise QgsProcessingException(
                "Exactly two end-cap lines are required, but "
                f"{len(closure_features)} were created.")

        # --------------------------------------------------------------
        # Build an undirected graph directly from Step 18.
        #
        # Every consecutive pair of vertices becomes a graph edge.
        # Coordinates within 0.001 map units are treated as one node.
        # --------------------------------------------------------------
        graph_snap_tolerance = 0.001

        def node_key(point):
            return (
                int(round(point.x() / graph_snap_tolerance)),
                int(round(point.y() / graph_snap_tolerance))
            )

        node_coordinates = {}
        adjacency = {}
        graph_edges = []

        for network_feature in network_layer.getFeatures():

            if feedback.isCanceled():
                return {}

            if (not network_feature.hasGeometry()
                    or network_feature.geometry().isEmpty()):
                continue

            geometry = network_feature.geometry()

            if geometry.isMultipart():
                line_parts = geometry.asMultiPolyline()
            else:
                single_part = geometry.asPolyline()
                line_parts = [single_part] if single_part else []

            for line_part in line_parts:

                if len(line_part) < 2:
                    continue

                for vertex_index in range(len(line_part) - 1):
                    point_1 = QgsPointXY(line_part[vertex_index])
                    point_2 = QgsPointXY(line_part[vertex_index + 1])

                    key_1 = node_key(point_1)
                    key_2 = node_key(point_2)

                    if key_1 == key_2:
                        continue

                    if key_1 not in node_coordinates:
                        node_coordinates[key_1] = point_1

                    if key_2 not in node_coordinates:
                        node_coordinates[key_2] = point_2

                    edge_length = point_1.distance(point_2)

                    if edge_length <= 0:
                        continue

                    edge_index = len(graph_edges)

                    graph_edges.append({
                        'node_1': key_1,
                        'node_2': key_2,
                        'length': edge_length,
                        'source_fid': network_feature.id()
                    })

                    adjacency.setdefault(key_1, []).append(
                        (key_2, edge_length, edge_index)
                    )

                    adjacency.setdefault(key_2, []).append(
                        (key_1, edge_length, edge_index)
                    )

        if len(node_coordinates) < 2 or not graph_edges:
            raise QgsProcessingException(
                "The Step 18 layer did not produce a usable "
                "Voronoi graph.")

        feedback.pushInfo(
            f"Step 18 Voronoi graph: {len(node_coordinates)} nodes and "
            f"{len(graph_edges)} edges."
        )

        # --------------------------------------------------------------
        # Calculate the midpoint of each end cap.
        # --------------------------------------------------------------
        cap_midpoints = []

        for closure_feature in closure_features:
            closure_geometry = closure_feature.geometry()
            closure_length = closure_geometry.length()

            if closure_length <= 0:
                raise QgsProcessingException(
                    "An end-cap line has zero length.")

            midpoint_geometry = closure_geometry.interpolate(
                closure_length / 2.0
            )

            if midpoint_geometry.isEmpty():
                raise QgsProcessingException(
                    "Could not calculate an end-cap midpoint.")

            cap_midpoints.append({
                'closure_fid': closure_feature.id(),
                'point': QgsPointXY(midpoint_geometry.asPoint())
            })

        # --------------------------------------------------------------
        # Rank graph nodes by distance from each end-cap midpoint.
        # Two distinct graph nodes are required.
        # --------------------------------------------------------------
        def graph_node_ranking(target_point):
            ranking = []
            for key, graph_point in node_coordinates.items():
                dx = graph_point.x() - target_point.x()
                dy = graph_point.y() - target_point.y()
                distance = (dx * dx + dy * dy) ** 0.5
                ranking.append((distance, key))

            ranking.sort(key=lambda item: item[0])
            return ranking

        first_ranking = graph_node_ranking(
            cap_midpoints[0]['point'])
        second_ranking = graph_node_ranking(
            cap_midpoints[1]['point'])

        if not first_ranking or not second_ranking:
            raise QgsProcessingException(
                "No graph nodes were available for terminal selection.")

        first_choice = first_ranking[0]
        second_choice = second_ranking[0]

        # If both cap midpoints choose the same node, compare the two
        # valid alternatives and use the lower combined snap distance.
        if first_choice[1] == second_choice[1]:
            alternatives = []

            if len(first_ranking) > 1:
                alternatives.append((
                    first_ranking[1][0] + second_choice[0],
                    first_ranking[1],
                    second_choice
                ))

            if len(second_ranking) > 1:
                alternatives.append((
                    first_choice[0] + second_ranking[1][0],
                    first_choice,
                    second_ranking[1]
                ))

            if not alternatives:
                raise QgsProcessingException(
                    "Two distinct terminal graph nodes could not "
                    "be identified.")

            alternatives.sort(key=lambda item: item[0])
            first_choice = alternatives[0][1]
            second_choice = alternatives[0][2]

        start_node = first_choice[1]
        end_node = second_choice[1]

        start_snap_distance = first_choice[0]
        end_snap_distance = second_choice[0]

        if start_node == end_node:
            raise QgsProcessingException(
                "The two end caps were linked to the same graph node.")

        selected_anchors = [
            {
                'role': 'START',
                'closure_fid': cap_midpoints[0]['closure_fid'],
                'cap_point': cap_midpoints[0]['point'],
                'node_key': start_node,
                'node_point': node_coordinates[start_node],
                'distance': start_snap_distance
            },
            {
                'role': 'END',
                'closure_fid': cap_midpoints[1]['closure_fid'],
                'cap_point': cap_midpoints[1]['point'],
                'node_key': end_node,
                'node_point': node_coordinates[end_node],
                'distance': end_snap_distance
            }
        ]

        # --------------------------------------------------------------
        # Save the two end-cap midpoints.
        # --------------------------------------------------------------
        fb.setCurrentStep(19)

        midpoint_layer = QgsVectorLayer(
            f'Point?crs={crs_authid}',
            'EndCapMidpoints',
            'memory'
        )

        if not midpoint_layer.isValid():
            raise QgsProcessingException(
                "Could not create the end-cap midpoint layer.")

        midpoint_provider = midpoint_layer.dataProvider()
        midpoint_provider.addAttributes([
            QgsField('role', QVariant.String),
            QgsField('closure_id', QVariant.String)
        ])
        midpoint_layer.updateFields()

        midpoint_features = []

        for anchor in selected_anchors:
            feature = QgsFeature(midpoint_layer.fields())
            feature.setGeometry(
                QgsGeometry.fromPointXY(anchor['cap_point'])
            )
            feature['role'] = anchor['role']
            feature['closure_id'] = str(anchor['closure_fid'])
            midpoint_features.append(feature)

        midpoint_provider.addFeatures(midpoint_features)
        midpoint_layer.updateExtents()

        outputs['EndCapMidpoints'] = processing.run(
            'native:savefeatures',
            {
                'INPUT': midpoint_layer,
                'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
            },
            context=context,
            feedback=child_fb,
            is_child_algorithm=True
        )

        # --------------------------------------------------------------
        # Save the two graph nodes selected as START and END.
        # --------------------------------------------------------------
        fb.setCurrentStep(20)

        anchor_layer = QgsVectorLayer(
            f'Point?crs={crs_authid}',
            'StartEndGraphNodes',
            'memory'
        )

        if not anchor_layer.isValid():
            raise QgsProcessingException(
                "Could not create the start/end graph-node layer.")

        anchor_provider = anchor_layer.dataProvider()
        anchor_provider.addAttributes([
            QgsField('role', QVariant.String),
            QgsField('closure_id', QVariant.String),
            QgsField('snap_dist', QVariant.Double),
            QgsField('node_x', QVariant.Double),
            QgsField('node_y', QVariant.Double)
        ])
        anchor_layer.updateFields()

        anchor_features = []

        for anchor in selected_anchors:
            feature = QgsFeature(anchor_layer.fields())
            feature.setGeometry(
                QgsGeometry.fromPointXY(anchor['node_point'])
            )
            feature['role'] = anchor['role']
            feature['closure_id'] = str(anchor['closure_fid'])
            feature['snap_dist'] = float(anchor['distance'])
            feature['node_x'] = float(anchor['node_point'].x())
            feature['node_y'] = float(anchor['node_point'].y())
            anchor_features.append(feature)

        anchor_provider.addFeatures(anchor_features)
        anchor_layer.updateExtents()

        outputs['StartEndGraphNodes'] = processing.run(
            'native:savefeatures',
            {
                'INPUT': anchor_layer,
                'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
            },
            context=context,
            feedback=child_fb,
            is_child_algorithm=True
        )

        feedback.pushInfo(
            f"Start end-cap midpoint to graph-node distance: "
            f"{start_snap_distance:.6f}"
        )

        feedback.pushInfo(
            f"End end-cap midpoint to graph-node distance: "
            f"{end_snap_distance:.6f}"
        )

        # --------------------------------------------------------------
        # Dijkstra shortest path.
        #
        # Edge length is the traversal cost. The graph is built
        # directly from the Step 18 bank-buffer-filtered Voronoi network.
        # --------------------------------------------------------------
        distances = {start_node: 0.0}
        previous = {}
        priority_queue = [(0.0, start_node)]
        visited = set()

        while priority_queue:

            if feedback.isCanceled():
                return {}

            current_distance, current_node = heapq.heappop(
                priority_queue)

            if current_node in visited:
                continue

            visited.add(current_node)

            if current_node == end_node:
                break

            for neighbour, edge_cost, edge_index in adjacency.get(
                    current_node, []):

                new_distance = current_distance + edge_cost

                if new_distance < distances.get(
                        neighbour, float('inf')):

                    distances[neighbour] = new_distance
                    previous[neighbour] = (
                        current_node,
                        edge_index
                    )

                    heapq.heappush(
                        priority_queue,
                        (new_distance, neighbour)
                    )

        if end_node not in distances:
            raise QgsProcessingException(
                "No connected route was found between the selected "
                "start and end nodes in the Step 18 Voronoi network.")

        # ---- Reconstruct the ordered graph route ----
        path_nodes_reversed = [end_node]
        path_edges_reversed = []

        current_node = end_node

        while current_node != start_node:
            if current_node not in previous:
                raise QgsProcessingException(
                    "The Dijkstra route could not be reconstructed.")

            previous_node, edge_index = previous[current_node]
            path_edges_reversed.append(edge_index)
            path_nodes_reversed.append(previous_node)
            current_node = previous_node

        path_nodes = list(reversed(path_nodes_reversed))
        path_edge_indices = list(reversed(path_edges_reversed))

        if len(path_nodes) < 2:
            raise QgsProcessingException(
                "The reconstructed route contains fewer than two nodes.")

        # ---- Save the ordered Dijkstra path edges ----
        fb.setCurrentStep(21)

        path_edges_layer = QgsVectorLayer(
            f'LineString?crs={crs_authid}',
            'DijkstraPathEdges',
            'memory'
        )

        if not path_edges_layer.isValid():
            raise QgsProcessingException(
                "Could not create the Dijkstra path-edge layer.")

        path_edges_provider = path_edges_layer.dataProvider()
        path_edges_provider.addAttributes([
            QgsField('sequence', QVariant.Int),
            QgsField('source_id', QVariant.String),
            QgsField('seg_length', QVariant.Double)
        ])
        path_edges_layer.updateFields()

        output_edge_features = []

        for sequence, edge_index in enumerate(
                path_edge_indices, start=1):

            graph_edge = graph_edges[edge_index]
            route_node_1 = path_nodes[sequence - 1]
            route_node_2 = path_nodes[sequence]

            point_1 = node_coordinates[route_node_1]
            point_2 = node_coordinates[route_node_2]

            feature = QgsFeature(path_edges_layer.fields())
            feature.setGeometry(
                QgsGeometry.fromPolylineXY([point_1, point_2])
            )
            feature['sequence'] = sequence
            feature['source_id'] = str(
                graph_edge['source_fid'])
            feature['seg_length'] = float(
                graph_edge['length'])
            output_edge_features.append(feature)

        path_edges_provider.addFeatures(output_edge_features)
        path_edges_layer.updateExtents()

        outputs['DijkstraPathEdges'] = processing.run(
            'native:savefeatures',
            {
                'INPUT': path_edges_layer,
                'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
            },
            context=context,
            feedback=child_fb,
            is_child_algorithm=True
        )

        # --------------------------------------------------------------
        # Construct one ordered LineString from the path-node sequence.
        # The result has one start point, one end point and no branches.
        # --------------------------------------------------------------
        fb.setCurrentStep(22)

        ordered_path_points = [
            node_coordinates[key] for key in path_nodes
        ]

        cleaned_path_points = []
        for point in ordered_path_points:
            if not cleaned_path_points:
                cleaned_path_points.append(point)
                continue

            previous_point = cleaned_path_points[-1]

            if (point.x() != previous_point.x()
                    or point.y() != previous_point.y()):
                cleaned_path_points.append(point)

        if len(cleaned_path_points) < 2:
            raise QgsProcessingException(
                "The final Dijkstra centreline contains fewer than "
                "two distinct vertices.")

        final_path_geometry = QgsGeometry.fromPolylineXY(
            cleaned_path_points)

        if final_path_geometry.isEmpty():
            raise QgsProcessingException(
                "The final Dijkstra centreline geometry is empty.")

        # Final geometry checks (diagnostic only; do not modify the result).
        feedback.pushInfo('--- Final centerline geometry checks ---')

        # Check whether any part of the centerline lies outside the selected
        # river-channel polygon. A line on the polygon boundary is allowed.
        try:
            channel_layer = QgsProcessingUtils.mapLayerFromString(
                outputs['RiverChannelPolygon']['OUTPUT'], context)
            if channel_layer is None:
                feedback.pushWarning(
                    'Centerline within channel polygon: NOT CHECKED '
                    '(channel polygon layer could not be loaded).')
            else:
                channel_geometries = [
                    QgsGeometry(feature.geometry())
                    for feature in channel_layer.getFeatures()
                    if feature.hasGeometry() and not feature.geometry().isEmpty()
                ]
                if not channel_geometries:
                    feedback.pushWarning(
                        'Centerline within channel polygon: NOT CHECKED '
                        '(no usable channel polygon geometry).')
                else:
                    channel_geometry = QgsGeometry.unaryUnion(channel_geometries)
                    outside_geometry = final_path_geometry.difference(
                        channel_geometry)
                    if outside_geometry.isNull():
                        feedback.pushWarning(
                            'Centerline within channel polygon: NOT CHECKED '
                            '(geometry difference could not be calculated).')
                    elif outside_geometry.isEmpty() or outside_geometry.length() <= 1e-6:
                        feedback.pushInfo(
                            'Centerline within channel polygon: PASS '
                            '(no portion outside the polygon).')
                    else:
                        feedback.pushWarning(
                            'Centerline within channel polygon: WARNING '
                            '({:.3f} m of centerline lies outside the polygon).'.format(
                                outside_geometry.length()))
        except Exception as exc:
            feedback.pushWarning(
                'Centerline within channel polygon: NOT CHECKED ({})'.format(exc))

        # Check actual line crossings, not simple touches, with either bank.
        try:
            crosses_left = final_path_geometry.crosses(left_geom)
            crosses_right = final_path_geometry.crosses(right_geom)
            if crosses_left or crosses_right:
                crossing_banks = []
                if crosses_left:
                    crossing_banks.append('left')
                if crosses_right:
                    crossing_banks.append('right')
                feedback.pushWarning(
                    'Centerline crosses bankline: WARNING '
                    '(crosses {} bankline).'.format(' and '.join(crossing_banks)))
            else:
                feedback.pushInfo(
                    'Centerline crosses bankline: PASS '
                    '(no crossings detected).')
        except Exception as exc:
            feedback.pushWarning(
                'Centerline crosses bankline: NOT CHECKED ({})'.format(exc))

        final_path_layer = QgsVectorLayer(
            f'LineString?crs={crs_authid}',
            'DijkstraCenterline',
            'memory'
        )

        if not final_path_layer.isValid():
            raise QgsProcessingException(
                "Could not create the final centreline layer.")

        final_path_provider = final_path_layer.dataProvider()
        final_path_provider.addAttributes([
            QgsField('path_cost', QVariant.Double),
            QgsField('node_count', QVariant.Int),
            QgsField('edge_count', QVariant.Int)
        ])
        final_path_layer.updateFields()

        final_feature = QgsFeature(final_path_layer.fields())
        final_feature.setGeometry(final_path_geometry)
        final_feature['path_cost'] = float(distances[end_node])
        final_feature['node_count'] = len(path_nodes)
        final_feature['edge_count'] = len(path_edge_indices)

        final_path_provider.addFeature(final_feature)
        final_path_layer.updateExtents()

        outputs['Centerline_FinalGeom'] = processing.run(
            'native:savefeatures',
            {
                'INPUT': final_path_layer,
                'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
            },
            context=context,
            feedback=child_fb,
            is_child_algorithm=True
        )

        # Add autoincremental field
        fb.setCurrentStep(23)
        alg_params = {
            'FIELD_NAME': 'AUTO',
            'GROUP_FIELDS': [],
            'INPUT': outputs['Centerline_FinalGeom']['OUTPUT'],
            'MODULUS': 0,
            'SORT_ASCENDING': True,
            'SORT_EXPRESSION': '',
            'SORT_NULLS_FIRST': False,
            'START': 1,
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['AddAutoincrementalField'] = processing.run(
            'native:addautoincrementalfield',
            alg_params, context=context,
            feedback=child_fb, is_child_algorithm=True)

        # Retain fields
        fb.setCurrentStep(24)
        alg_params = {
            'FIELDS': ['AUTO'],
            'INPUT': outputs['AddAutoincrementalField']['OUTPUT'],
            'OUTPUT': parameters['Centerline']  # parameters[self.OUTPUT]
        }
        outputs['RetainFields'] = processing.run(
            'native:retainfields', alg_params,
            context=context, feedback=child_fb,
            is_child_algorithm=True)

        results['Centerline'] = outputs['RetainFields']['OUTPUT']

        layer_id = results['Centerline']
        layer_details = context.layerToLoadOnCompletionDetails(layer_id)
        layer_details.name = "centerline"
        # Timings are printed once, avoiding dozens of native child logs.
        dijkstra_time = time.perf_counter() - dijkstra_started
        total_time = time.perf_counter() - started_at
        other_time = max(0.0, total_time - preprocess_time - voronoi_time - dijkstra_time)
        feedback.pushInfo('--- RivaPlan centerline processing ---')
        feedback.pushInfo('1. Input preprocessing and polygon construction: {:.3f} s'.format(preprocess_time))
        feedback.pushInfo('2. Voronoi generation and filtering: {:.3f} s'.format(voronoi_time))
        feedback.pushInfo('3. Dijkstra graph construction and route extraction: {:.3f} s'.format(dijkstra_time))
        feedback.pushInfo('Total centerline generation: {:.3f} s'.format(total_time))
        if other_time > 0.01:
            feedback.pushInfo('Other overhead: {:.3f} s'.format(other_time))
        feedback.pushInfo('Centerline generation completed successfully.')
        return results