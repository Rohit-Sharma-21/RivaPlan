# -*- coding: utf-8 -*-
# RivaPlan: QGIS Processing tools for river planform morphology analysis
# Copyright (C) 2026 Rohit Sharma



import math
import time

from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterEnum,
    QgsProcessingParameterNumber,
    QgsProcessingParameterCrs,
    QgsProcessingParameterFeatureSink,
    QgsProcessingException,
    QgsProcessingUtils,
    QgsProcessingFeedback,
    QgsProcessingMultiStepFeedback,
    QgsUnitTypes,
    QgsWkbTypes,
)
from qgis import processing


class linesinuosity(QgsProcessingAlgorithm):

    INPUT = "INPUT"
    MODE = "MODE"
    WIN_KM = "WIN_KM"
    STEP_M = "STEP_M"
    OUTPUT_CRS = "CRS"
    OUTPUT = "OUTPUT"

    def tr(self, string):
        return QCoreApplication.translate("Processing", string)

    def createInstance(self):
        return linesinuosity()

    def name(self):
        return "line_sinuosity_windows"

    def displayName(self):
        return self.tr("Line sinuosity")

    def group(self):
        return self.tr(self.groupId())

    def groupId(self):
        return ""

    def shortHelpString(self):
        return self.tr("""<h3>Line Sinuosity</h3>
<p>Calculates centerline sinuosity over fixed segments or sliding windows.</p>
<h4>Parameters</h4><ul>
<li><b>Input line:</b> River centerline for sinuosity analysis.</li>
<li><b>Mode:</b> Fixed segments or sliding windows.</li>
<li><b>Window length:</b> Length of each analysis window (km).</li>
<li><b>Step / shift:</b> Spacing between sliding windows (m); used only in sliding mode.</li>
</ul><h4>Advanced Parameters</h4><ul>
<li><b>Working CRS:</b> Optional projected CRS in metres; defaults to the input CRS.</li>
</ul><h4>Output</h4><p>Line windows with length, chord distance, sinuosity and QC status. Short final windows are retained.</p>
<p>For detailed documentation, see the <a href="https://github.com/Rohit-Sharma-21/RivaPlan">RivaPlan GitHub page</a>.</p>""")

    def initAlgorithm(self, config=None):

        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.INPUT,
                self.tr("Input line layer"),
                types=[QgsProcessing.TypeVectorLine],
            )
        )

        self.addParameter(
            QgsProcessingParameterEnum(
                self.MODE,
                self.tr("Mode"),
                options=["Fixed segments (X km)", "Sliding windows (X km, step Y m)"],
                defaultValue=0,
            )
        )

        self.addParameter(
            QgsProcessingParameterNumber(
                self.WIN_KM,
                self.tr("Window length (km)"),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=1.0,
                minValue=0.001,
            )
        )

        self.addParameter(
            QgsProcessingParameterNumber(
                self.STEP_M,
                self.tr("Step / shift (meters) [Mode 2 only]"),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=100.0,
                minValue=0.0,
            )
        )

        self.addParameter(
            QgsProcessingParameterCrs(
                self.OUTPUT_CRS,
                self.tr("Working CRS (optional; defaults to input centerline CRS)"),
                defaultValue=None,
                optional=True,
            )
        )

        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                self.tr("Sinuosity windows"),
                type=QgsProcessing.TypeVectorLine,
                createByDefault=True,
                supportsAppend=False,
            )
        )

        # Optional settings appear under the collapsible Advanced Parameters section.
        parameter = self.parameterDefinition(self.OUTPUT_CRS)
        parameter.setFlags(parameter.flags() | QgsProcessingParameterDefinition.FlagAdvanced)

    class QuietFeedback(QgsProcessingFeedback):
        def __init__(self, parent_fb):
            super().__init__()
            self._p = parent_fb

        def pushInfo(self, msg): pass
        def pushCommandInfo(self, msg): pass
        def pushDebugInfo(self, msg): pass
        def pushConsoleInfo(self, msg): pass
        def pushWarning(self, msg): self._p.pushWarning(msg)
        def reportError(self, msg, fatalError=False): self._p.reportError(msg, fatalError)

        def setProgress(self, progress):
            self._p.setProgress(progress)

        def isCanceled(self):
            return self._p.isCanceled()

    def _run(self, alg_id, params, context, feedback):
        return processing.run(
            alg_id,
            params,
            context=context,
            feedback=feedback,
            is_child_algorithm=True,
        )

    def processAlgorithm(self, parameters, context, feedback):
        started_at = time.perf_counter()
        inp = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        mode = self.parameterAsEnum(parameters, self.MODE, context)
        win_km = self.parameterAsDouble(parameters, self.WIN_KM, context)
        step_m = self.parameterAsDouble(parameters, self.STEP_M, context)

        if inp is None or not inp.isValid():
            raise QgsProcessingException("Select a valid input centerline layer.")
        if mode not in (0, 1):
            raise QgsProcessingException("Unknown sinuosity calculation mode.")
        if not inp.crs().isValid():
            raise QgsProcessingException("The input centerline has no valid CRS.")

        specified_crs = parameters.get(self.OUTPUT_CRS)
        if specified_crs is not None and str(specified_crs).strip():
            out_crs = self.parameterAsCrs(parameters, self.OUTPUT_CRS, context)
        else:
            out_crs = inp.crs()
        if (not out_crs.isValid() or out_crs.isGeographic()
                or out_crs.mapUnits() != QgsUnitTypes.DistanceMeters):
            raise QgsProcessingException(
                "Working CRS must be a valid projected CRS with metre units. "
                "Choose one explicitly if the input centerline CRS is unsuitable.")
        if not math.isfinite(win_km) or win_km <= 0:
            raise QgsProcessingException("Window length must be a positive finite number of km.")
        win_m = float(win_km) * 1000.0
        if not math.isfinite(win_m) or win_m <= 0:
            raise QgsProcessingException("Window length in metres is invalid.")
        if mode == 1 and (not math.isfinite(step_m) or step_m <= 0):
            raise QgsProcessingException("Sliding-window step must be positive and finite.")

        input_features = 0
        for feature in inp.getFeatures():
            input_features += 1
            if not feature.hasGeometry() or feature.geometry().isEmpty():
                raise QgsProcessingException(
                    "Centerline feature {} has empty geometry.".format(feature.id()))
            geometry = feature.geometry()
            if geometry.length() <= 0 or not geometry.isGeosValid():
                raise QgsProcessingException(
                    "Centerline feature {} has zero-length or invalid geometry.".format(feature.id()))
            if QgsWkbTypes.geometryType(geometry.wkbType()) != QgsWkbTypes.LineGeometry:
                raise QgsProcessingException("Input must contain line geometries only.")
        if input_features == 0:
            raise QgsProcessingException("The input centerline has no features.")

        feedback.pushInfo("RivaPlan | Line sinuosity")
        feedback.pushInfo("Working CRS: {} | Mode: {}".format(
            out_crs.authid() or out_crs.description(),
            "Fixed windows" if mode == 0 else "Sliding windows"))
        feedback.pushInfo("Window length: {:.3f} m".format(win_m))
        if mode == 1:
            feedback.pushInfo("Sliding step: {:.3f} m".format(step_m))
            if step_m > win_m:
                feedback.pushWarning("Sliding step exceeds window length; gaps occur between windows.")
        feedback.pushInfo("Input geometry validation: PASS")

        # Steps: reproject, dissolve, totlen, build, merge, refactor, finish
        ms = QgsProcessingMultiStepFeedback(7, feedback)
        child_fb = self.QuietFeedback(ms)

        # 1) Reproject only when needed
        ms.setCurrentStep(0)
        if inp.crs() == out_crs:
            reproj = inp
        else:
            reproj = self._run(
                "native:reprojectlayer",
                {"INPUT": inp, "TARGET_CRS": out_crs, "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT},
                context, child_fb
            )["OUTPUT"]

        # 2) Dissolve
        ms.setCurrentStep(1)
        dissolved = self._run(
            "native:dissolve",
            {"INPUT": reproj, "FIELD": [], "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT},
            context, child_fb
        )["OUTPUT"]

        # 3) Total length scalar
        ms.setCurrentStep(2)
        length_layer_id = self._run(
            "native:fieldcalculator",
            {
                "INPUT": dissolved,
                "FIELD_NAME": "TOTLEN",
                "FIELD_TYPE": 0,
                "FIELD_LENGTH": 20,
                "FIELD_PRECISION": 3,
                "FORMULA": "length($geometry)",
                "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
            },
            context, child_fb
        )["OUTPUT"]

        ll = QgsProcessingUtils.mapLayerFromString(length_layer_id, context)
        feat = next(ll.getFeatures(), None)
        if feat is None:
            raise QgsProcessingException("No features found after dissolve.")
        total_len = float(feat["TOTLEN"])
        if not math.isfinite(total_len) or total_len <= 0:
            raise QgsProcessingException("Input line has zero or invalid length after dissolve.")
        dissolved_geom = feat.geometry()
        if dissolved_geom.isEmpty() or not dissolved_geom.isGeosValid():
            raise QgsProcessingException("Dissolved centerline geometry is invalid.")
        if dissolved_geom.isMultipart() and len(dissolved_geom.asMultiPolyline()) != 1:
            raise QgsProcessingException(
                "Dissolving produced disconnected line parts. Provide one continuous centerline "
                "so that chainage and window substrings have an unambiguous order.")
        if total_len < win_m:
            feedback.pushWarning(
                "Centerline ({:.3f} m) is shorter than the requested window "
                "({:.3f} m). One incomplete window will be calculated.".format(total_len, win_m))
        prep_seconds = time.perf_counter() - started_at

        # 4) Build start positions (Mode 1 vs Mode 2)
        ms.setCurrentStep(3)

        starts = []

        if mode == 0:
            # Fixed segments: 0, win, 2win ... last partial allowed
            s = 0.0
            while s < total_len:
                starts.append(s)
                s += win_m
            layer_name = "Sinuosity_fixed_segments"

        else:
            # Sliding windows at the existing regular starts. Keep all full windows
            # and ONE final incomplete window at the next regular start, if needed.
            s = 0.0
            while (s + win_m) <= total_len + 1e-8:
                starts.append(s)
                s += step_m
            if s < total_len - 1e-8:
                starts.append(s)
            if not starts:  # shorter than one window
                starts.append(0.0)
            layer_name = "Sinuosity_sliding_windows"

        n = len(starts)
        if n == 0:
            raise QgsProcessingException("No windows could be generated (check length/step).")

        window_layers = []
        n_complete = 0
        n_incomplete = 0
        n_zero_chord = 0
        n_invalid_window = 0
        warnings = []
        window_started = time.perf_counter()

        for i, start_m in enumerate(starts):
            if ms.isCanceled():
                return {}

            end_m = min(start_m + win_m, total_len)
            incomplete = (end_m - start_m) < win_m - 1e-7

            sub = self._run(
                "native:linesubstring",
                {
                    "INPUT": dissolved,
                    "START_DISTANCE": float(start_m),
                    "END_DISTANCE": float(end_m),
                    "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
                },
                context, child_fb
            )["OUTPUT"]

            with_from = self._run(
                "native:fieldcalculator",
                {
                    "INPUT": sub,
                    "FIELD_NAME": "From_m",
                    "FIELD_TYPE": 1,
                    "FIELD_LENGTH": 10,
                    "FIELD_PRECISION": 0,
                    "FORMULA": f"round({float(start_m)})",
                    "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
                },
                context, child_fb
            )["OUTPUT"]

            with_to = self._run(
                "native:fieldcalculator",
                {
                    "INPUT": with_from,
                    "FIELD_NAME": "To_m",
                    "FIELD_TYPE": 1,
                    "FIELD_LENGTH": 10,
                    "FIELD_PRECISION": 0,
                    "FORMULA": f"round({float(end_m)})",
                    "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
                },
                context, child_fb
            )["OUTPUT"]

            # Range WITHOUT commas (avoid locale formatting issues)
            with_range = self._run(
                "native:fieldcalculator",
                {
                    "INPUT": with_to,
                    "FIELD_NAME": "Range",
                    "FIELD_TYPE": 2,
                    "FIELD_LENGTH": 40,
                    "FIELD_PRECISION": 0,
                    "FORMULA": "concat(to_string(\"From_m\"),'-',to_string(\"To_m\"))",
                    "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
                },
                context, child_fb
            )["OUTPUT"]

            with_len = self._run(
                "native:fieldcalculator",
                {
                    "INPUT": with_range,
                    "FIELD_NAME": "Len_m",
                    "FIELD_TYPE": 0,
                    "FIELD_LENGTH": 20,
                    "FIELD_PRECISION": 1,
                    "FORMULA": "round(length($geometry), 1)",
                    "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
                },
                context, child_fb
            )["OUTPUT"]

            with_chord = self._run(
                "native:fieldcalculator",
                {
                    "INPUT": with_len,
                    "FIELD_NAME": "Chord_m",
                    "FIELD_TYPE": 0,
                    "FIELD_LENGTH": 20,
                    "FIELD_PRECISION": 1,
                    "FORMULA": "round(distance(start_point($geometry), end_point($geometry)), 1)",
                    "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
                },
                context, child_fb
            )["OUTPUT"]

            with_sin = self._run(
                "native:fieldcalculator",
                {
                    "INPUT": with_chord,
                    "FIELD_NAME": "Sinuosity",
                    "FIELD_TYPE": 0,
                    "FIELD_LENGTH": 20,
                    "FIELD_PRECISION": 3,
                    "FORMULA": "CASE WHEN \"Chord_m\" > 0 THEN round(\"Len_m\" / \"Chord_m\", 3) ELSE NULL END",
                    "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
                },
                context, child_fb
            )["OUTPUT"]

            # Preserve all original fields and append a QC field. Status is
            # based on actual geometry (before 0.1 m attribute rounding).
            state_expr = (
                "CASE "
                "WHEN $geometry IS NULL OR is_empty($geometry) OR NOT is_valid($geometry) "
                "THEN 'INVALID_GEOMETRY' "
                "WHEN \"Chord_m\" <= 0 OR distance(start_point($geometry), end_point($geometry)) <= 1e-9 "
                "THEN 'ZERO_CHORD' "
                "WHEN {} THEN 'INCOMPLETE' "
                "ELSE 'COMPLETE' END".format('TRUE' if incomplete else 'FALSE')
            )
            with_qc = self._run(
                "native:fieldcalculator",
                {
                    "INPUT": with_sin,
                    "FIELD_NAME": "QC_Status",
                    "FIELD_TYPE": 2,
                    "FIELD_LENGTH": 24,
                    "FIELD_PRECISION": 0,
                    "FORMULA": state_expr,
                    "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
                },
                context, child_fb
            )["OUTPUT"]
            qc_layer = QgsProcessingUtils.mapLayerFromString(with_qc, context)
            qc_feature = next(qc_layer.getFeatures(), None) if qc_layer else None
            if qc_feature is None:
                raise QgsProcessingException(
                    "Window at {:.3f} m could not be extracted.".format(start_m))
            status = str(qc_feature["QC_Status"])
            if status == "COMPLETE":
                n_complete += 1
            elif status == "INCOMPLETE":
                n_incomplete += 1
            elif status == "ZERO_CHORD":
                n_zero_chord += 1
                warnings.append("Zero chord at window {:.3f}-{:.3f} m; sinuosity is NULL.".format(start_m, end_m))
            else:
                n_invalid_window += 1
                warnings.append("Invalid extracted geometry at {:.3f}-{:.3f} m.".format(start_m, end_m))
            window_layers.append(with_qc)

            # smooth loop progress
            ms.setProgress(40 + int(40 * (i + 1) / n))

        window_seconds = time.perf_counter() - window_started
        finish_started = time.perf_counter()
        # 5) Merge
        ms.setCurrentStep(4)
        merged = self._run(
            "native:mergevectorlayers",
            {"LAYERS": window_layers, "CRS": out_crs, "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT},
            context, child_fb
        )["OUTPUT"]

        # 6) Refactor fields (types + precision exactly as requested)
        ms.setCurrentStep(5)
        cleaned = self._run(
            "native:refactorfields",
            {
                "INPUT": merged,
                "FIELDS_MAPPING": [
                    {"name": "id",        "type": QVariant.Int,    "length": 10, "precision": 0, "expression": "$id"},
                    {"name": "From_m",    "type": QVariant.Int,    "length": 10, "precision": 0, "expression": "to_int(\"From_m\")"},
                    {"name": "To_m",      "type": QVariant.Int,    "length": 10, "precision": 0, "expression": "to_int(\"To_m\")"},
                    {"name": "Range",     "type": QVariant.String, "length": 40, "precision": 0, "expression": "\"Range\""},
                    {"name": "Len_m",     "type": QVariant.Double, "length": 20, "precision": 1, "expression": "round(\"Len_m\", 1)"},
                    {"name": "Chord_m",   "type": QVariant.Double, "length": 20, "precision": 1, "expression": "round(\"Chord_m\", 1)"},
                    {"name": "Sinuosity", "type": QVariant.Double, "length": 20, "precision": 3, "expression": "CASE WHEN \"QC_Status\" IN ('ZERO_CHORD','INVALID_GEOMETRY') THEN NULL ELSE round(\"Sinuosity\", 3) END"},
                    {"name": "QC_Status", "type": QVariant.String, "length": 24, "precision": 0, "expression": "\"QC_Status\""},
                ],
                "OUTPUT": parameters[self.OUTPUT],
            },
            context, child_fb
        )["OUTPUT"]

        # 7) Rename output layer when loaded
        ms.setCurrentStep(6)
        details = context.layerToLoadOnCompletionDetails(cleaned)
        details.name = layer_name
        finish_seconds = time.perf_counter() - finish_started
        total_seconds = time.perf_counter() - started_at
        feedback.pushInfo("--- RivaPlan sinuosity quality checks ---")
        feedback.pushInfo("Centerline length: {:.3f} m".format(total_len))
        feedback.pushInfo("Windows generated: {}".format(len(window_layers)))
        feedback.pushInfo("Complete windows: {}".format(n_complete))
        feedback.pushInfo("Incomplete windows: {}".format(n_incomplete))
        feedback.pushInfo("Zero-chord windows: {}".format(n_zero_chord))
        feedback.pushInfo("Invalid window geometries: {}".format(n_invalid_window))
        for message in warnings:
            feedback.pushWarning(message)
        feedback.pushInfo("--- RivaPlan sinuosity processing ---")
        feedback.pushInfo("1. Input preparation: {:.3f} s".format(prep_seconds))
        feedback.pushInfo("2. Window generation and calculations: {:.3f} s".format(window_seconds))
        feedback.pushInfo("3. Output assembly: {:.3f} s".format(finish_seconds))
        feedback.pushInfo("Total sinuosity processing: {:.3f} s".format(total_seconds))
        feedback.pushInfo("Sinuosity calculation completed successfully.")
        return {self.OUTPUT: cleaned}
