# -*- coding: utf-8 -*-
"""
Init file for Nearest Analysis Plugin
"""

def classFactory(iface):
    """
    QGIS calls this function to instantiate the plugin
    """
    from .Nearest_Analysis_dialog import NearestAnalysisPlugin
    return NearestAnalysisPlugin(iface)