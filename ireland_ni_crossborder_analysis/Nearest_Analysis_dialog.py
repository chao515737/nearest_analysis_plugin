# -*- coding: utf-8 -*-
"""
Nearest Analysis QGIS Plugin Launcher
QGIS 3 / QGIS 4 compatible launcher.

This file is loaded by __init__.py::classFactory(iface).
It must not import PyQt5 directly in QGIS 4 / Qt6 builds.
"""

# Use QGIS' Qt compatibility layer instead of PyQt5/PyQt6 directly.
# In QGIS 3 this maps to Qt5; in QGIS 4 this maps to Qt6.
from qgis.PyQt.QtWidgets import QApplication
from qgis.PyQt.QtGui import QAction

from .Nearest_Analysis import NearestAnalysisDialog


class NearestAnalysisPlugin:
    def __init__(self, iface):
        """Initialize the plugin."""
        self.iface = iface
        self.plugin_name = "Ireland–Northern Ireland Cross-Border Analysis"
        self.dialog = None
        self.action = None

    def initGui(self):
        """Create action in QGIS menu and toolbar."""
        self.action = QAction(self.plugin_name, self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu(self.plugin_name, self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        """Remove menu item and toolbar icon on unload."""
        if self.action:
            self.iface.removePluginMenu(self.plugin_name, self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action = None

        # Close dialog cleanly when plugin is unloaded.
        if self.dialog is not None:
            try:
                self.dialog.close()
            except Exception:
                pass
            self.dialog = None

    def run(self):
        """Run the plugin main dialog."""
        parent = QApplication.activeWindow() or self.iface.mainWindow()

        if self.dialog is None:
            self.dialog = NearestAnalysisDialog(parent)
        else:
            # Refresh layer/API lists if dialog already exists.
            try:
                self.dialog.populate_layers()
            except Exception:
                pass

        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
