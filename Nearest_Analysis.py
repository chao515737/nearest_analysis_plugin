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
  - Exports the figure to PNG/PDF with EPA attribution on the map

Notes
-----
- Network calls are made directly to EPA WFS and ArcGIS REST endpoints.
- Only the single nearest feature is exported.
"""

from PyQt5 import QtWidgets, uic
from PyQt5.QtCore import Qt
from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsDataSourceUri,
    QgsMessageLog,
    Qgis,
)
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

        # ---- WFS endpoint (EPA) ----
        self.WFS_URL = "https://gis.epa.ie/geoserver/EPA/wfs"
        self.WFS_VERSION = "1.1.0"

        # ---- Attribution text (EPA requirement) ----
        self.EPA_ATTRIBUTION = "Contains data from the Environmental Protection Agency (EPA), licensed under CC BY 4.0"

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
        # Cache pre-filtered API GeoDataFrames.
        # Keyed by (api_display_name, app_layer_data_source_uri)
        self.api_pre_filtered = {}
        # Caches to avoid repeated network calls when switching selections
        self.wfs_fields_cache = {}      # key: type_name -> [field names]
        self.arcgis_fields_cache = {}   # key: json_url -> [field names]

        # Reuse a single HTTP session for better performance (keep-alive, connection pooling)
        self.http = requests.Session()

        # ---- OPTIONAL: timeouts (connect_timeout, read_timeout) ----
        # Meta requests (DescribeFeatureType / ArcGIS layer JSON)
        self.timeout_meta = (10, 120)
        # Data requests (GetFeature / ArcGIS query)
        self.timeout_data = (10, 180)

        # Optional: some servers are more stable with a User-Agent header
        self.http.headers.update({"User-Agent": "QGIS-NearestAnalysis"})

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
    # NEW: auto-load selected WFS layer into QGIS if not present
    # -------------------------------
    def _ensure_selected_api_layer_loaded(self) -> None:
        """
        If user selected a WFS capability item (display_name in wfs_layers_info),
        ensure it is loaded into QGIS project as a QgsVectorLayer (provider: WFS).
        If already loaded, do nothing.
        """
        api_name = (self.combo_api.currentText() or "").strip()
        if not api_name:
            return

        type_name = self.wfs_layers_info.get(api_name)
        if not type_name:
            # Not a WFS capability item; likely an already-loaded layer or ArcGIS.
            return

        # 1) Check if already loaded
        for lyr in QgsProject.instance().mapLayers().values():
            if isinstance(lyr, QgsVectorLayer) and lyr.providerType().upper() == "WFS":
                src = (lyr.source() or "")
                if type_name in src:
                    self.log(f"WFS layer already loaded: {lyr.name()}")
                    return

        # 2) Load it
        uri = QgsDataSourceUri()
        uri.setParam("url", self.WFS_URL)
        uri.setParam("typename", type_name)
        uri.setParam("version", self.WFS_VERSION)

        wfs_layer = QgsVectorLayer(uri.uri(), api_name, "WFS")
        if not wfs_layer.isValid():
            msg = f"Failed to load WFS layer into QGIS: {api_name}"
            self.log(msg)
            try:
                QgsMessageLog.logMessage(msg, "NearestAnalysis", Qgis.Warning)
            except Exception:
                pass
            return

        QgsProject.instance().addMapLayer(wfs_layer)
        self.log(f"Loaded WFS layer into QGIS: {wfs_layer.name()}")

        # 3) Update local cache lists so other logic can see it without forcing a full refresh
        self.api_layers.append(wfs_layer)

    def on_api_selection_changed(self) -> None:
        """
        Slot for combo_api change:
        - if selected item is a WFS capabilities entry, auto-load it into QGIS
        - then update fields list
        """
        try:
            self._ensure_selected_api_layer_loaded()
        except Exception as e:
            self.log(f"Auto-load API layer failed: {e}")
        self.update_fields_for_api()

    # -------------------------------
    # Populate layer lists
    # -------------------------------
    def populate_layers(self) -> None:
        self.combo_app.clear()
        self.combo_api.clear()
        self.shp_layers = []
        self.api_layers = []
        self.wfs_layers_info.clear()

        # Collect vector layers from the current QGIS project
        # combo_app: only local shapefile/application layers
        # combo_api: ONLY EPA WFS layers (do not add project API layers)
        for layer in QgsProject.instance().mapLayers().values():
            if isinstance(layer, QgsVectorLayer):
                provider = layer.providerType().lower()
                name = layer.name()
                if provider == "ogr" or name.lower().endswith(".shp"):
                    self.combo_app.addItem(name)
                    self.shp_layers.append(layer)

        # Load EPA WFS contents for user convenience (capabilities)
        try:
            wfs = WebFeatureService(url=self.WFS_URL, version=self.WFS_VERSION)
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
        try:
            self.combo_api.currentIndexChanged.disconnect(self.update_fields_for_api)
        except Exception:
            pass
        try:
            self.combo_api.currentIndexChanged.disconnect(self.on_api_selection_changed)
        except Exception:
            pass

        self.combo_api.currentIndexChanged.connect(self.on_api_selection_changed)

        # Also refresh once for current selection
        try:
            self.on_api_selection_changed()
        except Exception:
            pass

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
            # Use cached field list when available
            if type_name in self.wfs_fields_cache:
                for f in self.wfs_fields_cache[type_name]:
                    self.fields_list.addItem(f)
                self.log(f"Loaded {len(self.wfs_fields_cache[type_name])} WFS fields (cached).")
                return
            try:
                params = {
                    "service": "WFS",
                    "version": self.WFS_VERSION,
                    "request": "DescribeFeatureType",
                    "typename": type_name
                }
                r = self.http.get(self.WFS_URL, params=params, timeout=self.timeout_meta)
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
                self.wfs_fields_cache[type_name] = fields
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

                if json_url in self.arcgis_fields_cache:
                    for f in self.arcgis_fields_cache[json_url]:
                        self.fields_list.addItem(f)
                    self.log(f"Loaded {len(self.arcgis_fields_cache[json_url])} ArcGIS REST fields (cached).")
                    return

                rj = self.http.get(json_url, timeout=self.timeout_meta)
                rj.raise_for_status()
                data = rj.json()

                fields = [f["name"] for f in data.get("fields", []) if "name" in f]
                self.arcgis_fields_cache[json_url] = fields
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
    def run_prestep(self, app_gdf_29903: gpd.GeoDataFrame, buffer_geom) -> None:
        """Download API features intersecting the provided 100 km buffer around the application area."""
        try:
            api_index = self.combo_api.currentIndex()
            if api_index < 0:
                return

            api_name = self.combo_api.currentText()
            type_name = self.wfs_layers_info.get(api_name)
            app_layer = self.shp_layers[self.combo_app.currentIndex()]
            app_uri = app_layer.dataProvider().dataSourceUri()
            cache_key = (api_name, app_uri)
            if cache_key in self.api_pre_filtered:
                self.log("Using cached preprocessed data.")
                return

            minx, miny, maxx, maxy = buffer_geom.bounds

            # Case 1: WFS GetFeature (GeoJSON)
            if type_name:
                try:
                    params = {
                        "service": "WFS",
                        "version": self.WFS_VERSION,
                        "request": "GetFeature",
                        "typename": type_name,
                        "outputFormat": "application/json",
                        "srsName": "EPSG:29903",
                        "bbox": f"{minx},{miny},{maxx},{maxy},EPSG:29903",
                    }
                    r = self.http.get(self.WFS_URL, params=params, timeout=self.timeout_data)
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
                    r = self.http.get(base_url, params=params, timeout=self.timeout_data)
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
    # Run Analysis → CSV + Matplotlib Figure Window + Export Map
    # -------------------------------
    def run_analysis(self) -> None:
        """Export the single nearest feature to CSV, display a figure window, and export the map to PNG/PDF."""
        try:
            import matplotlib.pyplot as plt
            from matplotlib.patches import FancyArrowPatch

            self.log("All computations use EPSG:29903 (Irish Grid).")
            app_index = self.combo_app.currentIndex()
            api_name = self.combo_api.currentText()
            if app_index < 0 or not api_name:
                QtWidgets.QMessageBox.warning(self, "Error", "Please select both layers.")
                return

            # Read application layer once
            app_layer = self.shp_layers[app_index]
            app_uri = app_layer.dataProvider().dataSourceUri()
            app_gdf = gpd.read_file(app_uri)
            app_gdf_29903, _, _ = self._to_metric_gdf(app_gdf)

            # Build buffer once (100 km)
            app_union = app_gdf_29903.unary_union
            app_centroid = app_union.centroid
            user_buffer_geom = app_union.buffer(100000)

            # Pre-download / cache API features only when running
            try:
                QtWidgets.QApplication.setOverrideCursor(Qt.WaitCursor)
            except Exception:
                pass
            try:
                self.run_prestep(app_gdf_29903, user_buffer_geom)
            finally:
                try:
                    QtWidgets.QApplication.restoreOverrideCursor()
                except Exception:
                    pass

            cache_key = (api_name, app_uri)
            pre_gdf = self.api_pre_filtered.get(cache_key)
            if pre_gdf is None or pre_gdf.empty:
                QtWidgets.QMessageBox.warning(self, "Error", "Preprocessed data is empty.")
                return

            result_gdf = pre_gdf[pre_gdf.geometry.intersects(user_buffer_geom)]
            if result_gdf.empty:
                QtWidgets.QMessageBox.information(self, "No Results", "No features found within the 100 km buffer.")
                return

            # Angle helpers
            def azimuth_to_dir(deg: float) -> str:
                dirs8 = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
                idx = int((deg + 22.5) // 45) % 8
                return dirs8[idx]

            def azimuth_geographic(p1, p2) -> float:
                """
                Geographic azimuth:
                - 0° = North
                - clockwise positive
                - p1 = start point
                - p2 = end point
                """
                dx = p2.x - p1.x
                dy = p2.y - p1.y
                angle_deg = math.degrees(math.atan2(dx, dy))
                return (angle_deg + 360) % 360

            # Find nearest feature by distance from API geometry to app boundary
            dist_series = result_gdf.geometry.distance(app_union.boundary)
            nearest_idx = dist_series.idxmin()
            nearest_row = result_gdf.loc[[nearest_idx]].copy().reset_index(drop=True)

            nearest_geom = nearest_row.geometry.iloc[0]

            # IMPORTANT:
            # start point for angle/arrow = app centroid
            # end point for angle/arrow = nearest point on API geometry
            nearest_pt_on_api, nearest_pt_on_app = nearest_points(nearest_geom, app_union.boundary)

            dist = float(dist_series.loc[nearest_idx])
            angle = azimuth_geographic(app_centroid, nearest_pt_on_api)

            nearest_row["Distance_m"] = [dist]
            nearest_row["Direction (°)"] = [round(angle, 2)]
            nearest_row["Direction"] = [azimuth_to_dir(angle)]

            result_gdf = nearest_row

            # Determine export columns
            selected_fields = [item.text() for item in self.fields_list.selectedItems()]
            if not selected_fields:
                selected_fields = [c for c in result_gdf.columns if c != "geometry"]
            export_cols = selected_fields + ["Distance_m", "Direction (°)", "Direction"]

            # -------------------------------
            # Save CSV
            # -------------------------------
            output_csv, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                "Save CSV",
                os.path.expanduser("~/Nearest_Result.csv"),
                "CSV Files (*.csv)"
            )

            if not output_csv:
                self.log("CSV save cancelled by user.")
                return

            if not output_csv.lower().endswith(".csv"):
                output_csv += ".csv"

            try:
                with open(output_csv, "w", encoding="utf-8-sig", newline="") as f:
                    f.write("# Contains data from the Environmental Protection Agency (EPA), licensed under CC BY 4.0\n")
                    f.write("# Source: EPA API\n")
                    result_gdf[export_cols].to_csv(
                        f,
                        index=False,
                        lineterminator="\n"
                    )

                self.log(f"CSV saved: {output_csv}")
                self.log("EPA attribution added to CSV header.")

            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "CSV Export Error", str(e))
                self.log(f"CSV export failed: {e}")

            # -------------------------------
            # Plot (Matplotlib)
            # -------------------------------
            self.log("Opening Matplotlib Figure Window...")

            fig, ax = plt.subplots(figsize=(10, 10))
            app_gdf_29903.boundary.plot(ax=ax, color="blue", linewidth=2, label="Application Area")
            result_gdf.plot(ax=ax, color="cyan", alpha=0.6, label="Nearest Feature")

            gpd.GeoDataFrame(geometry=[app_centroid], crs="EPSG:29903").plot(
                ax=ax, color="red", marker="o", markersize=60, label="Centroid"
            )
            gpd.GeoDataFrame(geometry=[nearest_pt_on_app], crs="EPSG:29903").plot(
                ax=ax, color="orange", marker="x", markersize=100, label="Nearest Point - App"
            )
            gpd.GeoDataFrame(geometry=[nearest_pt_on_api], crs="EPSG:29903").plot(
                ax=ax, color="green", marker="o", markersize=100, label="Nearest Point - API"
            )

            # Arrow:
            # start = combo_app centroid
            # end   = nearest point on combo_api
            arrow = FancyArrowPatch(
                posA=(app_centroid.x, app_centroid.y),
                posB=(nearest_pt_on_api.x, nearest_pt_on_api.y),
                arrowstyle="->",
                mutation_scale=18,
                linewidth=2,
                color="red",
                transform=ax.transData,
                shrinkA=0,
                shrinkB=0,
                zorder=5,
            )
            ax.add_patch(arrow)

            # Label
            label_x = (app_centroid.x + nearest_pt_on_api.x) / 2.0
            label_y = (app_centroid.y + nearest_pt_on_api.y) / 2.0
            ax.text(
                label_x,
                label_y,
                f"{round(result_gdf['Distance_m'].iloc[0], 2)} m\n"
                f"{result_gdf['Direction (°)'].iloc[0]}° ({result_gdf['Direction'].iloc[0]})",
                fontsize=10,
                color="darkred",
                ha="center",
                va="center",
                bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.8, edgecolor="none"),
                zorder=6,
            )

            ax.legend()
            ax.set_title("Nearest Feature and Geographic Azimuth (EPSG:29903)")
            ax.set_xlabel("Easting (m)")
            ax.set_ylabel("Northing (m)")
            ax.grid(True)
            ax.set_aspect("equal", adjustable="box")

            fig.text(
                0.5,
                0.01,
                self.EPA_ATTRIBUTION,
                ha="center",
                va="bottom",
                fontsize=8,
            )

            fig.tight_layout()
            fig.subplots_adjust(bottom=0.06)

            # -------------------------------
            # Export Map (PNG/PDF)
            # -------------------------------
            output_map, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                "Save Map (PNG/PDF)",
                os.path.expanduser("~/Nearest_Result.png"),
                "PNG (*.png);;PDF (*.pdf)",
            )

            if output_map:
                lower = output_map.lower()
                if not (lower.endswith(".png") or lower.endswith(".pdf")):
                    output_map += ".png"

                fig.savefig(output_map, dpi=300, bbox_inches="tight")
                self.log(f"Map saved: {output_map}")
                self.log(f"Map attribution added: {self.EPA_ATTRIBUTION}")
            else:
                self.log("Map export cancelled by user.")

            plt.show()

        except Exception as e:
            self.log(f"Analysis failed: {e}")
            self.log(traceback.format_exc())
