# -*- coding: utf-8 -*-
"""
Bank lines to centerline (QGIS Processing Algorithm)

This tool creates a river centerline using left and right bank polylines.
Method:
1) Extract the four bank endpoints and create two end caps using the minimum-total-distance pairing.
2) Merge the banks and end caps to create a closed river-channel polygon.
3) Merge and densify the two bank lines at the original 5 m interval.
4) Create Voronoi polygons from the bank vertices and convert them to Voronoi lines.
5) Clip the Voronoi lines to the river-channel polygon before exploding and cleaning them.
6) Create a dissolved exclusion buffer around both banks.
7) Retain only exploded Voronoi segments disjoint from the bank buffer.
8) Use the retained Voronoi segments directly as an undirected graph.
9) Use the graph node nearest to each end-cap midpoint as the start and end node.
10) Trace one continuous start-to-end route using Dijkstra's algorithm.

Notes:
- The 5 m bank densification interval is retained.
- Intermediate layers are handled as temporary QGIS outputs.
- A fixed 0.50 map-unit bank-exclusion distance is used internally.
- This script assumes banks are reasonably clean and in a projected CRS (meters/feet).
"""

import heapq

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
                       QgsProcessingParameterCrs,
                       QgsProcessingParameterFeatureSource,
                       QgsProcessingParameterVectorLayer,
                       QgsProcessingParameterFeatureSink,
                       QgsProcessingUtils,
                       QgsProcessingMultiStepFeedback)
from qgis import processing


class mergingbanklines(QgsProcessingAlgorithm):
    LEFTBANK = 'LEFTBANK'
    RIGHTBANK = 'RIGHTBANK'
    OUTPUT_CRS = 'CRS'
    OUTPUT = 'OUTPUT'

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
        return self.tr("This tool makes the centerline for the given banks of the river")

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
                self.tr('CRS'),
                defaultValue='EPSG:32644'))

        self.addParameter(
            QgsProcessingParameterFeatureSink(
                'Centerline',  # self.OUTPUT
                self.tr('Centerline'),
                type=QgsProcessing.TypeVectorAnyGeometry,
                createByDefault=True, supportsAppend=True, defaultValue=None))

    # ------------------------- Main algorithm -------------------------
    def processAlgorithm(self, parameters, context, feedback):
        outputs = {}
        results = {}

        # Fixed internal distance used to remove Voronoi segments
        # close to either bank. The input CRS must use suitable map units.
        bank_exclusion_distance = 0.50

        leftBankLayer = self.parameterAsVectorLayer(parameters, self.LEFTBANK, context)
        rightBankLayer = self.parameterAsVectorLayer(parameters, self.RIGHTBANK, context)

        if leftBankLayer is None or rightBankLayer is None:
            raise QgsProcessingException("Invalid input: left and/or right bank layers are missing")

        output_crs = self.parameterAsCrs(
            parameters, self.OUTPUT_CRS, context)

        crs_authid = output_crs.authid()
        if not crs_authid:
            crs_authid = leftBankLayer.crs().authid()

        fb = QgsProcessingMultiStepFeedback(25, feedback)

        # Extract first and last vertices of both banks
        fb.setCurrentStep(0)
        alg_params = {
            'INPUT': leftBankLayer,
            'VERTICES': '0,-1',
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['LeftEnds'] = processing.run(
            'native:extractspecificvertices', alg_params,
            context=context, feedback=fb, is_child_algorithm=True)

        fb.setCurrentStep(1)
        alg_params = {
            'INPUT': rightBankLayer,
            'VERTICES': '0,-1',
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['RightEnds'] = processing.run(
            'native:extractspecificvertices', alg_params,
            context=context, feedback=fb, is_child_algorithm=True)

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

        # Compare the two valid one-to-one endpoint pairings
        pairing_a = (
            left_points[0].distance(right_points[0])
            + left_points[1].distance(right_points[1])
        )
        pairing_b = (
            left_points[0].distance(right_points[1])
            + left_points[1].distance(right_points[0])
        )

        if pairing_a <= pairing_b:
            cap_pairs = [
                (left_points[0], right_points[0]),
                (left_points[1], right_points[1])
            ]
        else:
            cap_pairs = [
                (left_points[0], right_points[1]),
                (left_points[1], right_points[0])
            ]

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
            context=context, feedback=fb, is_child_algorithm=True)

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
            feedback=fb,
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
            context=context, feedback=fb, is_child_algorithm=True)

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
            context=context, feedback=fb, is_child_algorithm=True)

        # Original bank merge and 5 m densification
        fb.setCurrentStep(6)
        alg_params = {
            'LAYERS': [leftBankLayer, rightBankLayer],
            'CRS': parameters[self.OUTPUT_CRS],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['MergeVectorLayers'] = processing.run(
            'native:mergevectorlayers', alg_params,
            context=context, feedback=fb, is_child_algorithm=True)

        fb.setCurrentStep(7)
        alg_params = {
            'INPUT': outputs['MergeVectorLayers']['OUTPUT'],
            'INTERVAL': 5,
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['DensifyByInterval'] = processing.run(
            'native:densifygeometriesgivenaninterval', alg_params,
            context=context, feedback=fb, is_child_algorithm=True)

        fb.setCurrentStep(8)
        alg_params = {
            'INPUT': outputs['DensifyByInterval']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['ExtractVertices_01'] = processing.run(
            'native:extractvertices', alg_params,
            context=context, feedback=fb, is_child_algorithm=True)

        fb.setCurrentStep(9)
        alg_params = {
            'BUFFER': 0,
            'INPUT': outputs['ExtractVertices_01']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['VoronoiPolygons'] = processing.run(
            'qgis:voronoipolygons', alg_params,
            context=context, feedback=fb, is_child_algorithm=True)

        fb.setCurrentStep(10)
        alg_params = {
            'INPUT': outputs['VoronoiPolygons']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['PolygonsToLines'] = processing.run(
            'native:polygonstolines', alg_params,
            context=context, feedback=fb, is_child_algorithm=True)

        # Clip before exploding and cleaning the Voronoi linework
        fb.setCurrentStep(11)
        alg_params = {
            'INPUT': outputs['PolygonsToLines']['OUTPUT'],
            'OVERLAY': outputs['RiverChannelPolygon']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['ClippedVoronoiLines'] = processing.run(
            'native:clip', alg_params,
            context=context, feedback=fb, is_child_algorithm=True)

        fb.setCurrentStep(12)
        alg_params = {
            'INPUT': outputs['ClippedVoronoiLines']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['ExplodeLines'] = processing.run(
            'native:explodelines', alg_params,
            context=context, feedback=fb, is_child_algorithm=True)

        fb.setCurrentStep(13)
        alg_params = {
            'INPUT': outputs['ExplodeLines']['OUTPUT'],
            'TOLERANCE': 1e-06,
            'USE_Z_VALUE': False,
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['RemoveDuplicateVertices'] = processing.run(
            'native:removeduplicatevertices', alg_params,
            context=context, feedback=fb, is_child_algorithm=True)

        fb.setCurrentStep(14)
        alg_params = {
            'INPUT': outputs['RemoveDuplicateVertices']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['DeleteDuplicateGeometriesVoronoiPolylines'] = processing.run(
            'native:deleteduplicategeometries', alg_params,
            context=context, feedback=fb, is_child_algorithm=True)

        fb.setCurrentStep(15)
        alg_params = {
            'INPUT': outputs[
                'DeleteDuplicateGeometriesVoronoiPolylines']['OUTPUT']
        }
        outputs['CreateSpatialIndex_01'] = processing.run(
            'native:createspatialindex', alg_params,
            context=context, feedback=fb, is_child_algorithm=True)

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
            feedback=fb,
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
            feedback=fb,
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
            feedback=fb,
            is_child_algorithm=True
        )

        # ------------------------------------------------------------------
        # DIRECT DIJKSTRA TRACING ON THE STEP 18 VORONOI NETWORK
        # ------------------------------------------------------------------
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
            feedback=fb,
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
            feedback=fb,
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
            feedback=fb,
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
            feedback=fb,
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
            feedback=fb, is_child_algorithm=True)

        # Retain fields
        fb.setCurrentStep(24)
        alg_params = {
            'FIELDS': ['AUTO'],
            'INPUT': outputs['AddAutoincrementalField']['OUTPUT'],
            'OUTPUT': parameters['Centerline']  # parameters[self.OUTPUT]
        }
        outputs['RetainFields'] = processing.run(
            'native:retainfields', alg_params,
            context=context, feedback=fb,
            is_child_algorithm=True)

        results['Centerline'] = outputs['RetainFields']['OUTPUT']

        layer_id = results['Centerline']
        layer_details = context.layerToLoadOnCompletionDetails(layer_id)
        layer_details.name = "centerline"
        # results[self.OUTPUT] = outputs['RetainFields']['OUTPUT']
        return results