# -*- coding: utf-8 -*-
"""RivaPlan | Reverse Line Direction (standalone QGIS Processing script).

Reverses the vertex order of every line feature in the input layer.
Does not determine upstream/downstream direction.
"""

from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterVectorLayer,
)
import processing


class ReverseLineDirection(QgsProcessingAlgorithm):
    INPUT = 'INPUT'
    OUTPUT = 'OUTPUT'

    def tr(self, message):
        return QCoreApplication.translate('Processing', message)

    def name(self):
        return 'rivaplan_reverse_line_direction'

    def displayName(self):
        return self.tr('Reverse Line Direction')

    def group(self):
        return self.tr('')

    def groupId(self):
        return ''

    def createInstance(self):
        return ReverseLineDirection()

    def shortHelpString(self):
        return self.tr('''
        <h3>Reverse Line Direction</h3>
        <p>Reverses the vertex order of line features without changing their shape.</p>
        <h4>Parameters</h4>
        <ul>
          <li><b>Input line:</b> Centerline, bankline, or any other line layer.</li>
        </ul>
        <h4>Output</h4>
        <p>A new line layer with reversed vertex order. Attributes and CRS are retained.</p>
        <p>This tool does not identify upstream or downstream. Verify river flow
        direction before using the output for chainage-based analysis.</p>
        <p>For detailed documentation, see the
        <a href="https://github.com/Rohit-Sharma-21/RivaPlan">RivaPlan GitHub page</a>.</p>
        ''')

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.INPUT, self.tr('Input line layer'),
            types=[QgsProcessing.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr('Reversed line'),
            type=QgsProcessing.TypeVectorLine,
            createByDefault=True, supportsAppend=False))

    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        if source is None or not source.isValid():
            raise QgsProcessingException('Select a valid input line layer.')
        if source.featureCount() == 0:
            raise QgsProcessingException('Input line layer contains no features.')
        if feedback.isCanceled():
            return {}

        # Reuse the established QGIS algorithm to preserve attributes,
        # geometry dimensions (including Z/M where supported), and CRS.
        # Pass the actual Processing destination, not a memory: filename.
        result = processing.run(
            'native:reverselinedirection',
            {
                'INPUT': source,
                'OUTPUT': parameters[self.OUTPUT],
            },
            context=context,
            feedback=feedback,
            is_child_algorithm=True,
        )
        output_id = result['OUTPUT']
        feedback.pushInfo('RivaPlan | Reverse Line Direction')
        feedback.pushInfo('Reversed {} line feature(s).'.format(source.featureCount()))
        feedback.pushInfo('Original layer is unchanged.')
        feedback.pushInfo('Flow direction is not inferred; verify U/S and D/S independently.')
        return {self.OUTPUT: output_id}
