# -*- coding: utf-8 -*-
"""
Nearest_Analysis.py
Author: Chao Gong
Description
-----------
QGIS dialog for nearest-feature analysis between a local "Application Area"
(vector layer) and a remote API layer (EPA WFS or ArcGIS Feature Service / Map Server).

Native QGIS version:
- No GeoPandas dependency
- Uses QgsGeometry / QgsSpatialIndex / QgsCoordinateTransform
- Keeps EPSG:29903 workflow
- Keeps CSV export + Matplotlib figure export

Modified:
- When API layer is polygonal and Application Area is inside / overlapping the API polygon,
  Direction is set to "Inside" and Direction (°) is left blank.
"""

from PyQt5 import QtWidgets, uic
from PyQt5.QtCore import Qt
from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsDataSourceUri,
    QgsMessageLog,
    Qgis,
    QgsFeature,
    QgsGeometry,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsSpatialIndex,
    QgsRectangle,
    QgsPointXY,
    QgsWkbTypes,
)
import time
import os
import csv
import json
import math
import tempfile
import traceback
import requests
import xml.etree.ElementTree as ET
from owslib.wfs import WebFeatureService


class NearestAnalysisDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__()
        uic.loadUi(os.path.join(os.path.dirname(__file__), "Nearest_Analysis_dialog_base.ui"), self)

        # ---- WFS endpoint (EPA) ----
        self.WFS_URL = "https://gis.epa.ie/geoserver/EPA/wfs"
        self.WFS_VERSION = "1.1.0"

        # ---- Attribution text (EPA requirement) ----
        self.EPA_ATTRIBUTION = "Contains data from the Environmental Protection Agency (EPA), licensed under CC BY 4.0"

        # Default buffer distance cache (meters)
        self.buffer_distance_m = 30000.0

        # Cache: key = (api_display_name, app_layer_uri, buffer_distance_m)
        self.api_pre_filtered = {}

        # Track last analysis inputs for auto cache invalidation
        self.last_analysis_signature = None

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
        self.shp_layers = []
        self.api_layers = []

        # Caches for field metadata
        self.wfs_fields_cache = {}      # key: type_name -> [field names]
        self.arcgis_fields_cache = {}   # key: json_url -> [field names]

        # Reuse one session
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": "QGIS-NearestAnalysis"})

        # Timeouts
        self.timeout_meta = (10, 120)
        self.timeout_data = (10, 180)

        self.metric_crs = QgsCoordinateReferenceSystem("EPSG:29903")

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
    # Buffer distance helpers
    # -------------------------------
    def _get_buffer_distance_m(self) -> float:
        """
        Read buffer distance from UI in kilometers and convert to meters.
        Default: 30 km = 30000 m
        """
        default_km = 30.0

        try:
            if hasattr(self, "buffer_distance_spin"):
                km_val = float(self.buffer_distance_spin.value())
                if km_val > 0:
                    return km_val * 1000.0
        except Exception as e:
            self.log(f"Failed to read buffer distance from UI: {e}")

        return default_km * 1000.0

    def _make_analysis_signature(self, api_name: str, app_uri: str, buffer_distance_m: float):
        """
        Signature for determining whether cached preprocessed data is still valid.
        """
        return (api_name, app_uri, round(buffer_distance_m, 3))

    def _invalidate_cache_if_needed(self, api_name: str, app_uri: str, buffer_distance_m: float) -> None:
        """
        Automatically clear cached preprocessing results if app layer / API / buffer distance changed.
        """
        new_signature = self._make_analysis_signature(api_name, app_uri, buffer_distance_m)

        if self.last_analysis_signature is None:
            self.last_analysis_signature = new_signature
            return

        if new_signature != self.last_analysis_signature:
            self.log("Analysis inputs changed. Clearing cached preprocessed data.")
            self.api_pre_filtered.clear()
            self.last_analysis_signature = new_signature

    # -------------------------------
    # CRS / geometry helpers
    # -------------------------------
    def _transform_geometry(self, geom: QgsGeometry, src_crs: QgsCoordinateReferenceSystem,
                            dst_crs: QgsCoordinateReferenceSystem = None) -> QgsGeometry:
        if dst_crs is None:
            dst_crs = self.metric_crs

        if not geom or geom.isEmpty():
            return QgsGeometry()

        if not src_crs.isValid():
            self.log("Invalid source CRS; geometry returned unchanged.")
            return QgsGeometry(geom)

        if src_crs == dst_crs:
            return QgsGeometry(geom)

        g = QgsGeometry(geom)
        try:
            tr = QgsCoordinateTransform(src_crs, dst_crs, QgsProject.instance())
            ok = g.transform(tr)
            if ok != 0:
                self.log("Geometry transform returned non-zero status.")
            return g
        except Exception as e:
            self.log(f"Geometry transform failed: {e}")
            return QgsGeometry(geom)

    def _layer_geometries_in_29903(self, layer: QgsVectorLayer):
        """Return transformed feature list and unary union in EPSG:29903."""
        feats_29903 = []
        src_crs = layer.crs()

        union_geom = None
        for f in layer.getFeatures():
            geom = f.geometry()
            if not geom or geom.isEmpty():
                continue
            geom_29903 = self._transform_geometry(geom, src_crs, self.metric_crs)
            if geom_29903.isEmpty():
                continue

            new_f = QgsFeature()
            new_f.setGeometry(geom_29903)
            new_f.setAttributes(f.attributes())
            new_f.setFields(layer.fields())
            feats_29903.append(new_f)

            if union_geom is None:
                union_geom = QgsGeometry(geom_29903)
            else:
                try:
                    union_geom = union_geom.combine(geom_29903)
                except Exception:
                    try:
                        union_geom = union_geom.unaryUnion([union_geom, geom_29903])
                    except Exception:
                        pass

        return feats_29903, union_geom

    def _geom_bbox_str(self, geom: QgsGeometry) -> str:
        rect = geom.boundingBox()
        return f"{rect.xMinimum()},{rect.yMinimum()},{rect.xMaximum()},{rect.yMaximum()}"

    def _rect_to_qgsrect(self, geom: QgsGeometry) -> QgsRectangle:
        return geom.boundingBox()

    # -------------------------------
    # Geometry drawing helpers
    # -------------------------------
    def _plot_qgs_geometry(self, ax, geom: QgsGeometry, color="blue", linewidth=1.5,
                           alpha=1.0, marker=None, markersize=50, label=None):
        """Plot QGIS geometry on a Matplotlib axis."""
        if not geom or geom.isEmpty():
            return

        try:
            geom_dict = json.loads(geom.asJson())
        except Exception:
            return

        gtype = geom_dict.get("type")
        coords = geom_dict.get("coordinates")

        if not gtype or coords is None:
            return

        first = True

        def add_label():
            nonlocal first
            if first:
                first = False
                return label
            return None

        if gtype == "Point":
            x, y = coords
            ax.scatter([x], [y], s=markersize, marker=marker or "o",
                       color=color, alpha=alpha, label=add_label())
        elif gtype == "MultiPoint":
            xs = [p[0] for p in coords]
            ys = [p[1] for p in coords]
            ax.scatter(xs, ys, s=markersize, marker=marker or "o",
                       color=color, alpha=alpha, label=add_label())
        elif gtype == "LineString":
            xs = [p[0] for p in coords]
            ys = [p[1] for p in coords]
            ax.plot(xs, ys, color=color, linewidth=linewidth, alpha=alpha, label=add_label())
        elif gtype == "MultiLineString":
            for line in coords:
                xs = [p[0] for p in line]
                ys = [p[1] for p in line]
                ax.plot(xs, ys, color=color, linewidth=linewidth, alpha=alpha, label=add_label())
        elif gtype == "Polygon":
            exterior = coords[0]
            xs = [p[0] for p in exterior]
            ys = [p[1] for p in exterior]
            ax.plot(xs, ys, color=color, linewidth=linewidth, alpha=alpha, label=add_label())
            for ring in coords[1:]:
                xs = [p[0] for p in ring]
                ys = [p[1] for p in ring]
                ax.plot(xs, ys, color=color, linewidth=max(0.7, linewidth * 0.7), alpha=alpha)
        elif gtype == "MultiPolygon":
            for poly in coords:
                exterior = poly[0]
                xs = [p[0] for p in exterior]
                ys = [p[1] for p in exterior]
                ax.plot(xs, ys, color=color, linewidth=linewidth, alpha=alpha, label=add_label())
                for ring in poly[1:]:
                    xs = [p[0] for p in ring]
                    ys = [p[1] for p in ring]
                    ax.plot(xs, ys, color=color, linewidth=max(0.7, linewidth * 0.7), alpha=alpha)
        else:
            rect = geom.boundingBox()
            xs = [rect.xMinimum(), rect.xMaximum(), rect.xMaximum(), rect.xMinimum(), rect.xMinimum()]
            ys = [rect.yMinimum(), rect.yMinimum(), rect.yMaximum(), rect.yMaximum(), rect.yMinimum()]
            ax.plot(xs, ys, color=color, linewidth=linewidth, alpha=alpha, label=add_label())

    def _point_from_geometry(self, geom: QgsGeometry):
        if not geom or geom.isEmpty():
            return None
        try:
            if QgsWkbTypes.geometryType(geom.wkbType()) == QgsWkbTypes.PointGeometry:
                if geom.isMultipart():
                    pts = geom.asMultiPoint()
                    return pts[0] if pts else None
                return geom.asPoint()
        except Exception:
            pass
        return geom.centroid().asPoint()

    def _is_polygon_geometry(self, geom: QgsGeometry) -> bool:
        try:
            return QgsWkbTypes.geometryType(geom.wkbType()) == QgsWkbTypes.PolygonGeometry
        except Exception:
            return False

    def _should_mark_inside(self, app_geom: QgsGeometry, api_geom: QgsGeometry, dist: float) -> bool:
        """
        For polygon API features, if Application Area is inside / overlapping / intersecting
        and distance == 0, mark direction as Inside.
        """
        try:
            if api_geom is None or api_geom.isEmpty() or app_geom is None or app_geom.isEmpty():
                return False

            if dist is None or abs(float(dist)) > 1e-9:
                return False

            if not self._is_polygon_geometry(api_geom):
                return False

            if api_geom.contains(app_geom):
                return True
            if api_geom.intersects(app_geom):
                return True
            if api_geom.overlaps(app_geom):
                return True
            if api_geom.touches(app_geom):
                return True
        except Exception:
            return False

        return False

    # -------------------------------
    # WFS / ArcGIS metadata
    # -------------------------------
    def _ensure_selected_api_layer_loaded(self) -> None:
        """
        If current combo_api item is a WFS capability entry, ensure it is loaded in QGIS.
        If already loaded, do nothing.
        """
        api_name = (self.combo_api.currentText() or "").strip()
        if not api_name:
            return

        type_name = self.wfs_layers_info.get(api_name)
        if not type_name:
            return

        for lyr in QgsProject.instance().mapLayers().values():
            if isinstance(lyr, QgsVectorLayer) and lyr.providerType().upper() == "WFS":
                src = lyr.source() or ""
                if type_name in src:
                    self.log(f"WFS layer already loaded: {lyr.name()}")
                    if lyr not in self.api_layers:
                        self.api_layers.append(lyr)
                    return

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
        self.api_layers.append(wfs_layer)

    def on_api_selection_changed(self) -> None:
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

        for layer in QgsProject.instance().mapLayers().values():
            if not isinstance(layer, QgsVectorLayer):
                continue

            provider = (layer.providerType() or "").lower()
            name = layer.name() or ""
            src = (layer.source() or "").lower()

            if provider == "ogr" or name.lower().endswith(".shp"):
                self.combo_app.addItem(name)
                self.shp_layers.append(layer)

            if ("/featureserver/" in src or "/mapserver/" in src or
                    provider in ("arcgisfeatureserver", "arcgismapserver")):
                self.combo_api.addItem(name)
                self.api_layers.append(layer)

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

        if hasattr(self, "fields_list") and self.shp_layers:
            self.fields_list.clear()
            try:
                for field in self.shp_layers[0].fields():
                    self.fields_list.addItem(field.name())
            except Exception:
                pass

        try:
            self.combo_api.currentIndexChanged.disconnect(self.update_fields_for_api)
        except Exception:
            pass
        try:
            self.combo_api.currentIndexChanged.disconnect(self.on_api_selection_changed)
        except Exception:
            pass

        self.combo_api.currentIndexChanged.connect(self.on_api_selection_changed)

        try:
            self.on_api_selection_changed()
        except Exception:
            pass

    # -------------------------------
    # Update fields for API
    # -------------------------------
    def update_fields_for_api(self) -> None:
        if not hasattr(self, "fields_list"):
            return

        self.fields_list.clear()
        api_name = self.combo_api.currentText()
        if not api_name:
            return

        type_name = self.wfs_layers_info.get(api_name)
        if type_name:
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

                self.wfs_fields_cache[type_name] = fields
                for f in fields:
                    self.fields_list.addItem(f)
                self.log(f"Loaded {len(fields)} WFS fields.")
            except Exception as e:
                self.log(f"Failed to load WFS fields: {e}")
            return

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
                json_url = base_url.split("/query")[0].rstrip("/") + "?f=json"

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
    # Remote download helpers
    # -------------------------------
    def _load_geojson_text_as_layer(self, geojson_text: str, layer_name: str) -> QgsVectorLayer:
        try:
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".geojson")
            tmp.close()
            with open(tmp.name, "w", encoding="utf-8") as f:
                f.write(geojson_text)

            lyr = QgsVectorLayer(tmp.name, layer_name, "ogr")
            if lyr.isValid():
                return lyr
            self.log("Temporary GeoJSON layer is invalid.")
            return None
        except Exception as e:
            self.log(f"Failed to load temporary GeoJSON: {e}")
            return None

    def _download_wfs_layer(self, type_name: str, buffer_geom_29903: QgsGeometry) -> QgsVectorLayer:
        try:
            rect = buffer_geom_29903.boundingBox()
            bbox = f"{rect.xMinimum()},{rect.yMinimum()},{rect.xMaximum()},{rect.yMaximum()},EPSG:29903"

            params = {
                "service": "WFS",
                "version": self.WFS_VERSION,
                "request": "GetFeature",
                "typename": type_name,
                "outputFormat": "application/json",
                "srsName": "EPSG:29903",
                "bbox": bbox,
            }

            r = self.http.get(self.WFS_URL, params=params, timeout=self.timeout_data)
            r.raise_for_status()

            lyr = self._load_geojson_text_as_layer(r.text, type_name)
            if lyr and lyr.isValid():
                return lyr
            self.log("WFS GeoJSON layer invalid.")
            return None
        except Exception as e:
            self.log(f"WFS download failed: {e}")
            return None

    def _download_arcgis_layer(self, source_str: str, buffer_geom_29903: QgsGeometry) -> QgsVectorLayer:
        try:
            source_str = source_str.strip()
            if source_str.startswith("url="):
                source_str = source_str.replace("url=", "").strip("'\"")
            source_str = source_str.strip("'\"")

            if not ("/FeatureServer/" in source_str or "/MapServer/" in source_str):
                self.log("ArcGIS source is not FeatureServer/MapServer.")
                return None

            base_url = source_str.split("?")[0].rstrip("/")
            if not base_url.endswith("/query"):
                base_url = base_url + "/query"

            rect = buffer_geom_29903.boundingBox()
            bbox_str = f"{rect.xMinimum()},{rect.yMinimum()},{rect.xMaximum()},{rect.yMaximum()}"

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

            r = self.http.get(base_url, params=params, timeout=self.timeout_data)
            r.raise_for_status()
            lyr = self._load_geojson_text_as_layer(r.text, "arcgis_api")
            if lyr and lyr.isValid():
                return lyr
            self.log("ArcGIS GeoJSON layer invalid.")
            return None
        except Exception as e:
            self.log(f"ArcGIS REST download failed: {e}")
            return None

    # -------------------------------
    # Pre-download data
    # -------------------------------
    def run_prestep(self, app_union_29903: QgsGeometry, buffer_geom_29903: QgsGeometry) -> None:
        """Download API features intersecting buffer and cache as list of QgsFeature in EPSG:29903."""
        try:
            api_index = self.combo_api.currentIndex()
            if api_index < 0:
                return

            api_name = self.combo_api.currentText()
            app_layer = self.shp_layers[self.combo_app.currentIndex()]
            app_uri = app_layer.dataProvider().dataSourceUri()
            cache_key = self._make_analysis_signature(api_name, app_uri, self.buffer_distance_m)

            if cache_key in self.api_pre_filtered:
                self.log("Using cached preprocessed data.")
                return

            type_name = self.wfs_layers_info.get(api_name)
            remote_layer = None

            t_download = time.perf_counter()

            if type_name:
                remote_layer = self._download_wfs_layer(type_name, buffer_geom_29903)

            if remote_layer is None:
                source_str = None
                for lyr in self.api_layers:
                    if lyr.name() == api_name:
                        source_str = lyr.source()
                        break
                if source_str:
                    remote_layer = self._download_arcgis_layer(source_str, buffer_geom_29903)

            self.log(f"download remote layer: {time.perf_counter() - t_download:.2f}s")

            if remote_layer is None or not remote_layer.isValid():
                self.log("Unsupported API type or failed to download API data.")
                return

            feats = []
            idx = QgsSpatialIndex()
            remote_crs = remote_layer.crs() if remote_layer.crs().isValid() else self.metric_crs

            t_process = time.perf_counter()

            total_count = 0
            kept_count = 0

            for f in remote_layer.getFeatures():
                total_count += 1

                geom = f.geometry()
                if not geom or geom.isEmpty():
                    continue

                geom_29903 = self._transform_geometry(geom, remote_crs, self.metric_crs)
                if geom_29903.isEmpty():
                    continue

                if not geom_29903.intersects(buffer_geom_29903):
                    continue

                new_f = QgsFeature(f)
                new_f.setGeometry(geom_29903)
                feats.append(new_f)
                idx.insertFeature(new_f)
                kept_count += 1

            self.log(f"process remote features: {time.perf_counter() - t_process:.2f}s")
            self.log(f"remote total features iterated: {total_count}")
            self.log(f"remote kept features: {kept_count}")

            self.api_pre_filtered[cache_key] = {
                "features": feats,
                "index": idx
            }

            self.log(f"Found {len(feats)} API features within buffer (EPSG:29903).")

        except Exception as e:
            self.log(f"Preprocessing failed: {e}")
            self.log(traceback.format_exc())

    # -------------------------------
    # Distance + direction helpers
    # -------------------------------
    def _azimuth_to_dir(self, deg: float) -> str:
        dirs8 = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        idx = int((deg + 22.5) // 45) % 8
        return dirs8[idx]

    def _azimuth_geographic(self, p1, p2) -> float:
        dx = p2.x() - p1.x()
        dy = p2.y() - p1.y()
        angle_deg = math.degrees(math.atan2(dx, dy))
        return (angle_deg + 360) % 360

    def _shortest_line_endpoints(self, geom_a: QgsGeometry, geom_b: QgsGeometry):
        """
        Return points on geom_a and geom_b that define the shortest connecting line.
        """
        try:
            line = geom_a.shortestLine(geom_b)
            if line and not line.isEmpty():
                if line.isMultipart():
                    parts = line.asMultiPolyline()
                    if parts and parts[0] and len(parts[0]) >= 2:
                        return parts[0][0], parts[0][-1]
                else:
                    pts = line.asPolyline()
                    if pts and len(pts) >= 2:
                        return pts[0], pts[-1]
        except Exception:
            pass

        pa = self._point_from_geometry(geom_a.nearestPoint(geom_b))
        pb = self._point_from_geometry(geom_b.nearestPoint(geom_a))
        return pa, pb

    def _build_spatial_index_lookup(self, features):
        idx = QgsSpatialIndex()
        lookup = {}
        for f in features:
            idx.insertFeature(f)
            lookup[f.id()] = f
        return idx, lookup

    def _find_nearest_feature_spatial_index(self, app_union_29903: QgsGeometry, pre_data: dict):
        """
        Optimized nearest search:
        1) SpatialIndex nearestNeighbor by application centroid
        2) Exact geometry distance to application geometry
        """
        feats = pre_data.get("features", [])
        idx = pre_data.get("index", None)

        if not feats:
            return None, None, None, None, None

        if idx is None:
            idx, _ = self._build_spatial_index_lookup(feats)
        feat_lookup = {f.id(): f for f in feats}

        app_centroid = app_union_29903.centroid()
        app_centroid_pt = app_centroid.asPoint()
        app_geom = app_union_29903

        candidate_ids = idx.nearestNeighbor(QgsPointXY(app_centroid_pt), 10)
        if not candidate_ids:
            return None, None, None, None, None

        candidates = [feat_lookup[cid] for cid in candidate_ids if cid in feat_lookup]

        if not candidates:
            candidate_rect = app_union_29903.buffer(self.buffer_distance_m, 8).boundingBox()
            candidate_ids_rect = idx.intersects(candidate_rect)
            candidates = [feat_lookup[cid] for cid in candidate_ids_rect if cid in feat_lookup]

        if not candidates:
            return None, None, None, None, None

        nearest_feat = None
        min_dist = None
        nearest_pt_on_api = None
        nearest_pt_on_app = None

        for feat in candidates:
            geom = feat.geometry()
            if not geom or geom.isEmpty():
                continue

            dist = geom.distance(app_geom)

            if min_dist is None or dist < min_dist:
                p_api, p_app = self._shortest_line_endpoints(geom, app_geom)
                min_dist = dist
                nearest_feat = feat
                nearest_pt_on_api = p_api
                nearest_pt_on_app = p_app

        return nearest_feat, min_dist, app_centroid_pt, nearest_pt_on_api, nearest_pt_on_app

    # -------------------------------
    # Run Analysis
    # -------------------------------
    def run_analysis(self) -> None:
        """Export nearest feature to CSV, display figure window, and export map."""
        try:
            import matplotlib.pyplot as plt
            from matplotlib.patches import FancyArrowPatch

            self.log("All computations use EPSG:29903 (Irish Grid).")

            app_index = self.combo_app.currentIndex()
            api_name = self.combo_api.currentText()

            if app_index < 0 or not api_name:
                QtWidgets.QMessageBox.warning(self, "Error", "Please select both layers.")
                return

            app_layer = self.shp_layers[app_index]
            app_uri = app_layer.dataProvider().dataSourceUri()

            t0 = time.perf_counter()
            app_feats_29903, app_union = self._layer_geometries_in_29903(app_layer)
            self.log(f"_layer_geometries_in_29903: {time.perf_counter() - t0:.2f}s")

            if not app_union or app_union.isEmpty():
                QtWidgets.QMessageBox.warning(self, "Error", "Application layer has no valid geometry.")
                return

            app_centroid = app_union.centroid()

            self.buffer_distance_m = self._get_buffer_distance_m()
            self.log(
                f"Using buffer distance: {self.buffer_distance_m / 1000.0:.2f} km "
                f"({self.buffer_distance_m:.0f} m)"
            )

            self._invalidate_cache_if_needed(api_name, app_uri, self.buffer_distance_m)

            user_buffer_geom = app_union.buffer(self.buffer_distance_m, 8)

            try:
                QtWidgets.QApplication.setOverrideCursor(Qt.WaitCursor)
            except Exception:
                pass

            try:
                t1 = time.perf_counter()
                self.run_prestep(app_union, user_buffer_geom)
                self.log(f"run_prestep: {time.perf_counter() - t1:.2f}s")
            finally:
                try:
                    QtWidgets.QApplication.restoreOverrideCursor()
                except Exception:
                    pass

            cache_key = self._make_analysis_signature(api_name, app_uri, self.buffer_distance_m)
            pre_data = self.api_pre_filtered.get(cache_key)
            if not pre_data or not pre_data.get("features"):
                QtWidgets.QMessageBox.warning(self, "Error", "Preprocessed data is empty.")
                return

            t2 = time.perf_counter()
            nearest_feat, dist, app_centroid_pt, nearest_pt_on_api, nearest_pt_on_app = \
                self._find_nearest_feature_spatial_index(app_union, pre_data)
            self.log(f"_find_nearest_feature_spatial_index: {time.perf_counter() - t2:.2f}s")

            if nearest_feat is None:
                QtWidgets.QMessageBox.information(self, "No Results", "No nearest feature could be determined.")
                return

            nearest_geom = nearest_feat.geometry()
            is_inside_case = self._should_mark_inside(app_union, nearest_geom, dist)

            angle = None
            direction = None
            if is_inside_case:
                direction = "Inside"
                self.log("Application Area is inside / overlapping polygon API feature. Direction set to 'Inside'.")
            else:
                angle = self._azimuth_geographic(app_centroid_pt, nearest_pt_on_api)
                direction = self._azimuth_to_dir(angle)

            selected_fields = [item.text() for item in self.fields_list.selectedItems()] if hasattr(
                self, "fields_list"
            ) else []
            nearest_fields = [field.name() for field in nearest_feat.fields()] if nearest_feat.fields() else []

            if not selected_fields:
                selected_fields = nearest_fields

            export_cols = [f for f in selected_fields if f in nearest_fields]
            export_cols += ["Distance_m", "Direction (°)", "Direction"]

            row = {}
            for fld in selected_fields:
                if fld in nearest_fields:
                    row[fld] = nearest_feat[fld]

            row["Distance_m"] = float(dist)
            row["Direction (°)"] = "" if angle is None else round(angle, 2)
            row["Direction"] = direction if direction is not None else ""

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
                    writer = csv.DictWriter(f, fieldnames=export_cols, extrasaction="ignore")
                    writer.writeheader()
                    writer.writerow(row)

                self.log(f"CSV saved: {output_csv}")
                self.log("EPA attribution added to CSV header.")
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "CSV Export Error", str(e))
                self.log(f"CSV export failed: {e}")
                return

            self.log("Opening Matplotlib Figure Window...")

            fig, ax = plt.subplots(figsize=(10, 10))

            self._plot_qgs_geometry(ax, app_union, color="blue", linewidth=2, alpha=1.0, label="Application Area")
            self._plot_qgs_geometry(ax, nearest_feat.geometry(), color="cyan", linewidth=2, alpha=0.8,
                                    label="Nearest Feature")

            ax.scatter([app_centroid_pt.x()], [app_centroid_pt.y()], color="red", s=60, marker="o", label="Centroid")
            ax.scatter([nearest_pt_on_app.x()], [nearest_pt_on_app.y()], color="orange", s=100, marker="x",
                       label="Nearest Point - App")
            ax.scatter([nearest_pt_on_api.x()], [nearest_pt_on_api.y()], color="green", s=100, marker="o",
                       label="Nearest Point - API")

            if not is_inside_case:
                arrow = FancyArrowPatch(
                    posA=(app_centroid_pt.x(), app_centroid_pt.y()),
                    posB=(nearest_pt_on_api.x(), nearest_pt_on_api.y()),
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

                label_x = (app_centroid_pt.x() + nearest_pt_on_api.x()) / 2.0
                label_y = (app_centroid_pt.y() + nearest_pt_on_api.y()) / 2.0
                label_text = f"{round(dist, 2)} m\n{round(angle, 2)}° ({direction})"
            else:
                label_x = app_centroid_pt.x()
                label_y = app_centroid_pt.y()
                label_text = f"{round(dist, 2)} m\nInside"

            ax.text(
                label_x,
                label_y,
                label_text,
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

                t3 = time.perf_counter()
                fig.savefig(output_map, dpi=300, bbox_inches="tight")
                self.log(f"fig.savefig: {time.perf_counter() - t3:.2f}s")

                self.log(f"Map saved: {output_map}")
                self.log(f"Map attribution added: {self.EPA_ATTRIBUTION}")
            else:
                self.log("Map export cancelled by user.")

            plt.show()

        except Exception as e:
            self.log(f"Analysis failed: {e}")
            self.log(traceback.format_exc())
