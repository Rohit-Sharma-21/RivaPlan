# -*- coding: utf-8 -*-
# RivaPlan: QGIS Processing tools for river planform morphology analysis
# Copyright (C) 2026 Rohit Sharma
#



import math
import time
from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterDefinition,
    QgsProcessingException,
    QgsProcessingMultiStepFeedback,
    QgsProcessingParameterCrs,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterNumber,
    QgsProcessingUtils,
    QgsUnitTypes,
    QgsWkbTypes,
    QgsProcessingFeedback,
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
        return self.tr("""<h3>Local Curvature</h3>
<p>Calculates local centerline curvature at regular sampling locations.</p>
<h4>Parameters</h4><ul>
<li><b>Centerline:</b> River centerline used for curvature analysis.</li>
<li><b>Sampling interval:</b> Distance between sample points (m).</li>
<li><b>Method:</b> Angle gradient, three-point circle, or both methods.</li>
</ul><h4>Advanced Parameters</h4><ul>
<li><b>Working CRS:</b> Optional projected CRS in metres; defaults to the centerline CRS.</li>
</ul><h4>Output</h4><p>Sample points with curvature, radius and turn-direction attributes.</p>
<p>For detailed documentation, see the <a href="https://github.com/Rohit-Sharma-21/RivaPlan">RivaPlan GitHub page</a>.</p>""")

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
                defaultValue=None,
                optional=True,
            )
        )

        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                self.tr("OUTPUT (Curvature points)"),
                QgsProcessing.TypeVectorPoint,
            )
        )

        # Optional settings appear under the collapsible Advanced Parameters section.
        parameter = self.parameterDefinition(self.WORKING_CRS)
        parameter.setFlags(parameter.flags() | QgsProcessingParameterDefinition.FlagAdvanced)

    def _run(self, alg_id, params, context, feedback):
        return processing.run(alg_id, params, context=context, feedback=feedback, is_child_algorithm=True)

    def processAlgorithm(self, parameters, context, feedback):
        started_at = time.perf_counter()
        dist_m = float(self.parameterAsDouble(parameters, self.DIST_M, context))
        mode = int(self.parameterAsEnum(parameters, self.MODE, context))
        input_layer = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        if input_layer is None or not input_layer.isValid():
            raise QgsProcessingException("Select a valid input centerline layer.")
        if not input_layer.crs().isValid():
            raise QgsProcessingException("The input centerline has no valid CRS.")
        specified_crs = parameters.get(self.WORKING_CRS)
        if specified_crs is None or not str(specified_crs).strip():
            working_crs = input_layer.crs()
        else:
            working_crs = self.parameterAsCrs(parameters, self.WORKING_CRS, context)
        if (not working_crs.isValid() or working_crs.isGeographic()
                or working_crs.mapUnits() != QgsUnitTypes.DistanceMeters):
            raise QgsProcessingException(
                "Working CRS must be a projected coordinate system with metre units.")
        if not math.isfinite(dist_m) or dist_m <= 0:
            raise QgsProcessingException("Sampling interval must be positive and finite.")
        if mode not in (0, 1, 2):
            raise QgsProcessingException("Invalid curvature method selected.")
        if input_layer.featureCount() != 1:
            raise QgsProcessingException(
                "Input must contain exactly one continuous centerline feature (found {}).".format(
                    input_layer.featureCount()))
        feature = next(input_layer.getFeatures(), None)
        if feature is None or not feature.hasGeometry() or feature.geometry().isEmpty():
            raise QgsProcessingException("Input centerline geometry is empty.")
        geometry = feature.geometry()
        if geometry.type() != QgsWkbTypes.LineGeometry or geometry.length() <= 0 or not geometry.isGeosValid():
            raise QgsProcessingException("Input must be a nonzero, GEOS-valid centerline.")
        if geometry.isMultipart() and len(geometry.asMultiPolyline()) != 1:
            raise QgsProcessingException("Input centerline has disconnected multipart geometries.")

        feedback.pushInfo("RivaPlan | Local Curvature")
        feedback.pushInfo("Working CRS: {}".format(working_crs.authid() or working_crs.description()))
        feedback.pushInfo("Sampling interval: {:.3f} m".format(dist_m))
        feedback.pushInfo("Method: {}".format(("Angle Gradient", "Three-Point Circle", "Both Methods")[mode]))
        feedback.pushInfo("Input geometry validation: PASS")

        class QuietFeedback(QgsProcessingFeedback):
            def __init__(self, parent):
                super().__init__()
                self.parent = parent
            def isCanceled(self):
                return self.parent.isCanceled()
            def reportError(self, error, fatalError=False):
                self.parent.reportError(error, fatalError)
            def pushInfo(self, message):
                pass
            def pushDebugInfo(self, message):
                pass
            def pushConsoleInfo(self, message):
                pass
            def pushCommandInfo(self, message):
                pass


        # Steps:
        # 0 reproject
        # 1 dissolve
        # 2 points along geometry
        # 3 chainage field
        # 4 calc mode 1 (optional)
        # 5 calc mode 2 (optional)
        # 6 refactor fields -> output
        ms = QgsProcessingMultiStepFeedback(7, feedback)
        child_fb = QuietFeedback(ms)

        # 0) Reproject
        ms.setCurrentStep(0)
        if input_layer.crs() == working_crs:
            reproj = input_layer
        else:
            reproj = self._run(
                "native:reprojectlayer",
                {
                    "INPUT": input_layer,
                    "TARGET_CRS": working_crs,
                    "OPERATION": "",
                    "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
                },
                context, child_fb,
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
            child_fb,
        )["OUTPUT"]

        dissolved_layer = (dissolved if hasattr(dissolved, "getFeatures")
                           else QgsProcessingUtils.mapLayerFromString(dissolved, context))
        if dissolved_layer is None or dissolved_layer.featureCount() != 1:
            raise QgsProcessingException("Dissolved centerline is missing or ambiguous.")
        dissolved_feature = next(dissolved_layer.getFeatures())
        dissolved_geom = dissolved_feature.geometry()
        if dissolved_geom.isEmpty() or not dissolved_geom.isGeosValid():
            raise QgsProcessingException("Dissolved centerline geometry is invalid.")
        if dissolved_geom.isMultipart() and len(dissolved_geom.asMultiPolyline()) != 1:
            raise QgsProcessingException("Dissolved centerline is disconnected; chainage order is ambiguous.")
        centerline_length = dissolved_geom.length()
        if centerline_length <= 0:
            raise QgsProcessingException("Centerline has no measurable length in the working CRS.")
        prep_time = time.perf_counter() - started_at
        sample_started = time.perf_counter()

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
            child_fb,
        )["OUTPUT"]

        point_layer = (pts if hasattr(pts, "getFeatures")
                       else QgsProcessingUtils.mapLayerFromString(pts, context))
        if point_layer is None:
            raise QgsProcessingException("Could not load generated curvature sample points.")
        sampled_points = [f for f in point_layer.getFeatures()
                          if f.hasGeometry() and not f.geometry().isEmpty()]
        # Distinct coordinates are essential for a meaningful three-point curvature.
        coords = [(f.geometry().asPoint().x(), f.geometry().asPoint().y())
                  for f in sampled_points]
        distinct = []
        for xy in coords:
            if not any(math.hypot(xy[0]-old[0], xy[1]-old[1]) <= 1e-9 for old in distinct):
                distinct.append(xy)
        if len(distinct) < 3:
            raise QgsProcessingException(
                "Insufficient distinct curvature sample locations: {} found, at least 3 required. "
                "Centerline length {:.3f} m, sampling interval {:.3f} m. "
                "Reduce the sampling interval.".format(len(distinct), centerline_length, dist_m))
        if len(distinct) != len(coords):
            raise QgsProcessingException(
                "Coincident sample locations detected. Repair the centerline or adjust the sampling interval.")
        feedback.pushInfo("Centerline length: {:.3f} m | Sample points: {}".format(
            centerline_length, len(coords)))

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
            child_fb,
        )["OUTPUT"]

        sample_time = time.perf_counter() - sample_started
        cur = pts_chain
        mode1_started = time.perf_counter()

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
        when @ds is NULL OR @ds <= 0 then NULL
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
          with_variable('area', sqrt(max(0.0, @s*(@s-@a)*(@s-@b)*(@s-@c))),

          with_variable('cross2',
            (x(@B)-x(@A))*(y(@C)-y(@A)) - (y(@B)-y(@A))*(x(@C)-x(@A)),

          with_variable('sgn',
            case
              when @cross2 > 0 then 1
              when @cross2 < 0 then -1
              else 0
            end,

            case
              when @a <= 1e-12 OR @b <= 1e-12 OR @c <= 1e-12 then NULL
              when @sgn = 0 then 0.0
              when abs(@cross2) <= 1e-12 * @a * @c then 0.0
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
                child_fb,
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
                child_fb,
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
                child_fb,
            )["OUTPUT"]

        mode1_time = time.perf_counter() - mode1_started if mode in (0, 2) else 0.0
        mode2_started = time.perf_counter()

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
                child_fb,
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
                child_fb,
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
                child_fb,
            )["OUTPUT"]

        mode2_time = time.perf_counter() - mode2_started if mode in (1, 2) else 0.0
        output_started = time.perf_counter()

        # Concise QC summary using the computed values, not the final output fields.
        calculated_layer = (cur if hasattr(cur, "getFeatures")
                            else QgsProcessingUtils.mapLayerFromString(cur, context))
        counts = {"endpoints": 0, "straight": 0, "valid": 0, "undefined": 0}
        if calculated_layer is None:
            raise QgsProcessingException("Could not load calculated curvature points.")
        max_sample_distance = max(float(f["distance"]) for f in sampled_points)
        for f in calculated_layer.getFeatures():
            k_fields = (["k1"] if mode == 0 else ["k2"] if mode == 1 else ["k1", "k2"])
            vals = []
            for fld in k_fields:
                val = f[fld]
                try:
                    if val is None or str(val) == "NULL":
                        vals.append(None)
                    else:
                        val = float(val)
                        vals.append(val if math.isfinite(val) else None)
                except (TypeError, ValueError):
                    vals.append(None)
            if all(v is None for v in vals):
                if abs(float(f["distance"])) < 1e-9 or abs(float(f["distance"]) - max_sample_distance) < 1e-9:
                    counts["endpoints"] += 1
                else:
                    counts["undefined"] += 1
            elif all(v is not None and abs(v) < 1e-12 for v in vals):
                counts["straight"] += 1
            else:
                counts["valid"] += 1

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
            child_fb,
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

        output_time = time.perf_counter() - output_started
        total_time = time.perf_counter() - started_at
        feedback.pushInfo("--- RivaPlan curvature quality checks ---")
        feedback.pushInfo("Total sample points: {}".format(len(sampled_points)))
        feedback.pushInfo("Endpoint samples without curvature: {}".format(counts["endpoints"]))
        feedback.pushInfo("Straight / near-zero curvature samples: {}".format(counts["straight"]))
        feedback.pushInfo("Other calculated samples: {}".format(counts["valid"]))
        feedback.pushInfo("Uncomputable internal samples: {}".format(counts["undefined"]))
        if counts["undefined"]:
            feedback.pushWarning("{} internal samples have undefined curvature; review geometry.".format(counts["undefined"]))
        feedback.pushInfo("--- RivaPlan curvature processing ---")
        feedback.pushInfo("Input preparation: {:.3f} s".format(prep_time))
        feedback.pushInfo("Sample-point generation: {:.3f} s".format(sample_time))
        if mode in (0, 2):
            feedback.pushInfo("Angle-gradient calculations: {:.3f} s".format(mode1_time))
        if mode in (1, 2):
            feedback.pushInfo("Three-point calculations: {:.3f} s".format(mode2_time))
        feedback.pushInfo("Output and QC reporting: {:.3f} s".format(output_time))
        feedback.pushInfo("Total curvature processing: {:.3f} s".format(total_time))
        feedback.pushInfo("Curvature calculation completed successfully.")
        return results

