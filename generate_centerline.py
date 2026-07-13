# -*- coding: utf-8 -*-
# RivaPlan: QGIS Processing tools for river planform morphology analysis
# Copyright (C) 2026 Rohit Sharma
#
# All rights reserved for this reviewer-access script archive.
# This file is provided for editorial and peer-review evaluation only.
#
# The public release of RivaPlan is planned as a QGIS Processing plugin
# under a GPL-compatible open-source license after article acceptance/publication.


"""
Bank lines to centerline (QGIS Processing Algorithm)

This tool creates a river centerline using left and right bank polylines.
Method:
1) Merge banks and densify (regular vertex spacing).
2) Create Voronoi polygons from bank vertices, convert to Voronoi edges.
3) Remove Voronoi edges touching the banks -> keep interior edges only.
4) From interior edge vertices, compute |d_left - d_right| using overlay_nearest().
5) Keep vertices where |d_left - d_right| ~= 0 -> centerline nodes.
6) Keep interior Voronoi edges intersecting these nodes and remove off-node branches.
7) Dissolve to centerline.
8) Trim ends by connecting bank endpoints using shortest lines and splitting the centerline.

Notes:
- overlay_nearest() requires the LAYER NAME (string), not the python QgsVectorLayer object.
- This script assumes banks are reasonably clean and in a projected CRS (meters/feet).
"""

from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (QgsProcessing,
                       QgsFeatureSink,
                       QgsVectorLayer,
                       QgsExpression,
                       QgsProcessingException,
                       QgsProcessingAlgorithm,
                       QgsProcessingParameterCrs,
                       QgsProcessingParameterFeatureSource,
                       QgsProcessingParameterVectorLayer,
                       QgsProcessingParameterNumber,
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

        leftBankLayer = self.parameterAsVectorLayer(parameters, self.LEFTBANK, context)
        rightBankLayer = self.parameterAsVectorLayer(parameters, self.RIGHTBANK, context)

        if leftBankLayer is None or rightBankLayer is None:
            raise QgsProcessingException("Invalid input: left and/or right bank layers are missing")

        fb = QgsProcessingMultiStepFeedback(27, feedback)

        # Merge Bank Lines
        fb.setCurrentStep(0)
        alg_params = {
            'LAYERS': [leftBankLayer, rightBankLayer],
            'CRS': parameters[self.OUTPUT_CRS],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT}

        outputs['MergeVectorLayers'] = processing.run(
            'native:mergevectorlayers',
            alg_params, context=context, feedback=fb,
            is_child_algorithm=True)

        # Densify by interval
        fb.setCurrentStep(1)
        alg_params = {
            'INPUT': outputs['MergeVectorLayers']['OUTPUT'],
            'INTERVAL': 5,
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT}

        outputs['DensifyByInterval'] = processing.run(
            'native:densifygeometriesgivenaninterval',
            alg_params, context=context, feedback=fb,
            is_child_algorithm=True)

        # Extract vertices
        fb.setCurrentStep(2)
        alg_params = {
            'INPUT': outputs['DensifyByInterval']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['ExtractVertices_01'] = processing.run(
            'native:extractvertices',
            alg_params, context=context, feedback=fb,
            is_child_algorithm=True)

        # Voronoi polygons
        fb.setCurrentStep(3)
        alg_params = {
            'BUFFER': 0,
            'INPUT': outputs['ExtractVertices_01']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['VoronoiPolygons'] = processing.run(
            'qgis:voronoipolygons',
            alg_params, context=context, feedback=fb,
            is_child_algorithm=True)

        # Polygons to lines
        fb.setCurrentStep(4)
        alg_params = {
            'INPUT': outputs['VoronoiPolygons']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['PolygonsToLines'] = processing.run(
            'native:polygonstolines',
            alg_params, context=context, feedback=fb,
            is_child_algorithm=True)

        # Explode lines
        fb.setCurrentStep(5)
        alg_params = {
            'INPUT': outputs['PolygonsToLines']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['ExplodeLines'] = processing.run(
            'native:explodelines',
            alg_params, context=context, feedback=fb,
            is_child_algorithm=True)

        # Remove duplicate vertices
        fb.setCurrentStep(6)
        alg_params = {
            'INPUT': outputs['ExplodeLines']['OUTPUT'],
            'TOLERANCE': 1e-06,
            'USE_Z_VALUE': False,
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['RemoveDuplicateVertices'] = processing.run(
            'native:removeduplicatevertices',
            alg_params, context=context,
            feedback=fb, is_child_algorithm=True)

        # Delete duplicate geometries
        fb.setCurrentStep(7)
        alg_params = {
            'INPUT': outputs['RemoveDuplicateVertices']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['DeleteDuplicateGeometriesVoronoiPolylines'] = processing.run(
            'native:deleteduplicategeometries',
            alg_params, context=context,
            feedback=fb, is_child_algorithm=True)

        # Create spatial index
        fb.setCurrentStep(8)
        alg_params = {
            'INPUT': outputs['DeleteDuplicateGeometriesVoronoiPolylines']['OUTPUT']
        }
        outputs['CreateSpatialIndex_01'] = processing.run(
            'native:createspatialindex', alg_params,
            context=context, feedback=fb, is_child_algorithm=True)

        # Extract by location between removing duplicate geometries (spatial index file)
        # and bank lines (merged files)
        fb.setCurrentStep(9)
        alg_params = {
            'INPUT': outputs['CreateSpatialIndex_01']['OUTPUT'],
            'INTERSECT': outputs['MergeVectorLayers']['OUTPUT'],
            'PREDICATE': [0, 4, 7],  # intersect,touch,cross
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['ExtractByLocation_01'] = processing.run(
            'native:extractbylocation', alg_params,
            context=context, feedback=fb, is_child_algorithm=True)

        # Difference between cleaned after removing duplicate geometries (spatial index file)
        # and extrated by location file
        fb.setCurrentStep(10)
        alg_params = {
            'INPUT': outputs['CreateSpatialIndex_01']['OUTPUT'],
            'OVERLAY': outputs['ExtractByLocation_01']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['Difference_01'] = processing.run(
            'native:difference', alg_params,
            context=context, feedback=fb,
            is_child_algorithm=True)

        # Extract vertices
        fb.setCurrentStep(11)
        alg_params = {
            'INPUT': outputs['Difference_01']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['ExtractVertices_02'] = processing.run(
            'native:extractvertices', alg_params,
            context=context, feedback=fb,
            is_child_algorithm=True)

        # Delete duplicate geometries vertices
        fb.setCurrentStep(12)
        alg_params = {
            'INPUT': outputs['ExtractVertices_02']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['DeleteDuplicateGeometriesVertices'] = processing.run(
            'native:deleteduplicategeometries',
            alg_params, context=context,
            feedback=fb, is_child_algorithm=True)

        # Format the expression string using f-string syntax for better readability
        expression = f"""abs(
                        length(
                            make_line(
                                $geometry, closest_point(
                                    overlay_nearest('{leftBankLayer.name()}', $geometry)[0], $geometry)))
                        -
                        length(
                            make_line(
                                $geometry, closest_point(
                                    overlay_nearest('{rightBankLayer.name()}', $geometry)[0], $geometry)))
                    )"""

        # Field calculator
        fb.setCurrentStep(13)
        alg_params = {
            'FIELD_LENGTH': 10,
            'FIELD_NAME': 'Distance_to_Centerline',
            'FIELD_PRECISION': 3,
            'FIELD_TYPE': 1,  # Use 0 for decimal numbers, 1 for integers
            'FORMULA': expression,
            'INPUT': outputs['DeleteDuplicateGeometriesVertices']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }

        outputs['FieldCalculator_01'] = processing.run(
            'native:fieldcalculator',
            alg_params, context=context, feedback=fb,
            is_child_algorithm=True)

        # Extract by expression to get the centerline nodes
        fb.setCurrentStep(14)
        alg_params = {
            'EXPRESSION': '"Distance_to_Centerline" = 0',
            'INPUT': outputs['FieldCalculator_01']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT  # parameters[self.OUTPUT]
        }
        outputs['ExtractByExpression_01'] = processing.run(
            'native:extractbyexpression',
            alg_params, context=context,
            feedback=fb, is_child_algorithm=True)

        # Create spatial index
        fb.setCurrentStep(15)
        alg_params = {
            'INPUT': outputs['ExtractByExpression_01']['OUTPUT']
        }
        outputs['CreateSpatialIndex_02'] = processing.run(
            'native:createspatialindex', alg_params,
            context=context, feedback=fb,
            is_child_algorithm=True)

        # Extract by location to identify the lines that intersects with the centerline nodes
        fb.setCurrentStep(16)
        alg_params = {
            'INPUT': outputs['Difference_01']['OUTPUT'],
            'INTERSECT': outputs['CreateSpatialIndex_02']['OUTPUT'],
            'PREDICATE': [0],  # intersect
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['ExtractByLocation_02'] = processing.run(
            'native:extractbylocation', alg_params,
            context=context, feedback=fb,
            is_child_algorithm=True)

        # Extract vertices
        fb.setCurrentStep(17)
        alg_params = {
            'INPUT': outputs['ExtractByLocation_02']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['ExtractVertices_03'] = processing.run(
            'native:extractvertices', alg_params,
            context=context, feedback=fb,
            is_child_algorithm=True)

        # Delete duplicate geometries vertices
        fb.setCurrentStep(18)
        alg_params = {
            'INPUT': outputs['ExtractVertices_03']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['DeleteDuplicateGeometriesVertices_02'] = processing.run(
            'native:deleteduplicategeometries',
            alg_params, context=context,
            feedback=fb,
            is_child_algorithm=True)

        # Join attributes by nearest
        fb.setCurrentStep(19)
        alg_params = {
            'DISCARD_NONMATCHING': False,
            'FIELDS_TO_COPY': [],
            'INPUT': outputs['DeleteDuplicateGeometriesVertices_02']['OUTPUT'],
            'INPUT_2': outputs['ExtractByExpression_01']['OUTPUT'],
            'MAX_DISTANCE': None,
            'NEIGHBORS': 1,
            'PREFIX': '',
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['JoinAttributesByNearest'] = processing.run(
            'native:joinbynearest', alg_params,
            context=context, feedback=fb,
            is_child_algorithm=True)

        # Field calculator to calculate distnace between all points and centerline nodes
        fb.setCurrentStep(20)
        alg_params = {
            'FIELD_LENGTH': 10,
            'FIELD_NAME': 'Distance_CN_AP',
            'FIELD_PRECISION': 3,
            'FIELD_TYPE': 0,  # Float
            'FORMULA': ' sqrt((( "feature_x" - "nearest_x" ) ^ 2)+(( "feature_y" - "nearest_y" ) ^ 2))',
            'INPUT': outputs['JoinAttributesByNearest']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['FieldCalculator_02'] = processing.run(
            'native:fieldcalculator', alg_params,
            context=context, feedback=fb,
            is_child_algorithm=True)

        # Extract by expression to extract points not on centerline
        fb.setCurrentStep(21)
        alg_params = {
            'EXPRESSION': '"Distance_CN_AP">0.0',
            'INPUT': outputs['FieldCalculator_02']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['ExtractByExpression_02'] = processing.run(
            'native:extractbyexpression',
            alg_params, context=context,
            feedback=fb, is_child_algorithm=True)

        # Extract by location to extract the lines that are not related to centerline
        fb.setCurrentStep(22)
        alg_params = {
            'INPUT': outputs['ExtractByLocation_02']['OUTPUT'],
            'INTERSECT': outputs['ExtractByExpression_02']['OUTPUT'],
            'PREDICATE': [0],  # intersect
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['ExtractByLocation_03'] = processing.run(
            'native:extractbylocation', alg_params,
            context=context, feedback=fb,
            is_child_algorithm=True)

        # Difference between all lines linked to centerlines nodes and lines not part of centerline
        fb.setCurrentStep(23)
        alg_params = {
            'INPUT': outputs['ExtractByLocation_02']['OUTPUT'],
            'OVERLAY': outputs['ExtractByLocation_03']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['Difference_02'] = processing.run(
            'native:difference', alg_params,
            context=context, feedback=fb,
            is_child_algorithm=True)

        # Dissolve
        fb.setCurrentStep(24)
        alg_params = {
            'FIELD': [],
            'INPUT': outputs['Difference_02']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['Dissolve'] = processing.run(
            'native:dissolve', alg_params, context=context,
            feedback=fb, is_child_algorithm=True)

        # ------------------------------------------------------------
        # Use the dissolved raw centreline directly.
        # End trimming is intentionally not included in this version.
        # ------------------------------------------------------------
        outputs['Centerline_FinalGeom'] = {
            'OUTPUT': outputs['Dissolve']['OUTPUT']
        }

        # Add autoincremental field
        fb.setCurrentStep(25)
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
        fb.setCurrentStep(26)
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
