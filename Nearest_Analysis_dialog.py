# -*- coding: utf-8 -*-
"""
Nearest Analysis QGIS Plugin Launcher
"""

from PyQt5.QtWidgets import QAction, QApplication
from .Nearest_Analysis import NearestAnalysisDialog


class NearestAnalysisPlugin:
    def __init__(self, iface):
        """Initialize the plugin"""
        self.iface = iface
        self.plugin_name = "Nearest Analysis"
        self.dialog = None
        self.action = None

    def initGui(self):
        """Create action in QGIS menu and toolbar"""
        self.action = QAction(self.plugin_name, self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu(self.plugin_name, self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        """Remove menu item and toolbar icon on unload"""
        if self.action:
            self.iface.removePluginMenu(self.plugin_name, self.action)
            self.iface.removeToolBarIcon(self.action)

    def run(self):
        """Run the plugin main dialog"""
        parent = QApplication.activeWindow()

        if self.dialog is None:
            self.dialog = NearestAnalysisDialog(parent)
        else:
            # refresh content if dialog already exists
            try:
                self.dialog.populate_layers()
            except Exception:
                pass

        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()