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
    QgsProcessingParameterCrs,
    QgsProcessingParameterNumber,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFeatureSink,
    QgsProcessingUtils,
    QgsProcessingMultiStepFeedback,
    QgsProcessingFeedback,

    # NEW (needed for final filtering step)
    QgsFeature,
    QgsSpatialIndex,
    QgsWkbTypes,
)
from qgis import processing


class QuietFeedback(QgsProcessingFeedback):
    """Pass progress/cancel through but suppress console/info spam from child algorithms."""
    def __init__(self, parent_feedback):
        super().__init__()
        self._fb = parent_feedback

    def isCanceled(self):
        return self._fb.isCanceled()

    def setProgress(self, progress):
        self._fb.setProgress(progress)

    def setProgressText(self, text):
        self._fb.setProgressText(text)

    def reportError(self, msg, fatalError=False):
        self._fb.reportError(msg, fatalError)

    def pushInfo(self, msg):          # silence
        pass

    def pushCommandInfo(self, msg):   # silence
        pass

    def pushDebugInfo(self, msg):     # silence
        pass

    def pushConsoleInfo(self, msg):   # silence
        pass


class WidthOfRiver(QgsProcessingAlgorithm):
    LEFTBANK = "LEFTBANK"
    RIGHTBANK = "RIGHTBANK"
    CENTERLINE = "CENTERLINE"
    CHAINAGE_INTERVAL = "CHAINAGE_INTERVAL"
    OUTPUT_CRS = "CRS"
    OUTPUT = "OUTPUT"

    def tr(self, string):
        return QCoreApplication.translate("Processing", string)

    def createInstance(self):
        return WidthOfRiver()

    def name(self):
        return "width_of_river"

    def displayName(self):
        return self.tr("River Width")

    def group(self):
        return self.tr("")

    def groupId(self):
        return ""

    def shortHelpString(self):
        return self.tr("Calculates river width at a defined chainage interval.")

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.LEFTBANK, self.tr("Input Left Bank Layer"), [QgsProcessing.TypeVectorLine]
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.RIGHTBANK, self.tr("Input Right Bank Layer"), [QgsProcessing.TypeVectorLine]
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.CENTERLINE, self.tr("Input Centerline Layer"), [QgsProcessing.TypeVectorLine]
            )
        )
        self.addParameter(
            QgsProcessingParameterCrs(
                self.OUTPUT_CRS, self.tr("CRS"), defaultValue="EPSG:32644"
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.CHAINAGE_INTERVAL,
                self.tr("Chainage Interval (m)"),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=50,
                minValue=1,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                self.tr("Widthlines"),
                type=QgsProcessing.TypeVectorAnyGeometry,
                createByDefault=True,
                supportsAppend=True,
            )
        )

    def _run(self, alg_id, params, context, feedback):
        return processing.run(
            alg_id,
            params,
            context=context,
            feedback=feedback,
            is_child_algorithm=True,
        )

    def processAlgorithm(self, parameters, context, feedback):
        # Inputs
        left_bank = self.parameterAsVectorLayer(parameters, self.LEFTBANK, context)
        right_bank = self.parameterAsVectorLayer(parameters, self.RIGHTBANK, context)
        centerline = self.parameterAsVectorLayer(parameters, self.CENTERLINE, context)
        chainage = self.parameterAsInt(parameters, self.CHAINAGE_INTERVAL, context)
        out_crs = parameters[self.OUTPUT_CRS]

        # Smooth progress: define steps once
        n_steps = 13  # CHANGED (added one final filtering step)
        fb = QgsProcessingMultiStepFeedback(n_steps, feedback)
        qf = QuietFeedback(fb)  # silence child spam

        def step(i, text):
            fb.setCurrentStep(i)
            fb.setProgressText(text)
            if fb.isCanceled():
                return True
            return False

        # 1) Merge bank lines
        if step(0, "Merging bankline layers…"):
            return {}
        merged_banks = self._run(
            "native:mergevectorlayers",
            {"LAYERS": [left_bank, right_bank], "CRS": out_crs, "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT},
            context, qf
        )["OUTPUT"]

        # 2) Extent polygon
        if step(1, "Computing merged banklines extent…"):
            return {}
        banks_extent = self._run(
            "native:polygonfromlayerextent",
            {"INPUT": merged_banks, "ROUND_TO": 0, "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT},
            context, qf
        )["OUTPUT"]

        # 3) Semi-diagonal
        if step(2, "Computing semi-diagonal transect length…"):
            return {}
        banks_extent_diag = self._run(
            "native:fieldcalculator",
            {
                "INPUT": banks_extent,
                "FIELD_NAME": "DIAGONAL",
                "FIELD_TYPE": 0,
                "FIELD_LENGTH": 20,
                "FIELD_PRECISION": 3,
                "FORMULA": '0.5*(sqrt("WIDTH"^2 + "HEIGHT"^2))',
                "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT
            },
            context, qf
        )["OUTPUT"]

        diag_layer = QgsProcessingUtils.mapLayerFromString(banks_extent_diag, context)
        diag_feat = next(diag_layer.getFeatures())
        diag = float(diag_feat["DIAGONAL"])

        # 4) Points along centerline
        if step(3, "Creating chainage points along centerline…"):
            return {}
        pts = self._run(
            "native:pointsalonglines",
            {
                "INPUT": centerline,
                "DISTANCE": chainage,
                "START_OFFSET": 0,
                "END_OFFSET": 0,
                "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
            },
            context, qf
        )["OUTPUT"]

        # 4b) Refactor fields (distance -> Chainage)
        if step(4, "Preparing chainage attributes…"):
            return {}
        pts_chainage = self._run(
            "native:refactorfields",
            {
                "INPUT": pts,
                "FIELDS_MAPPING": [
                    {"name": "Chainage", "type": 0, "length": 20, "precision": 3, "expression": "\"distance\""},
                    {"name": "angle", "type": 0, "length": 20, "precision": 6, "expression": "\"angle\""},
                ],
                "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT
            },
            context, qf
        )["OUTPUT"]

        # 5) Perpendicular transects
        if step(5, "Building transects…"):
            return {}
        cross_expr = f"""
extend(
  make_line(
    $geometry,
    project($geometry, {diag}, radians("angle" - 90))
  ),
  {diag}, 0
)
""".strip()

        cross_lines = self._run(
            "native:geometrybyexpression",
            {
                "INPUT": pts_chainage,
                "EXPRESSION": cross_expr,
                "OUTPUT_GEOMETRY": 1,
                "WITH_M": False,
                "WITH_Z": False,
                "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
            },
            context, qf
        )["OUTPUT"]

        # 6) Split transects with banks
        if step(6, "Splitting transects with banklines…"):
            return {}
        split0 = self._run(
            "native:splitwithlines",
            {"INPUT": cross_lines, "LINES": merged_banks, "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT},
            context, qf
        )["OUTPUT"]

        # 7) Extract segments intersecting centerline
        if step(7, "Extracting candidate width segments…"):
            return {}
        widthlines0 = self._run(
            "native:extractbylocation",
            {
                "INPUT": split0,
                "INTERSECT": centerline,
                "PREDICATE": [0],
                "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
            },
            context, qf
        )["OUTPUT"]

        # 8) Remark
        if step(8, "Classifying segments…"):
            return {}
        remarked = self._run(
            "native:fieldcalculator",
            {
                "INPUT": widthlines0,
                "FIELD_NAME": "Remark",
                "FIELD_TYPE": 2,
                "FIELD_LENGTH": 40,
                "FIELD_PRECISION": 0,
                "FORMULA": f"""
CASE
  WHEN $length > {diag} THEN 'Width by extended banklines'
  ELSE 'Width by banklines'
END
""".strip(),
                "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
            },
            context, qf
        )["OUTPUT"]

        # 9) Extend banks
        if step(9, "Extending banklines (fallback)…"):
            return {}
        ext_banklines = self._run(
            "native:extendlines",
            {
                "INPUT": merged_banks,
                "START_DISTANCE": diag,
                "END_DISTANCE": diag,
                "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
            },
            context, qf
        )["OUTPUT"]

        # 10) Split again
        if step(10, "Re-splitting with extended banklines…"):
            return {}
        splitted_crosssection = self._run(
            "native:splitwithlines",
            {"INPUT": remarked, "LINES": ext_banklines, "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT},
            context, qf
        )["OUTPUT"]

        # 11) Extract final width segment
        if step(11, "Extracting final width segments…"):
            return {}
        widthlines = self._run(
            "native:extractbylocation",
            {
                "INPUT": splitted_crosssection,
                "INTERSECT": centerline,
                "PREDICATE": [0],
                "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
            },
            context, qf
        )["OUTPUT"]

        # 12) Width field (TEMP, before filtering)
        fb.setProgressText("Calculating Width field…")
        width_temp = self._run(
            "native:fieldcalculator",
            {
                "INPUT": widthlines,
                "FIELD_NAME": "Width",
                "FIELD_TYPE": 0,
                "FIELD_LENGTH": 20,
                "FIELD_PRECISION": 3,
                "FORMULA": "$length",
                "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,  # CHANGED (TEMP)
            },
            context, qf
        )["OUTPUT"]

        # 13) Filter: for each Chainage point, keep nearest widthline with same Chainage
        if step(12, "Filtering widthlines (nearest per chainage)…"):
            return {}

        width_layer = QgsProcessingUtils.mapLayerFromString(width_temp, context)
        pts_layer = QgsProcessingUtils.mapLayerFromString(pts_chainage, context)

        # Build candidates grouped by Chainage
        # NOTE: Chainage is numeric; use float key to avoid string mismatches
        candidates = {}
        for f in width_layer.getFeatures():
            ch = f["Chainage"] if "Chainage" in width_layer.fields().names() else None
            if ch is None:
                # If Chainage field doesn't exist on widthlines, we cannot do this filter
                fb.reportError("Filtering skipped: 'Chainage' field not found on widthlines.", fatalError=True)
                return {}
            key = float(ch)
            candidates.setdefault(key, []).append(f)

        # Prepare output sink (same fields as width_layer)
        (sink, dest_id) = self.parameterAsSink(
            parameters,
            self.OUTPUT,
            context,
            width_layer.fields(),
            QgsWkbTypes.LineString,
            width_layer.sourceCrs(),
        )

        kept_fids = set()
        for p in pts_layer.getFeatures():
            pch = p["Chainage"]
            if pch is None:
                continue
            key = float(pch)
            cand = candidates.get(key, [])
            if not cand:
                continue

            pgeom = p.geometry()
            best = None
            best_d = None

            for lf in cand:
                d = pgeom.distance(lf.geometry())  # point-to-line distance
                if best_d is None or d < best_d:
                    best_d = d
                    best = lf

            if best is None:
                continue
            if best.id() in kept_fids:
                continue

            out_f = QgsFeature(width_layer.fields())
            out_f.setGeometry(best.geometry())
            out_f.setAttributes(best.attributes())
            sink.addFeature(out_f)
            kept_fids.add(best.id())

        # Name in Layers panel (reliable way)
        details = context.layerToLoadOnCompletionDetails(dest_id)
        details.name = "Widthlines"

        fb.setProgressText("Done.")
        return {self.OUTPUT: dest_id}
