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
    QgsProcessingAlgorithm,
    QgsProcessingParameterCrs,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterNumber,
    QgsProcessingParameterFeatureSink,
    QgsProcessingMultiStepFeedback
)
from qgis import processing


class centerlineshifting(QgsProcessingAlgorithm):

    NEWCL = 'NEWCL'
    OLDCL = 'OLDCL'
    INTERVAL = 'INTERVAL'
    OUTPUT_CRS = 'CRS'
    OUTPUT = 'OUTPUT'

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

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
        return self.tr(
            "Shift between NEW and OLD centerlines using perpendicular transects at NEW chainage points.\n"
            "Cleans multiple intersections per chainage by keeping the intersection closest to the principal chainage point."
        )

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
                self.NEWCL,
                self.tr('New Centerline'),
                types=[QgsProcessing.TypeVectorLine],
                defaultValue=None
            )
        )

        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.OLDCL,
                self.tr('Old Centerline'),
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
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                self.tr('Centerline Shifting (Hub Lines)'),
                type=QgsProcessing.TypeVectorLine,
                createByDefault=True,
                supportsAppend=False
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        results = {}
        outputs = {}

        newCL = self.parameterAsVectorLayer(parameters, self.NEWCL, context)
        oldCL = self.parameterAsVectorLayer(parameters, self.OLDCL, context)
        chainageInterval = self.parameterAsInt(parameters, self.INTERVAL, context)
        out_crs = self.parameterAsCrs(parameters, self.OUTPUT_CRS, context)

        fb = QgsProcessingMultiStepFeedback(22, feedback)

        # ---------------------------------------------------------------------
        # 0) Reproject inputs
        # ---------------------------------------------------------------------
        fb.setCurrentStep(0)
        alg_params = {'INPUT': newCL, 'TARGET_CRS': out_crs, 'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT}
        outputs['NewCL_Reproject'] = processing.run(
            'native:reprojectlayer', alg_params,
            context=context, feedback=fb, is_child_algorithm=True
        )

        fb.setCurrentStep(1)
        alg_params = {'INPUT': oldCL, 'TARGET_CRS': out_crs, 'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT}
        outputs['OldCL_Reproject'] = processing.run(
            'native:reprojectlayer', alg_params,
            context=context, feedback=fb, is_child_algorithm=True
        )

        newcl_proj = outputs['NewCL_Reproject']['OUTPUT']
        oldcl_proj = outputs['OldCL_Reproject']['OUTPUT']

        # ---------------------------------------------------------------------
        # 1) Points along NEW centerline (chainage points)
        # ---------------------------------------------------------------------
        fb.setCurrentStep(2)
        alg_params = {
            'DISTANCE': chainageInterval,
            'END_OFFSET': 0,
            'INPUT': newcl_proj,
            'START_OFFSET': 0,
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['PointsAlongNew'] = processing.run(
            'native:pointsalonglines', alg_params,
            context=context, feedback=fb, is_child_algorithm=True
        )

        # ---------------------------------------------------------------------
        # 2) Semi-diagonal for transect length
        # ---------------------------------------------------------------------
        fb.setCurrentStep(4)
        alg_params = {'LAYERS': [newcl_proj, oldcl_proj], 'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT}
        outputs['MergeCL'] = processing.run(
            'native:mergevectorlayers', alg_params,
            context=context, feedback=fb, is_child_algorithm=True
        )

        fb.setCurrentStep(5)
        alg_params = {'INPUT': outputs['MergeCL']['OUTPUT'], 'ROUND_TO': 0, 'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT}
        outputs['ExtentPoly'] = processing.run(
            'native:polygonfromlayerextent', alg_params,
            context=context, feedback=fb, is_child_algorithm=True
        )

        fb.setCurrentStep(6)
        alg_params = {
            'INPUT': outputs['ExtentPoly']['OUTPUT'],
            'FIELD_NAME': 'DIAGONAL',
            'FIELD_TYPE': 0,  # Float
            'FIELD_LENGTH': 20,
            'FIELD_PRECISION': 3,
            'FORMULA': '0.5 * sqrt("WIDTH"^2 + "HEIGHT"^2)',
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['Diag'] = processing.run(
            'native:fieldcalculator', alg_params,
            context=context, feedback=fb, is_child_algorithm=True
        )

        diag_layer = QgsProcessingUtils.mapLayerFromString(outputs['Diag']['OUTPUT'], context)
        diag_feat = next(diag_layer.getFeatures())
        diag = float(diag_feat['DIAGONAL'])


        # ---------------------------------------------------------------------
        # 4) Build perpendicular transects (using your extend/make_line/project)
        # ---------------------------------------------------------------------
        fb.setCurrentStep(7)
        alg_params = {
            'INPUT': outputs['PointsAlongNew']['OUTPUT'],
            'OUTPUT_GEOMETRY': 1,  # Line
            'WITH_M': False,
            'WITH_Z': False,
            'EXPRESSION': f"""
                extend(
                    make_line(
                        $geometry,
                        project($geometry, {diag}, radians("angle" - 90))
                    ),
                    {diag}, 0
                )
                """.strip(),
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['Transects'] = processing.run(
            'native:geometrybyexpression', alg_params,
            context=context, feedback=fb, is_child_algorithm=True
        )

        # ---------------------------------------------------------------------
        # 5) Intersections: OLD centerline with transects
        #    IMPORTANT: bring 'ch' and 'angle' from transects (so intersections know their chainage)
        # ---------------------------------------------------------------------
        fb.setCurrentStep(8)
        alg_params = {
            'INPUT': oldcl_proj,
            'INPUT_FIELDS': [''],
            'INTERSECT': outputs['Transects']['OUTPUT'],
            'INTERSECT_FIELDS': ['distance', 'angle'],
            'INTERSECT_FIELDS_PREFIX': '',
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['Intersections'] = processing.run(
            'native:lineintersections', alg_params,
            context=context, feedback=fb, is_child_algorithm=True
        )

        # ---------------------------------------------------------------------
        # 6) Compute dcp = distance(intersection, principal point of SAME ch)
        #    This implements your exact logic correctly (NO overlay_nearest guessing)
        # ---------------------------------------------------------------------
        
        fb.setCurrentStep(9)
        outputs['HubLines_raw'] = processing.run(
            "native:hublines",
            {
                'HUBS': outputs['PointsAlongNew']['OUTPUT'],              # chainage points (principal)
                'HUB_FIELD': 'distance',
                'HUB_FIELDS': [],
                'SPOKES': outputs['Intersections']['OUTPUT'],        # ALL intersection points (multiple per ch)
                'SPOKE_FIELD': 'distance',
                'SPOKE_FIELDS': [],                                  # optional
                'GEODESIC': False,
                'GEODESIC_DISTANCE': 1000,
                'ANTIMERIDIAN_SPLIT': False,
                'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
            },
            context=context, feedback=fb, is_child_algorithm=True
        )
        
        fb.setCurrentStep(10)
        outputs['HubLines_len'] = processing.run(
            'native:fieldcalculator',
            {
                'INPUT': outputs['HubLines_raw']['OUTPUT'],
                'FIELD_NAME': 'L',
                'FIELD_TYPE': 0,
                'FIELD_LENGTH': 14,
                'FIELD_PRECISION': 3,
                'FORMULA': '$length',
                'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
            },
            context=context, feedback=fb, is_child_algorithm=True
        )
        
        fb.setCurrentStep(11)
        outputs['HubLines_shortest'] = processing.run(
            'native:extractbyexpression',
            {
                'INPUT': outputs['HubLines_len']['OUTPUT'],
                'EXPRESSION': """
        "L" = aggregate(@layer, 'min', "L", "distance" = attribute(@parent,'distance'))
        """.strip(),
                'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
            },
            context=context, feedback=fb, is_child_algorithm=True
        )
        
        fb.setCurrentStep(12)
        alg_params = {
            'INPUT': outputs['HubLines_shortest']['OUTPUT'],
            'FIELDS': 'distance;angle;L',
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['KeepFields'] = processing.run(
            'native:retainfields', alg_params,
            context=context, feedback=fb, is_child_algorithm=True
        )
        
        fb.setCurrentStep(13)
        alg_params = {
            'INPUT': outputs['KeepFields']['OUTPUT'],
            'FIELD': 'distance',
            'NEW_NAME': 'Chainage',
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['RenameChainage'] = processing.run(
            'native:renametablefield', alg_params,
            context=context, feedback=fb, is_child_algorithm=True
        )

        fb.setCurrentStep(14)
        alg_params = {
            'INPUT': outputs['RenameChainage']['OUTPUT'],
            'FIELD': 'angle',
            'NEW_NAME': 'NewAngle',
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['RenameAngle'] = processing.run(
            'native:renametablefield', alg_params,
            context=context, feedback=fb, is_child_algorithm=True
        )

        fb.setCurrentStep(15)
        alg_params = {
            'INPUT': outputs['RenameAngle']['OUTPUT'], 
            'FIELD_NAME': 'Shift',
            'FIELD_TYPE': 1,        # Integer
            'FIELD_LENGTH': 10,
            'FIELD_PRECISION': 0,
            'FORMULA': 'round("L", 0)',   
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['RenameShift'] = processing.run(
            'native:fieldcalculator',
            alg_params,
            context=context,
            feedback=fb,
            is_child_algorithm=True
        )

        
        fb.setCurrentStep(16)
        alg_params = {
            'INPUT': outputs['RenameShift']['OUTPUT'],
            'FIELD_NAME': 'Direction',
            'FIELD_TYPE': 2,  # String
            'FIELD_LENGTH': 10,
            'FIELD_PRECISION': 0,
            'FORMULA': """
        with_variable('P', start_point($geometry),
        with_variable('Q', end_point($geometry),
        with_variable('azPQ', azimuth(@P,@Q),
        with_variable('azT', radians("NewAngle"),
        case
          when "Shift" = 0 or "Shift" is null then 'No Shift'
          when sin(@azPQ - @azT) > 0 then 'Left'
          else 'Right'
        end
        ))))
        """.strip(),
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['Direction'] = processing.run(
            'native:fieldcalculator', alg_params,
            context=context, feedback=fb, is_child_algorithm=True
        )

        fb.setCurrentStep(17)
        alg_params = {
            'INPUT': outputs['Direction']['OUTPUT'],
            'FIELD_NAME': 'SignedShift',
            'FIELD_TYPE': 1,  # Float
            'FIELD_LENGTH': 12,
            'FIELD_PRECISION': 3,
            'FORMULA': """
        CASE
          WHEN "Direction" = 'Left'  THEN -abs("Shift")
          WHEN "Direction" = 'Right' THEN  abs("Shift")
          ELSE 0
        END
        """.strip(),
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['SignedShift'] = processing.run(
            'native:fieldcalculator', alg_params,
            context=context, feedback=fb, is_child_algorithm=True
        )


        fb.setCurrentStep(18)
        alg_params = {
            'INPUT': outputs['SignedShift']['OUTPUT'],
            'FIELDS': 'Chainage;Shift;Direction;SignedShift',  
            'OUTPUT': parameters[self.OUTPUT]
        }
        outputs['Final'] = processing.run(
            'native:retainfields', alg_params,
            context=context, feedback=fb, is_child_algorithm=True
        )

        results[self.OUTPUT] = outputs['Final']['OUTPUT']
        details = context.layerToLoadOnCompletionDetails(results[self.OUTPUT])
        details.name = "Centerline_Shifting"
        return results
        

    