# -*- coding: utf-8 -*-
# RivaPlan: QGIS Processing tools for river planform morphology analysis
# Copyright (C) 2026 Rohit Sharma
#
# All rights reserved for this reviewer-access script archive.
# This file is provided for editorial and peer-review evaluation only.
#
# The public release of RivaPlan is planned as a QGIS Processing plugin
# under a GPL-compatible open-source license after article acceptance/publication.

from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessing,
    QgsProcessingUtils,
    QgsProcessingException,
    QgsProcessingAlgorithm,
    QgsProcessingParameterCrs,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterEnum,
    QgsProcessingParameterNumber,
    QgsProcessingParameterFeatureSink,
    QgsExpressionContextScope,
    QgsProcessingMultiStepFeedback
)
from qgis import processing


class banklineshifting(QgsProcessingAlgorithm):

    NEWBANKLINE = 'NEWBANKLINE'
    OLDBANKLINE = 'OLDBANKLINE'
    INTERVAL = 'INTERVAL'
    CENTERLINE = 'CENTERLINE'
    OUTPUT_CRS = 'CRS'
    BANK = 'BANK'
    OUTPUT = 'OUTPUT'

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

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
        return self.tr("This tool calculates the shifting distance between two banklines.")

    def initAlgorithm(self, config=None):

        self.addParameter(
            QgsProcessingParameterNumber(
                self.INTERVAL,
                self.tr('Chainage Interval'),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=100
            )
        )

        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.CENTERLINE,
                self.tr('Centerline'),
                types=[QgsProcessing.TypeVectorLine],
                defaultValue=None
            )
        )

        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.NEWBANKLINE,
                self.tr('New Bank Line'),
                types=[QgsProcessing.TypeVectorLine],
                defaultValue=None
            )
        )

        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.OLDBANKLINE,
                self.tr('Old Bank Line'),
                types=[QgsProcessing.TypeVectorLine],
                defaultValue=None
            )
        )

        self.addParameter(
            QgsProcessingParameterCrs(
                self.OUTPUT_CRS,
                self.tr("Working CRS (projected)"),
                defaultValue="EPSG:32644"
            )
        )

        self.addParameter(
            QgsProcessingParameterEnum(
                self.BANK,
                self.tr('Select the Bank'),
                options=['Left Bank', 'Right Bank'],
                allowMultiple=False,
                defaultValue=0
            )
        )

        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                self.tr('Bankline Shifting'),
                type=QgsProcessing.TypeVectorPoint,  # your final is a POINT layer (from lineintersections chainage table)
                createByDefault=True,
                supportsAppend=False
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        results = {}
        outputs = {}

        newBankLayer = self.parameterAsVectorLayer(parameters, self.NEWBANKLINE, context)
        oldBankLayer = self.parameterAsVectorLayer(parameters, self.OLDBANKLINE, context)
        centerlineLayer = self.parameterAsVectorLayer(parameters, self.CENTERLINE, context)
        chainageInterval = self.parameterAsInt(parameters, self.INTERVAL, context)
        bank_selection_index = self.parameterAsEnum(parameters, self.BANK, context)
        out_crs = self.parameterAsCrs(parameters, self.OUTPUT_CRS, context)

        # ---------------------------------------------------------------------
        # 0) Reproject inputs to working CRS (so lengths/diag are meaningful)
        # ---------------------------------------------------------------------
        alg_params = {'INPUT': centerlineLayer, 'TARGET_CRS': out_crs, 'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT}
        outputs['centerline_proj'] = processing.run('native:reprojectlayer', alg_params,
                                                   context=context, feedback=feedback, is_child_algorithm=True)

        alg_params = {'INPUT': newBankLayer, 'TARGET_CRS': out_crs, 'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT}
        outputs['newbank_proj'] = processing.run('native:reprojectlayer', alg_params,
                                                context=context, feedback=feedback, is_child_algorithm=True)

        alg_params = {'INPUT': oldBankLayer, 'TARGET_CRS': out_crs, 'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT}
        outputs['oldbank_proj'] = processing.run('native:reprojectlayer', alg_params,
                                                context=context, feedback=feedback, is_child_algorithm=True)

        centerline_proj = outputs['centerline_proj']['OUTPUT']
        newbank_proj = outputs['newbank_proj']['OUTPUT']
        oldbank_proj = outputs['oldbank_proj']['OUTPUT']
        
        fb = QgsProcessingMultiStepFeedback(21, feedback)

        # ---------------------------------------------------------------------
        # 1) Points along centerline (chainage points)
        # ---------------------------------------------------------------------
        fb.setCurrentStep(0)
        alg_params = {
            'DISTANCE': chainageInterval,
            'END_OFFSET': 0,
            'INPUT': centerline_proj,
            'START_OFFSET': 0,
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['PointsAlongGeometry'] = processing.run(
                                        'native:pointsalonglines', alg_params,
                                        context=context, feedback=fb, 
                                        is_child_algorithm=True)

        points_id = outputs['PointsAlongGeometry']['OUTPUT']
        points_layer = QgsProcessingUtils.mapLayerFromString(points_id, context)
        # Use layer id (best in Processing); fallback to name
        points_ref = points_layer.id() if points_layer else 'PointsAlongGeometry'

        # ---------------------------------------------------------------------
        # 2) Compute transect half-length from extent diagonal of merged inputs
        # ---------------------------------------------------------------------
        fb.setCurrentStep(1)
        alg_params = {
            'LAYERS': [newbank_proj, oldbank_proj, centerline_proj],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['merged_banks'] = processing.run('native:mergevectorlayers', alg_params,
                                context=context, feedback=fb, is_child_algorithm=True)

        fb.setCurrentStep(2)
        alg_params = {
            'INPUT': outputs['merged_banks']['OUTPUT'],
            'ROUND_TO': 0,
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['banks_extent'] = processing.run(
                                    'native:polygonfromlayerextent', alg_params,
                                    context=context, feedback=fb, is_child_algorithm=True)

        fb.setCurrentStep(3)
        alg_params = {
            'INPUT': outputs['banks_extent']['OUTPUT'],
            'FIELD_NAME': 'DIAGONAL',
            'FIELD_TYPE': 0,  # Float
            'FIELD_LENGTH': 20,
            'FIELD_PRECISION': 3,
            'FORMULA': '0.5 * sqrt("WIDTH"^2 + "HEIGHT"^2)',
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['banks_extent_diag'] = processing.run(
                                            'native:fieldcalculator', alg_params,
                                            context=context, feedback=fb, is_child_algorithm=True)

        diag_layer = QgsProcessingUtils.mapLayerFromString(outputs['banks_extent_diag']['OUTPUT'], context)
        diag_feat = next(diag_layer.getFeatures())
        diag = float(diag_feat['DIAGONAL'])

        # ---------------------------------------------------------------------
        # 3) Perpendicular transects from chainage points using nearest centerline
        # ---------------------------------------------------------------------
        fb.setCurrentStep(4)
        alg_params = {
            'EXPRESSION': f"""
                extend(
                    make_line(
                        $geometry,
                        project($geometry, {diag}, radians("angle" - 90))
                    ),
                    {diag}, 0
                )
                """.strip(),
            'INPUT': points_id,
            'OUTPUT_GEOMETRY': 1,  # Line
            'WITH_M': False,
            'WITH_Z': False,
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['Transects'] = processing.run(
                                'native:geometrybyexpression', alg_params,
                                context=context, feedback=fb, is_child_algorithm=True)

        # ---------------------------------------------------------------------
        # 4) Intersections with NEW bank
        # ---------------------------------------------------------------------
        fb.setCurrentStep(5)
        alg_params = {
            'INPUT': newbank_proj,
            'INPUT_FIELDS': [''],
            'INTERSECT': outputs['Transects']['OUTPUT'],
            'INTERSECT_FIELDS': ['distance'],
            'INTERSECT_FIELDS_PREFIX': '',
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['LineIntersectionsNew'] = processing.run(
                                            'native:lineintersections', alg_params,
                                            context=context, feedback=fb, is_child_algorithm=True)

        # ---------------------------------------------------------------------
        # 4b) NEW intersections: hubline shortest-per-chainage, then rebuild points
        # ---------------------------------------------------------------------
        fb.setCurrentStep(6)
        outputs['HubLinesNew_raw'] = processing.run(
            "native:hublines",
            {
                'HUBS': points_id,
                'HUB_FIELD': 'distance',
                'HUB_FIELDS': [],
                'SPOKES': outputs['LineIntersectionsNew']['OUTPUT'],  # ALL intersections
                'SPOKE_FIELD': 'distance',
                'SPOKE_FIELDS': [],
                'GEODESIC': False,
                'GEODESIC_DISTANCE': 1000,
                'ANTIMERIDIAN_SPLIT': False,
                'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
            },
            context=context, feedback=fb, is_child_algorithm=True
        )

        fb.setCurrentStep(7)
        outputs['HubLinesNew_len'] = processing.run(
            'native:fieldcalculator',
            {
                'INPUT': outputs['HubLinesNew_raw']['OUTPUT'],
                'FIELD_NAME': 'L',
                'FIELD_TYPE': 0,      # Float
                'FIELD_LENGTH': 14,
                'FIELD_PRECISION': 3,
                'FORMULA': '$length',
                'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
            },
            context=context, feedback=fb, is_child_algorithm=True
        )

        fb.setCurrentStep(8)
        outputs['HubLinesNew_shortest'] = processing.run(
            'native:extractbyexpression',
            {
                'INPUT': outputs['HubLinesNew_len']['OUTPUT'],
                'EXPRESSION': """
        "L" = aggregate(@layer, 'min', "L", "distance" = attribute(@parent,'distance'))
        """.strip(),
                'OUTPUT':  QgsProcessing.TEMPORARY_OUTPUT
            },
            context=context, feedback=fb, is_child_algorithm=True
        )
        
        fb.setCurrentStep(9)
        outputs['NewBankPts_filtered'] = processing.run(
            'native:geometrybyexpression',
            {
                'INPUT': outputs['HubLinesNew_shortest']['OUTPUT'],
                'OUTPUT_GEOMETRY': 2,  # For Point
                'WITH_M': False,
                'WITH_Z': False,
                'EXPRESSION': 'end_point($geometry)',
                'OUTPUT':  QgsProcessing.TEMPORARY_OUTPUT
            },
            context=context, feedback=fb, is_child_algorithm=True
        )
        

        # ---------------------------------------------------------------------
        # 5) Intersections with OLD bank
        # ---------------------------------------------------------------------
        fb.setCurrentStep(10)
        alg_params = {
            'INPUT': oldbank_proj,
            'INPUT_FIELDS': [''],
            'INTERSECT': outputs['Transects']['OUTPUT'],
            'INTERSECT_FIELDS': ['distance'],
            'INTERSECT_FIELDS_PREFIX': '',
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['LineIntersectionsOld'] = processing.run(
                                            'native:lineintersections', alg_params,
                                            context=context, feedback=fb, is_child_algorithm=True)

        fb.setCurrentStep(11)
        outputs['HubLinesOld_raw'] = processing.run(
            "native:hublines",
            {
                'HUBS': points_id,
                'HUB_FIELD': 'distance',
                'HUB_FIELDS': [],
                'SPOKES': outputs['LineIntersectionsOld']['OUTPUT'],
                'SPOKE_FIELD': 'distance',
                'SPOKE_FIELDS': [],
                'GEODESIC': False,
                'GEODESIC_DISTANCE': 1000,
                'ANTIMERIDIAN_SPLIT': False,
                'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
            },
            context=context, feedback=fb, is_child_algorithm=True
        )

        fb.setCurrentStep(12)
        outputs['HubLinesOld_len'] = processing.run(
            'native:fieldcalculator',
            {
                'INPUT': outputs['HubLinesOld_raw']['OUTPUT'],
                'FIELD_NAME': 'L',
                'FIELD_TYPE': 0,
                'FIELD_LENGTH': 14,
                'FIELD_PRECISION': 3,
                'FORMULA': '$length',
                'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
            },
            context=context, feedback=fb, is_child_algorithm=True
        )

        fb.setCurrentStep(13)
        outputs['HubLinesOld_shortest'] = processing.run(
            'native:extractbyexpression',
            {
                'INPUT': outputs['HubLinesOld_len']['OUTPUT'],
                'EXPRESSION': """
        "L" = aggregate(@layer, 'min', "L", "distance" = attribute(@parent,'distance'))
        """.strip(),
                'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
            },
            context=context, feedback=fb, is_child_algorithm=True
        )
        
        fb.setCurrentStep(14)
        outputs['OldBankPts_filtered'] = processing.run(
            'native:geometrybyexpression',
            {
                'INPUT': outputs['HubLinesOld_shortest']['OUTPUT'],
                'OUTPUT_GEOMETRY': 2,  # Point
                'WITH_M': False,
                'WITH_Z': False,
                'EXPRESSION': 'end_point($geometry)',
                'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
            },
            context=context, feedback=fb, is_child_algorithm=True
        )
        
        # ---------------------------------------------------------------------
        # 6) Hub lines: centerline points -> NEW, centerline points -> OLD, OLD -> NEW (AB)
        # ---------------------------------------------------------------------
        fb.setCurrentStep(15)
        alg_params = {
            'ANTIMERIDIAN_SPLIT': False,
            'GEODESIC': False,
            'GEODESIC_DISTANCE': 1000,
            'HUBS': points_id,
            'HUB_FIELD': 'distance',
            'HUB_FIELDS': [''],
            'SPOKES': outputs['NewBankPts_filtered']['OUTPUT'],
            'SPOKE_FIELD': 'distance',
            'SPOKE_FIELDS': [''],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['HubLinesNew'] = processing.run('native:hublines', alg_params,
                            context=context, feedback=fb, is_child_algorithm=True)

        fb.setCurrentStep(16)
        alg_params = {
            'ANTIMERIDIAN_SPLIT': False,
            'GEODESIC': False,
            'GEODESIC_DISTANCE': 1000,
            'HUBS': points_id,
            'HUB_FIELD': 'distance',
            'HUB_FIELDS': [''],
            'SPOKES': outputs['OldBankPts_filtered']['OUTPUT'],
            'SPOKE_FIELD': 'distance',
            'SPOKE_FIELDS': [''],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['HubLinesOld'] = processing.run('native:hublines', alg_params,
                                context=context, feedback=fb, is_child_algorithm=True)

        fb.setCurrentStep(11)
        alg_params = {
            'ANTIMERIDIAN_SPLIT': False,
            'GEODESIC': False,
            'GEODESIC_DISTANCE': 1000,
            'HUBS': outputs['OldBankPts_filtered']['OUTPUT'],
            'HUB_FIELD': 'distance',
            'HUB_FIELDS': [''],
            'SPOKES': outputs['NewBankPts_filtered']['OUTPUT'],
            'SPOKE_FIELD': 'distance',
            'SPOKE_FIELDS': [''],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['HubLinesAB'] = processing.run('native:hublines', alg_params,
                                context=context, feedback=fb, is_child_algorithm=True)

        # ---------------------------------------------------------------------
        # 7) Length fields l1, l2, l3
        # ---------------------------------------------------------------------
        fb.setCurrentStep(12)
        alg_params = {
            'FIELD_LENGTH': 10,
            'FIELD_NAME': 'l2',
            'FIELD_PRECISION': 0,
            'FIELD_TYPE': 1,
            'FORMULA': '$length',
            'INPUT': outputs['HubLinesNew']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['LenNew'] = processing.run('native:fieldcalculator', alg_params,
                            context=context, feedback=fb, is_child_algorithm=True)

        fb.setCurrentStep(13)
        alg_params = {
            'FIELD_LENGTH': 10,
            'FIELD_NAME': 'l1',
            'FIELD_PRECISION': 0,
            'FIELD_TYPE': 1,
            'FORMULA': '$length',
            'INPUT': outputs['HubLinesOld']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['LenOld'] = processing.run('native:fieldcalculator', alg_params,
                            context=context, feedback=fb, is_child_algorithm=True)

        fb.setCurrentStep(14)
        alg_params = {
            'FIELD_LENGTH': 10,
            'FIELD_NAME': 'l3',
            'FIELD_PRECISION': 0,
            'FIELD_TYPE': 1,
            'FORMULA': '$length',
            'INPUT': outputs['HubLinesAB']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['LenAB'] = processing.run('native:fieldcalculator', alg_params,
                        context=context, feedback=fb, is_child_algorithm=True)

        # ---------------------------------------------------------------------
        # 8) Join l1 and l2 onto AB (by distance)
        # ---------------------------------------------------------------------
        fb.setCurrentStep(15)
        alg_params = {
            'DISCARD_NONMATCHING': False,
            'FIELD': 'distance',
            'FIELDS_TO_COPY': ['l1'],
            'FIELD_2': 'distance',
            'INPUT': outputs['LenAB']['OUTPUT'],
            'INPUT_2': outputs['LenOld']['OUTPUT'],
            'METHOD': 1,
            'PREFIX': '',
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['Join_l1'] = processing.run('native:joinattributestable', alg_params,
                            context=context, feedback=fb, is_child_algorithm=True)

        fb.setCurrentStep(16)
        alg_params = {
            'DISCARD_NONMATCHING': False,
            'FIELD': 'distance',
            'FIELDS_TO_COPY': ['l2'],
            'FIELD_2': 'distance',
            'INPUT': outputs['Join_l1']['OUTPUT'],
            'INPUT_2': outputs['LenNew']['OUTPUT'],
            'METHOD': 1,
            'PREFIX': '',
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['Join_l2'] = processing.run('native:joinattributestable', alg_params,
                            context=context, feedback=fb, is_child_algorithm=True)

        # ---------------------------------------------------------------------
        # 9) Direction field
        # ---------------------------------------------------------------------
        fb.setCurrentStep(17)
        if bank_selection_index == 0:
            formula = (
                "if(l3 = 0, 'No Shift', "
                "if(abs(l1 - (l2 + l3)) < abs(l1 - abs(l2 - l3)), 'Left', 'Right'))"
            )
        else:
            formula = (
                "if(l3 = 0, 'No Shift', "
                "if(abs(l1 - (l2 + l3)) < abs(l1 - abs(l2 - l3)), 'Right', 'Left'))"
            )

        alg_params = {
            'FIELD_LENGTH': 10,
            'FIELD_NAME': 'Direction',
            'FIELD_PRECISION': 0,
            'FIELD_TYPE': 2,
            'FORMULA': formula,
            'INPUT': outputs['Join_l2']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['Direction'] = processing.run('native:fieldcalculator', alg_params,
                            context=context, feedback=fb, is_child_algorithm=True)

        # ---------------------------------------------------------------------
        # 10) Keep fields + rename + final output
        # ---------------------------------------------------------------------
        fb.setCurrentStep(18)
        alg_params = {
            'FIELDS': 'distance;angle;l3;Direction',
            'INPUT': outputs['Direction']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['Keep'] = processing.run('native:retainfields', alg_params,
                        context=context, feedback=fb, is_child_algorithm=True)

        fb.setCurrentStep(19)
        alg_params = {
            'FIELD': 'l3',
            'INPUT': outputs['Keep']['OUTPUT'],
            'NEW_NAME': 'Shift',
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['Rename1'] = processing.run('native:renametablefield', alg_params,
                        context=context, feedback=fb, is_child_algorithm=True)
                        
        fb.setCurrentStep(20)
        alg_params = {
            'FIELD_NAME': 'SignedShift',
            'FIELD_TYPE': 0,        # Float
            'FIELD_LENGTH': 10,
            'FIELD_PRECISION': 2,
            'FORMULA': """
        CASE
          WHEN "Direction" = 'Left'  THEN -abs("Shift")
          WHEN "Direction" = 'Right' THEN  abs("Shift")
          ELSE 0
        END
        """,
            'INPUT': outputs['Rename1']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['SignedShift'] = processing.run(
            'native:fieldcalculator',
            alg_params,
            context=context,
            feedback=fb,
            is_child_algorithm=True
        )


        fb.setCurrentStep(21)
        alg_params = {
            'FIELD': 'distance',
            'INPUT': outputs['SignedShift']['OUTPUT'],
            'NEW_NAME': 'Chainage',
            'OUTPUT': parameters[self.OUTPUT]
        }
        outputs['Final'] = processing.run('native:renametablefield', alg_params,
                            context=context, feedback=fb, is_child_algorithm=True)

        results[self.OUTPUT] = outputs['Final']['OUTPUT']
        layer_id = results[self.OUTPUT]
        details = context.layerToLoadOnCompletionDetails(layer_id)

        if bank_selection_index == 0:
            details.name = "LeftBank_Shifting"
        else:
            details.name = "RightBank_Shifting"

        return results
