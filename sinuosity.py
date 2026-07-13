# -*- coding: utf-8 -*-
# RivaPlan: QGIS Processing tools for river planform morphology analysis
# Copyright (C) 2026 Rohit Sharma
#
# All rights reserved for this reviewer-access script archive.
# This file is provided for editorial and peer-review evaluation only.
#
# The public release of RivaPlan is planned as a QGIS Processing plugin
# under a GPL-compatible open-source license after article acceptance/publication.


from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterEnum,
    QgsProcessingParameterNumber,
    QgsProcessingParameterCrs,
    QgsProcessingParameterFeatureSink,
    QgsProcessingException,
    QgsProcessingUtils,
    QgsProcessingFeedback,
    QgsProcessingMultiStepFeedback,
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
        return self.tr(
            "Mode 1: fixed windows of X km from 0.\n"
            "Mode 2: sliding windows of X km shifted by Y m.\n"
            "Computes Len_m, Chord_m, and Sinuosity for each window."
        )

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
                self.tr("Working CRS (projected)"),
                defaultValue="EPSG:32644",
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

    class QuietFeedback(QgsProcessingFeedback):
        def __init__(self, parent_fb):
            super().__init__()
            self._p = parent_fb

        def pushInfo(self, msg): pass
        def pushCommandInfo(self, msg): pass
        def pushDebugInfo(self, msg): pass
        def pushConsoleInfo(self, msg): pass
        def pushWarning(self, msg): pass
        def reportError(self, msg, fatalError=False): pass

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

        inp = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        mode = self.parameterAsEnum(parameters, self.MODE, context)
        win_km = self.parameterAsDouble(parameters, self.WIN_KM, context)
        step_m = self.parameterAsDouble(parameters, self.STEP_M, context)
        out_crs = parameters[self.OUTPUT_CRS]

        win_m = float(win_km) * 1000.0
        if win_m <= 0:
            raise QgsProcessingException("Window length must be > 0.")

        # Steps: reproject, dissolve, totlen, build, merge, refactor, finish
        ms = QgsProcessingMultiStepFeedback(7, feedback)
        child_fb = self.QuietFeedback(ms)

        # 1) Reproject
        ms.setCurrentStep(0)
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
                "FORMULA": "$length",
                "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
            },
            context, child_fb
        )["OUTPUT"]

        ll = QgsProcessingUtils.mapLayerFromString(length_layer_id, context)
        feat = next(ll.getFeatures(), None)
        if feat is None:
            raise QgsProcessingException("No features found after dissolve.")
        total_len = float(feat["TOTLEN"])
        if total_len <= 0:
            raise QgsProcessingException("Input line has zero length after dissolve.")

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
            # Sliding windows: 0, step, 2step ... ONLY full windows (no end overflow)
            if step_m <= 0:
                raise QgsProcessingException("Step (meters) must be > 0 for Mode 2.")

            s = 0.0
            while (s + win_m) <= total_len:
                starts.append(s)
                s += step_m
            layer_name = "Sinuosity_sliding_windows"

        n = len(starts)
        if n == 0:
            raise QgsProcessingException("No windows could be generated (check length/step).")

        window_layers = []

        for i, start_m in enumerate(starts):
            if ms.isCanceled():
                break

            end_m = min(start_m + win_m, total_len)  # mode1 may be partial; mode2 never exceeds

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
                    "FORMULA": "round($length, 1)",
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

            window_layers.append(with_sin)

            # smooth loop progress
            ms.setProgress(40 + int(40 * (i + 1) / n))

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
                    {"name": "Sinuosity", "type": QVariant.Double, "length": 20, "precision": 3, "expression": "round(\"Sinuosity\", 3)"},
                ],
                "OUTPUT": parameters[self.OUTPUT],
            },
            context, child_fb
        )["OUTPUT"]

        # 7) Rename output layer when loaded
        ms.setCurrentStep(6)
        details = context.layerToLoadOnCompletionDetails(cleaned)
        details.name = layer_name

        return {self.OUTPUT: cleaned}
