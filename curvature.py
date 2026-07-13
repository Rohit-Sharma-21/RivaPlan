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
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingMultiStepFeedback,
    QgsProcessingParameterCrs,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterNumber,
    QgsCoordinateReferenceSystem,
)
import processing


class LocalCurvatureSimple(QgsProcessingAlgorithm):
    """
    Simple curvature tool:
      - Common preprocess: Reproject -> Dissolve -> Points along geometry
      - Then Field Calculator expressions 

    Modes:
      0 = Mode 1 (Angle gradient): kappa magnitude from angles, SIGN from geometry (same as 3-point)
      1 = Mode 2 (3-point circle): signed kappa from A,B,C circumcircle
      2 = Both: compute both kappas + radii
    """

    INPUT = "INPUT"
    DIST_M = "DIST_M"
    MODE = "MODE"
    WORKING_CRS = "WORKING_CRS"
    OUTPUT = "OUTPUT"

    def tr(self, s):
        return QCoreApplication.translate("LocalCurvatureSimple", s)

    def createInstance(self):
        return LocalCurvatureSimple()

    def name(self):
        return "local_curvature_simple"

    def displayName(self):
        return self.tr("Local Curvature")

    def group(self):
        return self.tr("")

    def groupId(self):
        return ""

    def shortHelpString(self):
        return self.tr(
            "Inputs: centerline, sampling interval DIST_M, method.\n"
            "Workflow: reproject -> dissolve -> points along geometry.\n"
            "Then computes curvature using QGIS field expressions based on points' "
            "\"distance\" and \"angle\" fields.\n"
            "Mode 1 sign is aligned to Mode 2 using triangle orientation.\n"
            
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT,
                self.tr("INPUT (river centerline)"),
                [QgsProcessing.TypeVectorLine],
            )
        )

        self.addParameter(
            QgsProcessingParameterNumber(
                self.DIST_M,
                self.tr("DIST_M (sampling interval, meters)"),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=50.0,
                minValue=0.0001,
            )
        )

        self.addParameter(
            QgsProcessingParameterEnum(
                self.MODE,
                self.tr("MODE"),
                options=[
                    self.tr("Mode 1: Angle gradient (dtheta/ds)"),
                    self.tr("Mode 2: 3-point circle (circumcircle)"),
                    self.tr("Both (compute both methods)"),
                ],
                defaultValue=0,
            )
        )

        self.addParameter(
            QgsProcessingParameterCrs(
                self.WORKING_CRS,
                self.tr("WORKING_CRS (projected, meters)"),
                defaultValue=QgsCoordinateReferenceSystem("EPSG:32644"),
            )
        )

        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                self.tr("OUTPUT (Curvature points)"),
                QgsProcessing.TypeVectorPoint,
            )
        )

    def _run(self, alg_id, params, context, feedback):
        return processing.run(alg_id, params, context=context, feedback=feedback, is_child_algorithm=True)

    def processAlgorithm(self, parameters, context, feedback):
        dist_m = float(self.parameterAsDouble(parameters, self.DIST_M, context))
        mode = int(self.parameterAsEnum(parameters, self.MODE, context))
        working_crs = self.parameterAsCrs(parameters, self.WORKING_CRS, context)

        if dist_m <= 0:
            raise QgsProcessingException("DIST_M must be > 0")

        # Steps:
        # 0 reproject
        # 1 dissolve
        # 2 points along geometry
        # 3 chainage field
        # 4 calc mode 1 (optional)
        # 5 calc mode 2 (optional)
        # 6 refactor fields -> output
        ms = QgsProcessingMultiStepFeedback(7, feedback)

        # 0) Reproject
        ms.setCurrentStep(0)
        reproj = self._run(
            "native:reprojectlayer",
            {
                "INPUT": parameters[self.INPUT],
                "TARGET_CRS": working_crs,
                "OPERATION": "",
                "OUTPUT": "memory:",
            },
            context,
            ms,
        )["OUTPUT"]

        # 1) Dissolve
        ms.setCurrentStep(1)
        dissolved = self._run(
            "native:dissolve",
            {
                "INPUT": reproj,
                "FIELD": [],
                "SEPARATE_DISJOINT": False,
                "OUTPUT": "memory:",
            },
            context,
            ms,
        )["OUTPUT"]

        # 2) Points along geometry
        # NOTE: must create fields named "distance" and "angle"
        ms.setCurrentStep(2)
        pts = self._run(
            "native:pointsalonglines",
            {
                "INPUT": dissolved,
                "DISTANCE": dist_m,
                "START_OFFSET": 0.0,
                "END_OFFSET": 0.0,
                "OUTPUT": "memory:",
            },
            context,
            ms,
        )["OUTPUT"]

        # 3) Chainage (integer) from "distance"
        ms.setCurrentStep(3)
        pts_chain = self._run(
            "native:fieldcalculator",
            {
                "INPUT": pts,
                "FIELD_NAME": "chain_m",
                "FIELD_TYPE": 1,  # integer
                "FIELD_LENGTH": 12,
                "FIELD_PRECISION": 0,
                "FORMULA": 'to_int(round("distance"))',
                "OUTPUT": "memory:",
            },
            context,
            ms,
        )["OUTPUT"]

        cur = pts_chain

        # --- Mode 1: kappa magnitude from angle change, SIGN from geometry orientation (align with 3-point)
        # This fixes the "Mode1 says L, Mode2 says R" issue.
        mode1_kappa_expr = """
with_variable('d', "distance",
with_variable('min_d', aggregate(@layer,'min',"distance"),
with_variable('max_d', aggregate(@layer,'max',"distance"),
case
  when @d = @min_d OR @d = @max_d then NULL
  else
    with_variable('prev_d', aggregate(@layer,'max',"distance","distance" < @d),
    with_variable('next_d', aggregate(@layer,'min',"distance","distance" > @d),

    with_variable('prev_ang', aggregate(@layer,'max',"angle","distance" = @prev_d),
    with_variable('next_ang', aggregate(@layer,'max',"angle","distance" = @next_d),

    with_variable('A', geometry(get_feature(@layer,'distance',@prev_d)),
    with_variable('B', $geometry,
    with_variable('C', geometry(get_feature(@layer,'distance',@next_d)),

    with_variable('ds', @next_d - @prev_d,

    with_variable('cross2',
      (x(@B)-x(@A))*(y(@C)-y(@A)) - (y(@B)-y(@A))*(x(@C)-x(@A)),

    with_variable('sgn',
      case
        when @cross2 > 0 then 1
        when @cross2 < 0 then -1
        else 0
      end,

    with_variable('dtheta_deg_raw', @next_ang - @prev_ang,
    with_variable('dtheta_deg',
        ((@dtheta_deg_raw +180)%360) -180
    ,
    with_variable('dtheta_rad', radians(@dtheta_deg),
      case
        when @prev_d is NULL OR @next_d is NULL then NULL
        when @prev_ang is NULL OR @next_ang is NULL then NULL
        when @ds = 0 then NULL
        when @sgn = 0 then 0.0
        else @sgn * (abs(@dtheta_rad) / @ds)
      end
    ))

    )))))))))))
end
)))
"""

        # --- Mode 2: signed 3-point kappa (circumcircle)
        mode2_kappa_expr = """
with_variable('d', "distance",
with_variable('min_d', aggregate(@layer,'min',"distance"),
with_variable('max_d', aggregate(@layer,'max',"distance"),
case
  when @d = @min_d OR @d = @max_d then NULL
  else
    with_variable('prev_d', aggregate(@layer,'max',"distance","distance" < @d),
    with_variable('next_d', aggregate(@layer,'min',"distance","distance" > @d),
      case
        when @prev_d IS NULL OR @next_d IS NULL then NULL
        else
          with_variable('A', geometry(get_feature(@layer,'distance',@prev_d)),
          with_variable('B', $geometry,
          with_variable('C', geometry(get_feature(@layer,'distance',@next_d)),

          with_variable('c', distance(@A,@B),
          with_variable('a', distance(@B,@C),
          with_variable('b', distance(@A,@C),

          with_variable('s', (@a+@b+@c)/2,
          with_variable('area', sqrt(@s*(@s-@a)*(@s-@b)*(@s-@c)),

          with_variable('cross2',
            (x(@B)-x(@A))*(y(@C)-y(@A)) - (y(@B)-y(@A))*(x(@C)-x(@A)),

          with_variable('sgn',
            case
              when @cross2 > 0 then 1
              when @cross2 < 0 then -1
              else 0
            end,

            case
              when @a = 0 OR @b = 0 OR @c = 0 then NULL
              when @sgn = 0 then 0.0
              when @area <= 1e-12 then 0.0
              else @sgn * (4*@area)/(@a*@b*@c)
            end
          ))))))))))
      end
    ))
end
)))
"""

        def radius_expr(kappa_field):
            return f"""
CASE
  WHEN "{kappa_field}" IS NULL THEN NULL
  WHEN abs("{kappa_field}") < 1e-15 THEN NULL
  ELSE 1.0 / abs("{kappa_field}")
END
"""

        def turn_expr(kappa_field):
            return f"""
CASE
  WHEN "{kappa_field}" IS NULL THEN NULL
  WHEN abs("{kappa_field}") < 1e-12 THEN 'S'
  WHEN "{kappa_field}" > 0 THEN 'L'
  ELSE 'R'
END
"""

        # 4) Compute Mode 1 fields if needed
        if mode in (0, 2):
            ms.setCurrentStep(4)

            cur = self._run(
                "native:fieldcalculator",
                {
                    "INPUT": cur,
                    "FIELD_NAME": "k1",
                    "FIELD_TYPE": 0,  # double
                    "FIELD_LENGTH": 24,
                    "FIELD_PRECISION": 12,
                    "FORMULA": mode1_kappa_expr,
                    "OUTPUT": "memory:",
                },
                context,
                ms,
            )["OUTPUT"]

            cur = self._run(
                "native:fieldcalculator",
                {
                    "INPUT": cur,
                    "FIELD_NAME": "r1",
                    "FIELD_TYPE": 0,
                    "FIELD_LENGTH": 24,
                    "FIELD_PRECISION": 6,
                    "FORMULA": radius_expr("k1"),
                    "OUTPUT": "memory:",
                },
                context,
                ms,
            )["OUTPUT"]

            cur = self._run(
                "native:fieldcalculator",
                {
                    "INPUT": cur,
                    "FIELD_NAME": "t1",
                    "FIELD_TYPE": 2,  # string
                    "FIELD_LENGTH": 1,
                    "FIELD_PRECISION": 0,
                    "FORMULA": turn_expr("k1"),
                    "OUTPUT": "memory:",
                },
                context,
                ms,
            )["OUTPUT"]

        # 5) Compute Mode 2 fields if needed
        if mode in (1, 2):
            ms.setCurrentStep(5)

            cur = self._run(
                "native:fieldcalculator",
                {
                    "INPUT": cur,
                    "FIELD_NAME": "k2",
                    "FIELD_TYPE": 0,  # double
                    "FIELD_LENGTH": 24,
                    "FIELD_PRECISION": 12,
                    "FORMULA": mode2_kappa_expr,
                    "OUTPUT": "memory:",
                },
                context,
                ms,
            )["OUTPUT"]

            cur = self._run(
                "native:fieldcalculator",
                {
                    "INPUT": cur,
                    "FIELD_NAME": "r2",
                    "FIELD_TYPE": 0,
                    "FIELD_LENGTH": 24,
                    "FIELD_PRECISION": 6,
                    "FORMULA": radius_expr("k2"),
                    "OUTPUT": "memory:",
                },
                context,
                ms,
            )["OUTPUT"]

            cur = self._run(
                "native:fieldcalculator",
                {
                    "INPUT": cur,
                    "FIELD_NAME": "t2",
                    "FIELD_TYPE": 2,
                    "FIELD_LENGTH": 1,
                    "FIELD_PRECISION": 0,
                    "FORMULA": turn_expr("k2"),
                    "OUTPUT": "memory:",
                },
                context,
                ms,
            )["OUTPUT"]

        # 6) Refactor fields to final output (more readable) + set layer name by mode
        ms.setCurrentStep(6)

        # Always keep these for readability
        fields_map = [
            {"name": "Chainage", "type": 4, "length": 12, "precision": 0, "expression": "\"chain_m\""},
            {"name": "Distance", "type": 6, "length": 20, "precision": 3, "expression": "\"distance\""},
            {"name": "Angle", "type": 6, "length": 20, "precision": 6, "expression": "\"angle\""},
        ]

        if mode == 0:
            fields_map += [
                {"name": "Kappa", "type": 6, "length": 24, "precision": 12, "expression": "\"k1\""},
                {"name": "R", "type": 6, "length": 24, "precision": 6, "expression": "\"r1\""},
                {"name": "Turn", "type": 10, "length": 1, "precision": 0, "expression": "\"t1\""},
                {"name": "Method", "type": 10, "length": 24, "precision": 0, "expression": "'AngleGradient'"},
            ]
        elif mode == 1:
            fields_map += [
                {"name": "Kappa", "type": 6, "length": 24, "precision": 12, "expression": "\"k2\""},
                {"name": "R", "type": 6, "length": 24, "precision": 6, "expression": "\"r2\""},
                {"name": "Turn", "type": 10, "length": 1, "precision": 0, "expression": "\"t2\""},
                {"name": "Method", "type": 10, "length": 24, "precision": 0, "expression": "'ThreePoint'"},
            ]
        else:  # both
            fields_map += [
                {"name": "Kappa_Angle", "type": 6, "length": 24, "precision": 12, "expression": "\"k1\""},
                {"name": "R_Angle", "type": 6, "length": 24, "precision": 6, "expression": "\"r1\""},
                {"name": "Turn_Angle", "type": 10, "length": 1, "precision": 0, "expression": "\"t1\""},
                {"name": "Kappa_3pt", "type": 6, "length": 24, "precision": 12, "expression": "\"k2\""},
                {"name": "R_3pt", "type": 6, "length": 24, "precision": 6, "expression": "\"r2\""},
                {"name": "Turn_3pt", "type": 10, "length": 1, "precision": 0, "expression": "\"t2\""},
                {"name": "Method", "type": 10, "length": 24, "precision": 0, "expression": "'Both'"},
            ]

        outputs = {}

        outputs['Final'] = self._run(
            "native:refactorfields",
            {
                "INPUT": cur,
                "FIELDS_MAPPING": fields_map,
                "OUTPUT": parameters[self.OUTPUT],
            },
            context,
            ms,
        )

        results = {}
        results[self.OUTPUT] = outputs['Final']['OUTPUT']

        layer_id = results[self.OUTPUT]
        details = context.layerToLoadOnCompletionDetails(layer_id)

        if mode == 0:
            details.name = "Angle_Gradient_Curvature"
        elif mode == 1:
            details.name = "ThreePoint_Curvature"
        else:
            details.name = "Curvature_Both_Methods"

        return results

