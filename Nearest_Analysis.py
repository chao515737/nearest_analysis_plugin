# -*- coding: utf-8 -*-
"""
Nearest_Analysis.py
Author: Chao Gong
Description
-----------
QGIS dialog for nearest-feature analysis between a local "Application Area"
(vector layer) and a remote API layer (EPA WFS or ArcGIS Feature Service / Map Server).
The tool:
  - Normalizes all analysis to EPSG:29903 (Irish Grid)
  - Pre-downloads API features within 100 km of the application area
  - Computes nearest distance and geographic azimuth (0° = North, clockwise)
  - Exports a CSV with the single nearest feature
  - Displays a Matplotlib figure showing centroid, nearest points, and an arrow

Provenance Disclosure
---------------------
This file was originally prototyped with assistance from a large language model
(GPT-family). It has been refactored, reviewed, and documented by the author to
meet engineering and academic transparency standards. All responsibility for the
final code lies with the author.

Requirements
------------
- QGIS (PyQt5, qgis.core)
- geopandas, shapely, requests, owslib
- matplotlib (for the figure window)

Notes
-----
- Network calls are made directly to EPA WFS and ArcGIS REST endpoints.
- Only the single nearest feature is exported.
"""

from PyQt5 import QtWidgets, uic
from qgis.core import QgsProject, QgsVectorLayer
import geopandas as gpd
import os
import requests
import xml.etree.ElementTree as ET
from owslib.wfs import WebFeatureService
from shapely.geometry import shape
from shapely.ops import nearest_points
import io
import math
import traceback
from pathlib import Path


class NearestAnalysisDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__()
        uic.loadUi(os.path.join(os.path.dirname(__file__), "Nearest_Analysis_dialog_base.ui"), self)

        # Buttons
        self.run_btn.clicked.connect(self.run_analysis)
        if hasattr(self, "refresh_btn"):
            self.refresh_btn.clicked.connect(self.populate_layers)
        if hasattr(self, "clear_log_btn"):
            self.clear_log_btn.clicked.connect(self.clear_log)

        # Multi-selection for fields_list
        if hasattr(self, "fields_list"):
            self.fields_list.setSelectionMode(QtWidgets.QAbstractItemView.MultiSelection)
            self.fields_list.itemSelectionChanged.connect(self.show_selected_fields)

        # State
        self.wfs_layers_info = {}
        self.api_pre_filtered = {}

        self.populate_layers()

    # -------------------------------
    # Logging helpers
    # -------------------------------
    def log(self, message: str) -> None:
        if hasattr(self, "log_browser"):
            self.log_browser.append(message)
        else:
            print(message)

    def clear_log(self) -> None:
        if hasattr(self, "log_browser"):
            self.log_browser.clear()

    def show_selected_fields(self) -> None:
        selected = [item.text() for item in self.fields_list.selectedItems()]
        self.log(f"Selected fields: {selected}")

    # -------------------------------
    # CRS conversion → EPSG:29903
    # -------------------------------
    def _to_metric_gdf(self, gdf: gpd.GeoDataFrame):
        """
        Ensure GeoDataFrame is in EPSG:29903. If CRS is missing, assume EPSG:4326.
        Returns (gdf_29903, changed, original_crs)
        """
        orig_crs = getattr(gdf, "crs", None)
        if orig_crs is None:
            try:
                gdf = gdf.set_crs(epsg=4326)
                orig_crs = gdf.crs
                self.log("Layer has no CRS; assumed EPSG:4326.")
            except Exception:
                self.log("Unable to set CRS for the layer.")
                return gdf, False, None

        try:
            if orig_crs.to_epsg() != 29903:
                gdf_29903 = gdf.to_crs(epsg=29903)
                self.log("Reprojected layer to EPSG:29903 (Irish Grid).")
                return gdf_29903, True, orig_crs
            else:
                return gdf, False, orig_crs
        except Exception as e:
            self.log(f"CRS conversion to EPSG:29903 failed: {e}")
            return gdf, False, orig_crs

    # -------------------------------
    # Populate layer lists
    # -------------------------------
    def populate_layers(self) -> None:
        self.combo_app.clear()
        self.combo_api.clear()
        self.shp_layers = []
        self.api_layers = []
        self.wfs_layers_info.clear()
        self.api_pre_filtered.clear()

        # Collect vector layers from the current QGIS project
        for layer in QgsProject.instance().mapLayers().values():
            if isinstance(layer, QgsVectorLayer):
                provider = layer.providerType().lower()
                name = layer.name()
                if provider == "ogr" or name.lower().endswith(".shp"):
                    self.combo_app.addItem(name)
                    self.shp_layers.append(layer)
                else:
                    self.combo_api.addItem(name)
                    self.api_layers.append(layer)

        # Load EPA WFS contents for user convenience
        try:
            wfs_url = "https://gis.epa.ie/geoserver/EPA/wfs"
            wfs = WebFeatureService(url=wfs_url, version='1.1.0')
            for layer_name, layer_obj in wfs.contents.items():
                type_name = layer_name if layer_name.startswith("EPA:") else f"EPA:{layer_name}"
                title = getattr(layer_obj, "title", layer_name)
                display_name = f"{title} ({type_name})"
                self.combo_api.addItem(display_name)
                self.wfs_layers_info[display_name] = type_name
            self.log(f"Loaded {len(self.wfs_layers_info)} WFS layers.")
        except Exception as e:
            self.log(f"Failed to access WFS service: {e}")

        # Prefill fields_list using the first shapefile (if available)
        if hasattr(self, "fields_list") and self.shp_layers:
            self.fields_list.clear()
            try:
                for field in self.shp_layers[0].fields():
                    self.fields_list.addItem(field.name())
            except Exception:
                pass

        # Re-bind signals (avoid duplicates)
        for sig, slot in [
            (self.combo_api.currentIndexChanged, self.update_fields_for_api),
            (self.combo_api.currentIndexChanged, self.run_prestep),
            (self.combo_app.currentIndexChanged, self.run_prestep),
        ]:
            try:
                sig.disconnect(slot)
            except Exception:
                pass
            sig.connect(slot)

    # -------------------------------
    # Update fields for API (WFS + ArcGIS)
    # -------------------------------
    def update_fields_for_api(self) -> None:
        """Refresh the field list based on the selected API layer (WFS or ArcGIS REST)."""
        if not hasattr(self, "fields_list"):
            return

        self.fields_list.clear()
        api_name = self.combo_api.currentText()
        if not api_name:
            return

        # Case 1: WFS via DescribeFeatureType
        type_name = self.wfs_layers_info.get(api_name)
        if type_name:
            try:
                wfs_url = "https://gis.epa.ie/geoserver/EPA/wfs"
                params = {
                    "service": "WFS",
                    "version": "1.1.0",
                    "request": "DescribeFeatureType",
                    "typename": type_name
                }
                r = requests.get(wfs_url, params=params, timeout=10)
                r.raise_for_status()
                root = ET.fromstring(r.content)

                def strip_ns(tag):
                    return tag.split('}', 1)[-1] if '}' in tag else tag

                fields = []
                for element in root.iter():
                    if strip_ns(element.tag) == "element":
                        name_attr = element.attrib.get("name")
                        type_attr = element.attrib.get("type")
                        if name_attr and type_attr and not name_attr.lower().endswith("geom"):
                            fields.append(name_attr)

                for f in fields:
                    self.fields_list.addItem(f)
                self.log(f"Loaded {len(fields)} WFS fields.")
            except Exception as e:
                self.log(f"Failed to load WFS fields: {e}")
            return

        # Case 2: ArcGIS REST (layer JSON)
        try:
            source_str = None
            for lyr in self.api_layers:
                if lyr.name() == api_name:
                    source_str = lyr.source()
                    break
            if not source_str:
                return

            source_str = source_str.strip()
            if source_str.startswith("url="):
                source_str = source_str.replace("url=", "").strip("'\"")
            source_str = source_str.strip("'\"")

            if "/FeatureServer/" in source_str or "/MapServer/" in source_str:
                base_url = source_str.split("?")[0].rstrip("/")
                json_url = base_url.split("/query")[0]
                json_url = json_url.rstrip("/query") + "?f=json"

                rj = requests.get(json_url, timeout=10)
                rj.raise_for_status()
                data = rj.json()

                fields = [f["name"] for f in data.get("fields", []) if "name" in f]
                for f in fields:
                    self.fields_list.addItem(f)
                self.log(f"Loaded {len(fields)} ArcGIS REST fields.")
            else:
                self.log("Selected API is not recognized as ArcGIS REST or WFS.")
        except Exception as e:
            self.log(f"Failed to load ArcGIS fields: {e}")

    # -------------------------------
    # Pre-download data (WFS / ArcGIS)
    # -------------------------------
    def run_prestep(self) -> None:
        """Download API features intersecting a 100 km buffer around the application area."""
        try:
            app_index = self.combo_app.currentIndex()
            api_index = self.combo_api.currentIndex()
            if app_index < 0 or api_index < 0:
                return

            api_name = self.combo_api.currentText()
            type_name = self.wfs_layers_info.get(api_name)
            cache_key = (api_name, app_index)
            if cache_key in self.api_pre_filtered:
                self.log("Using cached preprocessed data.")
                return

            # Read application layer
            app_layer = self.shp_layers[app_index]
            app_gdf = gpd.read_file(app_layer.dataProvider().dataSourceUri())
            app_gdf_29903, _, _ = self._to_metric_gdf(app_gdf)
            buffer_geom = app_gdf_29903.unary_union.buffer(100000)

            # Case 1: WFS GetFeature (GeoJSON)
            if type_name:
                try:
                    wfs_url = "https://gis.epa.ie/geoserver/EPA/wfs"
                    params = {
                        "service": "WFS",
                        "version": "1.1.0",
                        "request": "GetFeature",
                        "typename": type_name,
                        "outputFormat": "application/json",
                    }
                    r = requests.get(wfs_url, params=params, timeout=60)
                    r.raise_for_status()
                    api_gdf = gpd.read_file(io.StringIO(r.text)).to_crs(epsg=29903)
                    pre_gdf = api_gdf[api_gdf.geometry.intersects(buffer_geom)].reset_index(drop=True)
                    self.api_pre_filtered[cache_key] = pre_gdf
                    self.log(f"Found {len(pre_gdf)} WFS features within 100 km (EPSG:29903).")
                    return
                except Exception as e:
                    self.log(f"WFS pre-download failed: {e}")
                    return

            # Case 2: ArcGIS REST query (GeoJSON)
            source_str = None
            for lyr in self.api_layers:
                if lyr.name() == api_name:
                    source_str = lyr.source()
                    break

            if source_str:
                source_str = source_str.strip()
                if source_str.startswith("url="):
                    source_str = source_str.replace("url=", "").strip("'\"")
                source_str = source_str.strip("'\"")

            if source_str and ("/FeatureServer/" in source_str or "/MapServer/" in source_str):
                base_url = source_str.split("?")[0]
                if not base_url.endswith("/query"):
                    base_url = base_url.rstrip("/") + "/query"

                minx, miny, maxx, maxy = buffer_geom.bounds
                bbox_str = f"{minx},{miny},{maxx},{maxy}"

                params = {
                    "where": "1=1",
                    "outFields": "*",
                    "geometry": bbox_str,
                    "geometryType": "esriGeometryEnvelope",
                    "inSR": 29903,
                    "spatialRel": "esriSpatialRelIntersects",
                    "outSR": 29903,
                    "f": "geojson",
                }

                try:
                    r = requests.get(base_url, params=params, timeout=60)
                    r.raise_for_status()
                    data = r.json()
                    feats = data.get("features", [])
                    if not feats:
                        self.log("No features returned from ArcGIS API.")
                        return

                    geo_list = []
                    for feat in feats:
                        geom = shape(feat["geometry"])
                        props = feat.get("properties") or feat.get("attributes", {})
                        geo_list.append({**props, "geometry": geom})

                    api_gdf = gpd.GeoDataFrame(geo_list, crs="EPSG:29903")
                    pre_gdf = api_gdf[api_gdf.geometry.intersects(buffer_geom)].reset_index(drop=True)
                    self.api_pre_filtered[cache_key] = pre_gdf
                    self.log(f"Found {len(pre_gdf)} ArcGIS features within 100 km (EPSG:29903).")
                    return
                except Exception as e:
                    self.log(f"ArcGIS REST pre-download failed: {e}")
                    return

            self.log("Unsupported API type or empty data source.")
        except Exception as e:
            self.log(f"Preprocessing failed: {e}")
            self.log(traceback.format_exc())

    # -------------------------------
    # Run Analysis → CSV + Matplotlib Figure Window
    # -------------------------------
    def run_analysis(self) -> None:
        """Export the single nearest feature to CSV and display a figure window."""
        try:
            import matplotlib.pyplot as plt

            self.log("All computations use EPSG:29903 (Irish Grid).")
            app_index = self.combo_app.currentIndex()
            api_name = self.combo_api.currentText()
            if app_index < 0 or not api_name:
                QtWidgets.QMessageBox.warning(self, "Error", "Please select both layers.")
                return

            cache_key = (api_name, app_index)
            pre_gdf = self.api_pre_filtered.get(cache_key)
            if pre_gdf is None or pre_gdf.empty:
                QtWidgets.QMessageBox.warning(self, "Error", "Preprocessed data is empty.")
                return

            # Read application layer
            app_layer = self.shp_layers[app_index]
            app_gdf = gpd.read_file(app_layer.dataProvider().dataSourceUri())
            app_gdf_29903, _, _ = self._to_metric_gdf(app_gdf)

            # Intersect with user buffer (100 km)
            app_union = app_gdf_29903.unary_union
            app_centroid = app_union.centroid
            user_buffer_geom = app_union.buffer(100000)
            result_gdf = pre_gdf[pre_gdf.geometry.intersects(user_buffer_geom)]
            if result_gdf.empty:
                QtWidgets.QMessageBox.information(self, "No Results", "No features found within the 100 km buffer.")
                return

            # Distance and azimuth helpers
            def azimuth_to_dir(deg: float) -> str:
                dirs8 = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
                idx = int((deg + 22.5) // 45) % 8
                return dirs8[idx]

            def azimuth_geographic(p1, p2) -> float:
                # 0° points to North; increases clockwise
                dx = p2.x - p1.x
                dy = p2.y - p1.y
                angle_deg = math.degrees(math.atan2(dx, dy))
                return (angle_deg + 360) % 360

            distances, azimuths, dirs = [], [], []
            for geom in result_gdf.geometry:
                nearest_pt_on_app = nearest_points(geom, app_union.boundary)[1]
                dist = geom.distance(nearest_pt_on_app)
                angle = azimuth_geographic(app_centroid, nearest_pt_on_app)
                distances.append(dist)
                azimuths.append(round(angle, 2))
                dirs.append(azimuth_to_dir(angle))

            result_gdf["Distance_m"] = distances
            result_gdf["Direction (°)"] = azimuths
            result_gdf["Direction"] = dirs

            # Keep only the nearest feature
            result_gdf = result_gdf.sort_values("Distance_m", ascending=True).head(1).reset_index(drop=True)

            # Determine export columns
            selected_fields = [item.text() for item in self.fields_list.selectedItems()]
            if not selected_fields:
                selected_fields = [c for c in result_gdf.columns if c != "geometry"]
            export_cols = selected_fields + ["Distance_m", "Direction (°)", "Direction"]

            # Save CSV
            output_csv, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Save CSV", os.path.expanduser("~/Nearest_Result.csv"), "CSV Files (*.csv)"
            )
            if not output_csv:
                self.log("CSV save cancelled by user.")
                return

            result_gdf[export_cols].to_csv(output_csv, index=False, encoding="utf-8-sig")
            self.log(f"CSV saved: {output_csv}")
            self.log("Opening Matplotlib Figure Window...")

            # Plot
            nearest_geom = result_gdf.geometry.iloc[0]
            nearest_pt_on_app = nearest_points(nearest_geom, app_union.boundary)[1]
            nearest_pt_on_api = nearest_points(nearest_geom, app_union.boundary)[0]

            fig, ax = plt.subplots(figsize=(10, 10))
            app_gdf_29903.boundary.plot(ax=ax, color='blue', linewidth=2, label="Application Area")
            result_gdf.plot(ax=ax, color='cyan', alpha=0.6, label="Nearest Feature")

            gpd.GeoDataFrame(geometry=[app_centroid], crs="EPSG:29903").plot(
                ax=ax, color='red', marker='o', markersize=60, label="Centroid"
            )
            gpd.GeoDataFrame(geometry=[nearest_pt_on_app], crs="EPSG:29903").plot(
                ax=ax, color='orange', marker='x', markersize=100, label="Nearest Point - App"
            )
            gpd.GeoDataFrame(geometry=[nearest_pt_on_api], crs="EPSG:29903").plot(
                ax=ax, color='green', marker='o', markersize=100, label="Nearest Point - API"
            )

            ax.annotate(
                f"{round(result_gdf['Distance_m'].iloc[0], 2)} m\n"
                f"{result_gdf['Direction (°)'].iloc[0]}° ({result_gdf['Direction'].iloc[0]})",
                xy=(nearest_pt_on_api.x, nearest_pt_on_api.y),
                xytext=(app_centroid.x, app_centroid.y),
                arrowprops=dict(facecolor='red', width=2, headwidth=10, shrink=0.05),
                fontsize=10, color='darkred', ha='center'
            )

            plt.legend()
            plt.title("Nearest Feature and Geographic Azimuth (EPSG:29903)")
            plt.xlabel("Easting (m)")
            plt.ylabel("Northing (m)")
            plt.grid(True)
            plt.axis('equal')
            plt.tight_layout()
            plt.show()

        except Exception as e:
            self.log(f"Analysis failed: {e}")
            self.log(traceback.format_exc())