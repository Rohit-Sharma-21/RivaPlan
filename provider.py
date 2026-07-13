# -*- coding: utf-8 -*-

import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QIcon

# Import algorithm classes from your script files
from .centerline_shift import centerlineshifting
from .curvature import LocalCurvatureSimple
from .generate_centerline import mergingbanklines
from .shifting_calculation import banklineshifting
from .sinuosity import linesinuosity
from .width import WidthOfRiver


class RivaPlanProvider(QgsProcessingProvider):

    def __init__(self):
        super().__init__()

    def tr(self, string):
        return QCoreApplication.translate("RivaPlanProvider", string)

    def loadAlgorithms(self):
        self.addAlgorithm(mergingbanklines())
        self.addAlgorithm(WidthOfRiver())
        self.addAlgorithm(banklineshifting())
        self.addAlgorithm(centerlineshifting())
        self.addAlgorithm(linesinuosity())
        self.addAlgorithm(LocalCurvatureSimple())

    def id(self):
        return "rivaplan"

    def name(self):
        return self.tr("RivaPlan")

    def longName(self):
        return self.name()

    def icon(self):
        icon_path = os.path.join(os.path.dirname(__file__), "logo.png")
        return QIcon(icon_path)