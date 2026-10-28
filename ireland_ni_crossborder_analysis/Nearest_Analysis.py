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
- Keeps metric CRS workflow for distance analysis
- Keeps CSV export + QGIS native result layers

Modified:
- QGIS 4 / Qt6 compatibility pass: use qgis.PyQt compatibility imports instead of PyQt5
- Automatic CRS switching between EPSG:29903 (Irish Grid) and EPSG:27700 (British National Grid)
- Zero-distance topological relations are reported separately in Spatial_Relationship;
  Direction is Undefined and Direction (°) is blank
"""

from qgis.PyQt import QtWidgets, uic
from qgis.PyQt.QtCore import Qt, QVariant, QThread, pyqtSignal, QStandardPaths
from qgis.PyQt.QtGui import QColor
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
    QgsDistanceArea,
    QgsSpatialIndex,
    QgsRectangle,
    QgsPointXY,
    QgsWkbTypes,
    QgsField,
    QgsSymbol,
    QgsPalLayerSettings,
    QgsVectorLayerSimpleLabeling,
)
import time
import os
import csv
import json
import math
import tempfile
import traceback
import re
import requests
from owslib.wfs import WebFeatureService


# -----------------------------------------------------------------------------
# QGIS 3 / QGIS 4 compatibility helpers
# -----------------------------------------------------------------------------
def _qt_enum(container, enum_name: str, value_name: str):
    """
    Return a Qt enum value that works with both Qt5-style and Qt6-style names.

    Examples:
    - Qt.WaitCursor                      # Qt5 style
    - Qt.CursorShape.WaitCursor          # Qt6 style
    - QAbstractItemView.MultiSelection   # Qt5 style
    - QAbstractItemView.SelectionMode.MultiSelection  # Qt6 style
    """
    enum_container = getattr(container, enum_name, None)
    if enum_container is not None and hasattr(enum_container, value_name):
        return getattr(enum_container, value_name)
    return getattr(container, value_name)


def _wait_cursor():
    return _qt_enum(Qt, "CursorShape", "WaitCursor")


def _multi_selection_mode():
    return _qt_enum(QtWidgets.QAbstractItemView, "SelectionMode", "MultiSelection")


def _transform_failed(transform_result) -> bool:
    """
    QgsGeometry.transform returned an integer in QGIS 3. In newer bindings it may
    be an enum-like result. Treat 0/Success/None as success and anything else as
    failure for logging only.
    """
    if transform_result is None:
        return False
    try:
        return int(transform_result) != 0
    except Exception:
        return str(transform_result).lower() not in ("0", "success", "geometryoperationresult.success")


class EACatalogueWorker(QThread):
    """Network-only EA catalogue discovery; never touches QGIS widgets."""
    completed = pyqtSignal(dict, str)

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = dict(cfg)

    def run(self):
        layers = {}
        errors = []
        urls = [self.cfg["url"]]
        seen = set(urls)
        endpoint = self.cfg["catalogue_api"]
        session = requests.Session()
        for start in range(0, 500, 100):
            if self.isInterruptionRequested():
                return
            try:
                response = session.get(endpoint, params={
                    "q": '"Environment Agency"', "rows": 100, "start": start
                }, timeout=(5, 15))
                response.raise_for_status()
                payload = response.json()
                if not payload.get("success"):
                    break
                result = payload.get("result") or {}
                packages = result.get("results") or []
                for package in packages:
                    org = (package.get("organization") or {}).get("title", "")
                    publisher = str(package.get("author") or "")
                    if "environment agency" not in (org + " " + publisher).lower():
                        continue
                    for resource in package.get("resources") or []:
                        url = (resource.get("url") or "").strip()
                        label = " ".join(str(resource.get(k) or "") for k in ("name", "format", "description"))
                        if (url.startswith("https://") and
                            ("/wfs" in url.lower() or "service=wfs" in url.lower() or "wfs" in label.lower())
                            and url not in seen):
                            seen.add(url)
                            urls.append(url)
                if not packages or start + len(packages) >= int(result.get("count", 0)):
                    break
            except Exception as exc:
                errors.append("Catalogue request: " + str(exc))
                break
        for url in urls:
            if self.isInterruptionRequested():
                return
            try:
                wfs = WebFeatureService(url=url, version=self.cfg.get("wfs_version", "2.0.0"), timeout=12)
                for layer_name, obj in wfs.contents.items():
                    title = getattr(obj, "title", layer_name)
                    display = f"{title} ({layer_name})"
                    if display in layers and layers[display]["wfs_url"] != url:
                        display += f" [{len(layers) + 1}]"
                    layers[display] = {
                        "provider_key": "ea_arcgis", "kind": "wfs", "type_name": layer_name,
                        "title": title, "source_label": self.cfg["label"],
                        "wfs_url": url, "wfs_version": self.cfg.get("wfs_version", "2.0.0")
                    }
            except Exception as exc:
                errors.append(f"WFS {url}: {exc}")
        self.completed.emit(layers, f"{len(layers)} layers from {len(urls)} candidate services; {len(errors)} request errors")


class NearestAnalysisDialog(QtWidgets.QDialog):
    PLUGIN_NAME = "Ireland–Northern Ireland Cross-Border Analysis"

    # Per-session caches. These avoid repeated network catalogue and field requests.
    GLOBAL_PROVIDER_LAYER_CACHE = {}      # provider_key -> {display_name: layer_info}
    GLOBAL_WFS_FIELDS_CACHE = {}          # (provider_key, type_name) -> [field names]
    GLOBAL_ARCGIS_FIELDS_CACHE = {}       # service_or_layer_url -> [field names]

    # ArcGIS catalogue entries are now loaded into QGIS when selected, matching
    # the EPA/WFS behaviour: the layer appears in the Layers panel and on the map.
    # Analysis still uses buffered REST downloads later, so this only affects
    # interactive display/field inspection.
    LAZY_LOAD_CATALOG_ARCGIS_ON_SELECTION = False

    def __init__(self, parent=None):
        super().__init__()
        uic.loadUi(os.path.join(os.path.dirname(__file__), "Nearest_Analysis_dialog_base.ui"), self)

        self.setWindowTitle(self.PLUGIN_NAME)

        # ---- Public API providers ----
        # EPA is a WFS catalogue. UK/NI sources are discovered from public ArcGIS Hub
        # catalogues or ArcGIS REST service roots.
        self.WFS_VERSION = "1.1.0"
        self.API_PROVIDERS = {
            "epa_ie": {
                "label": "Ireland EPA API",
                "kind": "wfs",
                "url": "https://gis.epa.ie/geoserver/EPA/wfs",
                "attribution": "Contains data from the Environmental Protection Agency (EPA), licensed under CC BY 4.0",
            },
            "daera_niea": {
                "label": "DAERA / NIEA Open Data",
                "kind": "arcgis_hub",
                "hub_url": "https://opendata-daerani.hub.arcgis.com",
                "search_query": "NIEA OR DAERA OR environment OR habitat OR protected OR ASSI",
                "attribution": "Contains public sector information from DAERA / NIEA Open Data.",
            },
            "natural_england_defra": {
                "label": "Environment Agency / Natural England Open Data",
                "kind": "arcgis_hub",
                "hub_url": "https://naturalengland-defra.opendata.arcgis.com",
                "search_query": "Natural England OR Environment Agency OR SSSI OR SAC OR SPA OR flood",
                "attribution": "Contains public sector information from Natural England / Defra Open Data.",
            },
            "ea_arcgis": {
                "label": "Environment Agency WFS / API",
                "kind": "wfs",
                "url": "https://environment.data.gov.uk/spatialdata/recorded-flood-outlines/wfs",
                "wfs_version": "2.0.0",
                "catalogue_api": "https://data.gov.uk/api/action/package_search",
                "attribution": "Contains public sector information from the Environment Agency.",
            },
        }
        self.provider_label_to_key = {v["label"]: k for k, v in self.API_PROVIDERS.items()}
        self.current_provider_key = "epa_ie"

        # ------------------------------------------------------------------
        # Performance defaults
        # ------------------------------------------------------------------
        # The older EPA-only plugin was fast because it did not automatically
        # scan ArcGIS Hub / REST catalogues and it used a small nearest-neighbour
        # candidate set. Keep that behaviour as the default.
        #
        # - Non-EPA provider catalogues are loaded only when Refresh is clicked.
        # - Cross-border CRS auto-selection is enabled by default, so the
        #   analysis does not silently force every region into EPSG:29903.
        # - Extended nearest search is opt-in. By default use the old nearest-10
        #   spatial-index search.
        self.allow_remote_catalog_discovery = False
        self.enable_cross_border_auto_crs = True
        self.enable_extended_nearest_search = False

        self.wfs_layers_info = {}
        self.api_catalog = {}
        self._ea_worker = None
        self._ea_generation = 0

        # ---- Attribution text for current provider ----
        self.EPA_ATTRIBUTION = self.API_PROVIDERS["epa_ie"]["attribution"]

        # Default buffer distance cache (meters)
        self.buffer_distance_m = 30000.0

        # Cache: key = (api_display_name, app_layer_uri, buffer_distance_m, metric_crs_authid)
        self.api_pre_filtered = {}

        # Track last analysis inputs for auto cache invalidation
        self.last_analysis_signature = None

        # Default / active metric CRS for analysis
        self.metric_crs = QgsCoordinateReferenceSystem("EPSG:29903")
        self.manual_metric_crs_authid = None

        # Buttons
        self.run_btn.clicked.connect(self.run_analysis)
        if hasattr(self, "refresh_btn"):
            self.refresh_btn.clicked.connect(self.refresh_provider_catalog)
        if hasattr(self, "clear_log_btn"):
            self.clear_log_btn.clicked.connect(self.clear_log)

        if hasattr(self, "combo_provider"):
            self.combo_provider.clear()
            for provider in self.API_PROVIDERS.values():
                self.combo_provider.addItem(provider["label"])
            self.combo_provider.currentIndexChanged.connect(self.on_provider_changed)

        # Multi-selection for fields_list
        if hasattr(self, "fields_list"):
            self.fields_list.setSelectionMode(_multi_selection_mode())
            self.fields_list.itemSelectionChanged.connect(self.show_selected_fields)

        # State
        self.shp_layers = []
        self.api_layers = []

        # Caches for field metadata
        self.wfs_fields_cache = self.GLOBAL_WFS_FIELDS_CACHE
        self.arcgis_fields_cache = self.GLOBAL_ARCGIS_FIELDS_CACHE

        # Reuse one session
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": "QGIS-NearestAnalysis"})

        # Timeouts
        self.timeout_meta = (10, 120)
        self.timeout_data = (10, 180)

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

    def _checkbox_checked(self, *names) -> bool:
        """Return True if any optional checkbox-style UI control is present and checked."""
        for name in names:
            widget = getattr(self, name, None)
            if widget is not None and hasattr(widget, "isChecked"):
                try:
                    return bool(widget.isChecked())
                except Exception:
                    pass
        return False

    def _use_cross_border_auto_crs(self) -> bool:
        return bool(self.enable_cross_border_auto_crs or self._checkbox_checked(
            "cross_border_crs_checkbox",
            "check_cross_border_crs",
            "checkbox_cross_border_crs",
        ))

    def _use_extended_nearest_search(self) -> bool:
        return bool(self.enable_extended_nearest_search or self._checkbox_checked(
            "extended_nearest_checkbox",
            "check_extended_nearest",
            "checkbox_extended_nearest",
        ))

    def refresh_provider_catalog(self) -> None:
        """Manual refresh for the currently selected provider catalogue."""
        self.allow_remote_catalog_discovery = True
        self.api_pre_filtered.clear()
        self.last_analysis_signature = None
        self.GLOBAL_PROVIDER_LAYER_CACHE.pop(self._provider_key(), None)
        if self._provider_key() == "ea_arcgis":
            self._ea_cache_file().unlink(missing_ok=True)
        self.log(f"Refreshing catalogue for {self._provider_label()}...")
        if self._provider_key() == "ea_arcgis":
            self._start_ea_catalogue(force=True)
        else:
            self.populate_layers()

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

    def _make_analysis_signature(self, api_name: str, app_uri: str, buffer_distance_m: float, metric_authid: str):
        """
        Signature for determining whether cached preprocessed data is still valid.
        """
        return (api_name, app_uri, round(buffer_distance_m, 3), metric_authid)

    def _invalidate_cache_if_needed(self, api_name: str, app_uri: str, buffer_distance_m: float, metric_authid: str) -> None:
        """
        Automatically clear cached preprocessing results if app layer / API / buffer distance / metric CRS changed.
        """
        new_signature = self._make_analysis_signature(api_name, app_uri, buffer_distance_m, metric_authid)

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
            if _transform_failed(ok):
                self.log("Geometry transform returned non-zero status.")
            return g
        except Exception as e:
            self.log(f"Geometry transform failed: {e}")
            return QgsGeometry(geom)

    def _layer_geometries_in_metric_crs(self, layer: QgsVectorLayer):
        """Return transformed feature list and unary union in active metric CRS."""
        feats_metric = []
        src_crs = layer.crs()

        union_geom = None
        for f in layer.getFeatures():
            geom = f.geometry()
            if not geom or geom.isEmpty():
                continue
            geom_metric = self._transform_geometry(geom, src_crs, self.metric_crs)
            if geom_metric.isEmpty():
                continue

            new_f = QgsFeature()
            new_f.setGeometry(geom_metric)
            new_f.setAttributes(f.attributes())
            new_f.setFields(layer.fields())
            feats_metric.append(new_f)

            if union_geom is None:
                union_geom = QgsGeometry(geom_metric)
            else:
                try:
                    union_geom = union_geom.combine(geom_metric)
                except Exception:
                    try:
                        union_geom = QgsGeometry.unaryUnion([union_geom, geom_metric])
                    except Exception:
                        pass

        return feats_metric, union_geom

    def _geom_bbox_str(self, geom: QgsGeometry) -> str:
        rect = geom.boundingBox()
        return f"{rect.xMinimum()},{rect.yMinimum()},{rect.xMaximum()},{rect.yMaximum()}"

    def _rect_to_qgsrect(self, geom: QgsGeometry) -> QgsRectangle:
        return geom.boundingBox()

    def _safe_centroid_point(self, geom: QgsGeometry):
        """Return centroid point safely."""
        try:
            if geom and not geom.isEmpty():
                c = geom.centroid()
                if c and not c.isEmpty():
                    return c.asPoint()
        except Exception:
            pass
        return None

    def _transform_point(self, pt, src_crs: QgsCoordinateReferenceSystem,
                         dst_crs: QgsCoordinateReferenceSystem):
        """Transform a point between CRS safely."""
        if pt is None:
            return None
        try:
            if not src_crs.isValid() or not dst_crs.isValid() or src_crs == dst_crs:
                return pt
            tr = QgsCoordinateTransform(src_crs, dst_crs, QgsProject.instance())
            return tr.transform(pt)
        except Exception as e:
            self.log(f"Point transform failed: {e}")
            return pt


    def _get_layer_union_centroid_in_wgs84(self, layer: QgsVectorLayer):
        """
        Build union geometry from layer, then return centroid in EPSG:4326.
        """
        try:
            src_crs = layer.crs()
            if not src_crs.isValid():
                return None

            union_geom = None
            wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")

            for f in layer.getFeatures():
                geom = f.geometry()
                if not geom or geom.isEmpty():
                    continue

                g = QgsGeometry(geom)
                if src_crs != wgs84:
                    try:
                        tr = QgsCoordinateTransform(src_crs, wgs84, QgsProject.instance())
                        ok = g.transform(tr)
                        if _transform_failed(ok):
                            self.log("Geometry transform to WGS84 returned non-zero status.")
                    except Exception as e:
                        self.log(f"Geometry transform to WGS84 failed: {e}")
                        continue

                if union_geom is None:
                    union_geom = QgsGeometry(g)
                else:
                    try:
                        union_geom = union_geom.combine(g)
                    except Exception:
                        try:
                            union_geom = QgsGeometry.unaryUnion([union_geom, g])
                        except Exception:
                            pass

            if not union_geom or union_geom.isEmpty():
                return None

            c = union_geom.centroid()
            if not c or c.isEmpty():
                return None

            return c.asPoint()
        except Exception as e:
            self.log(f"Failed to compute centroid in WGS84: {e}")
            return None

    def _get_combined_centroid_in_wgs84(self, app_layer: QgsVectorLayer, api_layer: QgsVectorLayer = None):
        """
        Return centroid of combined Application Area + API layer in EPSG:4326.
        If api_layer is None, fall back to app_layer centroid.
        """
        try:
            wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
            combined_geom = None

            def add_layer_to_union(layer):
                nonlocal combined_geom
                if layer is None:
                    return

                src_crs = layer.crs()
                if not src_crs.isValid():
                    return

                for f in layer.getFeatures():
                    geom = f.geometry()
                    if not geom or geom.isEmpty():
                        continue

                    g = QgsGeometry(geom)
                    if src_crs != wgs84:
                        try:
                            tr = QgsCoordinateTransform(src_crs, wgs84, QgsProject.instance())
                            ok = g.transform(tr)
                            if ok != 0:
                                self.log("Geometry transform to WGS84 returned non-zero status.")
                        except Exception as e:
                            self.log(f"Geometry transform to WGS84 failed: {e}")
                            continue

                    if combined_geom is None:
                        combined_geom = QgsGeometry(g)
                    else:
                        try:
                            combined_geom = combined_geom.combine(g)
                        except Exception:
                            try:
                                combined_geom = QgsGeometry.unaryUnion([combined_geom, g])
                            except Exception:
                                pass

            add_layer_to_union(app_layer)
            add_layer_to_union(api_layer)

            if combined_geom is None or combined_geom.isEmpty():
                return None

            c = combined_geom.centroid()
            if c is None or c.isEmpty():
                return None

            return c.asPoint()

        except Exception as e:
            self.log(f"Failed to compute combined centroid in WGS84: {e}")
            return None

    def _is_cross_border_context(self, pt_wgs84) -> bool:
        """
        Heuristic for ROI / NI border context.
        Covers the broad Ireland / Northern Ireland border-adjacent region.
        """
        if pt_wgs84 is None:
            return False

        lon = pt_wgs84.x()
        lat = pt_wgs84.y()

        # Broad border-relevant envelope
        return (-8.8 <= lon <= -5.0) and (53.0 <= lat <= 55.6)

    def _is_likely_ireland_context(self, pt_wgs84) -> bool:
        """
        Heuristic for Republic of Ireland-dominant context.
        """
        if pt_wgs84 is None:
            return False

        lon = pt_wgs84.x()
        lat = pt_wgs84.y()

        return (-10.8 <= lon <= -5.3) and (51.3 <= lat <= 55.5)

    def _is_likely_british_context(self, pt_wgs84) -> bool:
        """
        Heuristic for UK / Northern Ireland-dominant context.
        """
        if pt_wgs84 is None:
            return False

        lon = pt_wgs84.x()
        lat = pt_wgs84.y()

        return (-8.5 <= lon <= 2.5) and (49.5 <= lat <= 59.5)

    def _choose_utm_for_lon(self, lon: float):
        """
        Choose UTM zone by longitude for Ireland context.
        """
        if lon > -8.0:
            return QgsCoordinateReferenceSystem("EPSG:32630")  # UTM Zone 30N
        return QgsCoordinateReferenceSystem("EPSG:32629")      # UTM Zone 29N

    def _choose_metric_crs_cross_border(self, app_layer: QgsVectorLayer, api_layer: QgsVectorLayer = None):
        """Select a metric CRS by sampling ellipsoidal distance distortion.

        Uses layer extent samples (not an unbounded remote feature download).
        The selection is a projection-distortion estimate, not a guarantee of
        exact geodesic distances for every feature pair.
        """
        if self.manual_metric_crs_authid:
            manual = QgsCoordinateReferenceSystem(self.manual_metric_crs_authid)
            if manual.isValid() and manual.isGeographic() is False:
                self.log(f"Manual metric CRS override: {manual.authid()}")
                return manual

        geographic = QgsCoordinateReferenceSystem("EPSG:4326")
        context = QgsProject.instance().transformContext()
        samples = []
        for layer in (app_layer, api_layer):
            if layer is None or not layer.isValid() or not layer.crs().isValid():
                continue
            try:
                extent = layer.extent()
                if extent.isEmpty():
                    continue
                transformer = QgsCoordinateTransform(layer.crs(), geographic, context)
                xmin, xmax = extent.xMinimum(), extent.xMaximum()
                ymin, ymax = extent.yMinimum(), extent.yMaximum()
                coords = [(xmin, ymin), (xmax, ymin), (xmin, ymax),
                          (xmax, ymax), ((xmin+xmax)/2, (ymin+ymax)/2)]
                for x, y in coords:
                    point = transformer.transform(QgsPointXY(x, y))
                    if -180 <= point.x() <= 180 and -90 <= point.y() <= 90:
                        samples.append(point)
            except Exception as exc:
                self.log(f"CRS sample warning ({layer.name()}): {exc}")

        if not samples:
            self.log("CRS sampling unavailable; using EPSG:2157 (Irish Transverse Mercator).")
            return QgsCoordinateReferenceSystem("EPSG:2157")

        # Sample modest ground distances in both east-west and north-south directions.
        # At Irish latitudes, 0.05 degrees is a few kilometres.
        geod = QgsDistanceArea()
        geod.setSourceCrs(geographic, context)
        geod.setEllipsoid("WGS84")
        candidate_ids = ("EPSG:2157", "EPSG:29903", "EPSG:27700",
                         "EPSG:32629", "EPSG:32630")
        ranked = []
        for authid in candidate_ids:
            crs = QgsCoordinateReferenceSystem(authid)
            if not crs.isValid():
                continue
            try:
                transform = QgsCoordinateTransform(geographic, crs, context)
                errors = []
                for point in samples:
                    for dx, dy in ((0.05, 0), (0, 0.05)):
                        other = QgsPointXY(point.x() + dx, point.y() + dy)
                        ground = geod.measureLine(point, other)
                        if ground <= 0:
                            continue
                        a, b = transform.transform(point), transform.transform(other)
                        projected = math.hypot(b.x()-a.x(), b.y()-a.y())
                        errors.append(abs(projected/ground-1))
                if errors:
                    ranked.append((max(errors), sum(errors)/len(errors), authid))
            except Exception as exc:
                self.log(f"CRS candidate {authid} unavailable: {exc}")
        if not ranked:
            self.log("CRS distortion comparison failed; using EPSG:2157.")
            return QgsCoordinateReferenceSystem("EPSG:2157")
        worst, mean, best = min(ranked)
        self.log(f"Automatic CRS: {best}; sampled maximum scale error {worst*100:.4f}%, mean {mean*100:.4f}%")
        if worst > 0.01:
            self.log("WARNING: sampled projection distortion exceeds 1%; inspect analysis extent.")
        return QgsCoordinateReferenceSystem(best)

    # -------------------------------
    # Geometry drawing helpers
    # -------------------------------
    def _plot_qgs_geometry(self, ax, geom: QgsGeometry, color="blue", linewidth=1.5,
                           alpha=1.0, marker=None, markersize=50, label=None):
        """Legacy geometry plotting helper retained for compatibility; not used by native QGIS output."""
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

    def _spatial_relationship(self, app_geom: QgsGeometry, target_geom: QgsGeometry, dist: float) -> str:
        """Describe topological relation, with zero-distance bearing undefined."""
        if app_geom is None or target_geom is None or app_geom.isEmpty() or target_geom.isEmpty():
            return "Undefined"
        try:
            if target_geom.disjoint(app_geom):
                return "Outside"
            if target_geom.equals(app_geom):
                return "Equal"
            if target_geom.contains(app_geom):
                return "Inside"  # Application Area inside target
            if app_geom.contains(target_geom):
                return "Contains"  # Application Area contains target
            if target_geom.touches(app_geom):
                return "Touching"
            if target_geom.overlaps(app_geom):
                return "Overlapping"
            if target_geom.intersects(app_geom):
                return "Intersecting"
            return "Outside"
        except Exception as exc:
            self.log(f"Spatial relationship could not be determined: {exc}")
            return "Undefined"

    # -------------------------------
    # Provider / WFS / ArcGIS metadata
    # -------------------------------
    def _provider_label(self) -> str:
        if hasattr(self, "combo_provider"):
            label = (self.combo_provider.currentText() or "").strip()
            if label:
                return label
        return self.API_PROVIDERS[self.current_provider_key]["label"]

    def _provider_key(self) -> str:
        label = self._provider_label()
        return self.provider_label_to_key.get(label, self.current_provider_key)

    def _provider_cfg(self) -> dict:
        return self.API_PROVIDERS.get(self._provider_key(), self.API_PROVIDERS["epa_ie"])

    def _current_attribution(self) -> str:
        return self._provider_cfg().get("attribution", "")

    def _normalize_arcgis_url(self, url: str) -> str:
        url = (url or "").strip().strip("'\"")
        if url.startswith("url="):
            url = url.replace("url=", "", 1).strip("'\"")
        url = url.split("?")[0].rstrip("/")
        if url.lower().endswith("/query"):
            url = url.rsplit("/", 1)[0]
        return url

    def _arcgis_layer_query_url(self, service_url: str) -> str:
        service_url = self._normalize_arcgis_url(service_url)
        if service_url.lower().endswith(("/featureserver", "/mapserver")):
            return service_url + "/0/query"
        if "/FeatureServer/" in service_url or "/MapServer/" in service_url:
            return service_url + "/query"
        return service_url + "/query"

    def _arcgis_qgis_layer_url(self, service_url: str) -> str:
        """
        Return a concrete ArcGIS layer URL suitable for QgsVectorLayer.

        ArcGIS catalogue searches often return a service root such as
        .../FeatureServer or .../MapServer. QGIS usually needs a concrete
        sub-layer URL such as .../FeatureServer/0. For display in the Layers
        panel, use the first advertised layer when a root URL is selected.
        """
        service_url = self._normalize_arcgis_url(service_url)
        if not service_url:
            return service_url

        # Already points at a concrete ArcGIS layer, e.g. /FeatureServer/3.
        if re.search(r"/(FeatureServer|MapServer)/\d+$", service_url, re.IGNORECASE):
            return service_url

        # Service root: discover the first layer id. If the metadata request
        # fails, fall back to /0, which is the common ArcGIS default.
        if service_url.lower().endswith(("/featureserver", "/mapserver")):
            try:
                data = self._read_json(service_url, params={"f": "json"}, timeout=self.timeout_meta)
                layers = data.get("layers") or []
                if layers:
                    layer_id = layers[0].get("id", 0)
                    return service_url.rstrip("/") + f"/{layer_id}"
            except Exception as e:
                self.log(f"Could not inspect ArcGIS service layers; falling back to layer 0: {e}")
            return service_url.rstrip("/") + "/0"

        return service_url

    def _read_json(self, url: str, params: dict = None, timeout=None):
        params = params or {}
        timeout = timeout or self.timeout_meta
        r = self.http.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def _discover_arcgis_hub_org_id(self, hub_url: str):
        """Try to discover the ArcGIS Online orgId behind an ArcGIS Hub site."""
        try:
            r = self.http.get(hub_url, timeout=self.timeout_meta)
            r.raise_for_status()
            html = r.text
            patterns = [
                r'"orgId"\s*:\s*"([A-Za-z0-9]+)"',
                r'"orgid"\s*:\s*"([A-Za-z0-9]+)"',
                r'orgId\s*=\s*"([A-Za-z0-9]+)"',
                r'orgid\s*=\s*"([A-Za-z0-9]+)"',
            ]
            for pattern in patterns:
                m = re.search(pattern, html)
                if m:
                    return m.group(1)
        except Exception as e:
            self.log(f"Failed to discover ArcGIS Hub orgId: {e}")
        return None

    def _discover_arcgis_hub_layers(self, provider_key: str, cfg: dict) -> dict:
        """
        Discover public ArcGIS Feature Service / Map Service items from an ArcGIS Hub site.
        The returned dict is display_name -> layer_info.
        """
        cache_key = provider_key
        if cache_key in self.GLOBAL_PROVIDER_LAYER_CACHE:
            return dict(self.GLOBAL_PROVIDER_LAYER_CACHE[cache_key])

        hub_url = cfg.get("hub_url", "").rstrip("/")
        q_text = cfg.get("search_query", "")
        layers = {}

        # Preferred route: discover orgId then search ArcGIS Online public items.
        org_id = self._discover_arcgis_hub_org_id(hub_url)
        search_urls = []
        if org_id:
            search_urls.append((
                "https://www.arcgis.com/sharing/rest/search",
                {
                    "f": "json",
                    "num": 100,
                    "sortField": "modified",
                    "sortOrder": "desc",
                    "q": f'orgid:{org_id} AND ({q_text}) AND (type:"Feature Service" OR type:"Map Service")',
                },
            ))

        # Fallback route: ArcGIS Hub search API. Hub deployments differ, so parse defensively.
        search_urls.append((
            hub_url + "/api/search/v1",
            {
                "q": q_text or "environment",
                "filter[content]": "dataset",
                "filter[access]": "public",
                "limit": 100,
            },
        ))

        for url, params in search_urls:
            try:
                data = self._read_json(url, params=params, timeout=self.timeout_meta)
                results = data.get("results") or data.get("data") or []
                for item in results:
                    attrs = item.get("attributes", item) if isinstance(item, dict) else {}
                    title = attrs.get("title") or attrs.get("name") or item.get("title") if isinstance(item, dict) else None
                    item_type = (attrs.get("type") or item.get("type") or "").lower() if isinstance(item, dict) else ""
                    service_url = attrs.get("url") or item.get("url") if isinstance(item, dict) else None
                    if not service_url:
                        links = attrs.get("links") or item.get("links") or {} if isinstance(item, dict) else {}
                        service_url = links.get("self") or links.get("api") if isinstance(links, dict) else None
                    if not title or not service_url:
                        continue
                    if "featureserver" not in service_url.lower() and "mapserver" not in service_url.lower():
                        if "feature service" not in item_type and "map service" not in item_type:
                            continue
                    service_url = self._normalize_arcgis_url(service_url)
                    display = f"{title} ({cfg['label']})"
                    layers[display] = {
                        "provider_key": provider_key,
                        "kind": "arcgis",
                        "service_url": service_url,
                        "title": title,
                        "source_label": cfg["label"],
                    }
            except Exception as e:
                self.log(f"ArcGIS Hub discovery attempt failed for {cfg.get('label')}: {e}")

            if layers:
                break

        self.GLOBAL_PROVIDER_LAYER_CACHE[cache_key] = dict(layers)
        return layers

    def _discover_arcgis_rest_root_layers(self, provider_key: str, cfg: dict) -> dict:
        """Discover services from an ArcGIS REST services root or folder."""
        cache_key = provider_key
        if cache_key in self.GLOBAL_PROVIDER_LAYER_CACHE:
            return dict(self.GLOBAL_PROVIDER_LAYER_CACHE[cache_key])

        root = cfg.get("rest_root", "").rstrip("/")
        layers = {}

        def read_folder(folder_url: str, prefix: str = ""):
            try:
                data = self._read_json(folder_url, params={"f": "json"}, timeout=self.timeout_meta)
                for service in data.get("services", []):
                    name = service.get("name", "")
                    stype = service.get("type", "")
                    if stype not in ("MapServer", "FeatureServer"):
                        continue
                    service_name = name.split("/")[-1]
                    service_url = f"{root}/{name}/{stype}" if prefix == "" else f"{folder_url.rstrip('/')}/{service_name}/{stype}"
                    display = f"{service_name} ({cfg['label']})"
                    layers[display] = {
                        "provider_key": provider_key,
                        "kind": "arcgis",
                        "service_url": service_url,
                        "title": service_name,
                        "source_label": cfg["label"],
                    }
                for folder in data.get("folders", [])[:20]:
                    read_folder(root + "/" + folder, folder)
            except Exception as e:
                self.log(f"ArcGIS REST service discovery failed: {e}")

        read_folder(root)
        self.GLOBAL_PROVIDER_LAYER_CACHE[cache_key] = dict(layers)
        return layers

    def _discover_ea_wfs_urls(self) -> list:
        """Discover public EA WFS resources from the UK open-data catalogue.

        This is a bounded catalogue search, not a claim to enumerate every EA dataset.
        """
        urls = [self.API_PROVIDERS["ea_arcgis"]["url"]]
        seen = set(urls)
        endpoint = self.API_PROVIDERS["ea_arcgis"]["catalogue_api"]
        # Bound catalogue requests to avoid freezing the QGIS UI indefinitely.
        for start in range(0, 500, 100):
            try:
                payload = self._read_json(endpoint, params={
                    "q": '"Environment Agency"', "rows": 100, "start": start
                }, timeout=self.timeout_meta)
                if not payload.get("success"):
                    break
                result = payload.get("result") or {}
                packages = result.get("results") or []
                for package in packages:
                    org = (package.get("organization") or {}).get("title", "")
                    publisher = str(package.get("author") or "")
                    if "environment agency" not in (org + " " + publisher).lower():
                        continue
                    for resource in package.get("resources") or []:
                        url = (resource.get("url") or "").strip()
                        label = " ".join(str(resource.get(k) or "") for k in ("name", "format", "description"))
                        if (url.startswith("https://") and
                                ("/wfs" in url.lower() or "service=wfs" in url.lower() or
                                 "wfs" in label.lower()) and url not in seen):
                            seen.add(url)
                            urls.append(url)
                if start + len(packages) >= int(result.get("count", 0)) or not packages:
                    break
            except Exception as exc:
                self.log(f"EA catalogue discovery interrupted: {exc}")
                break
        self.log(f"EA catalogue: {len(urls)} candidate WFS service URLs (including fallback).")
        return urls

    def _discover_wfs_layers(self, provider_key: str, cfg: dict) -> dict:
        cache_key = provider_key
        if cache_key in self.GLOBAL_PROVIDER_LAYER_CACHE:
            return dict(self.GLOBAL_PROVIDER_LAYER_CACHE[cache_key])

        layers = {}
        urls = ([cfg["url"]] if provider_key != "ea_arcgis"
                else self._discover_ea_wfs_urls())
        for url in urls:
            try:
                wfs_version = cfg.get("wfs_version", self.WFS_VERSION)
                wfs = WebFeatureService(url=url, version=wfs_version,
                                        timeout=self.timeout_meta)
                for layer_name, layer_obj in wfs.contents.items():
                    type_name = (layer_name if ":" in layer_name or provider_key != "epa_ie"
                                 else f"EPA:{layer_name}")
                    title = getattr(layer_obj, "title", layer_name)
                    display_name = f"{title} ({type_name})"
                    # Avoid overwriting identically named layers from other services.
                    if display_name in layers and layers[display_name]["wfs_url"] != url:
                        display_name = f"{display_name} [{len(layers) + 1}]"
                    layers[display_name] = {
                        "provider_key": provider_key, "kind": "wfs",
                        "type_name": type_name, "title": title,
                        "source_label": cfg["label"], "wfs_url": url,
                        "wfs_version": wfs_version,
                    }
            except Exception as exc:
                self.log(f"WFS discovery failed for {url}: {exc}")
        self.log(f"Loaded {len(layers)} WFS layers from {cfg['label']}.")
        if layers:
            self.GLOBAL_PROVIDER_LAYER_CACHE[cache_key] = dict(layers)
        return layers

    def _discover_provider_layers(self) -> dict:
        provider_key = self._provider_key()
        cfg = self.API_PROVIDERS[provider_key]
        kind = cfg.get("kind")

        # Fast default: EPA WFS is loaded automatically, because this matches the
        # older fast plugin. ArcGIS Hub / REST catalogues can be slow and may make
        # QGIS appear frozen, so skip them at startup and load them only
        # when the user selects that Data Source or clicks Refresh.
        if kind == "wfs":
            return self._discover_wfs_layers(provider_key, cfg)

        if not self.allow_remote_catalog_discovery:
            self.log(
                f"{cfg.get('label', 'Provider')} catalogue not auto-loaded. "
                "Select this Data Source or click Refresh to load this remote catalogue."
            )
            return {}

        if kind == "arcgis_hub":
            return self._discover_arcgis_hub_layers(provider_key, cfg)
        if kind == "arcgis_rest_root":
            return self._discover_arcgis_rest_root_layers(provider_key, cfg)
        return {}

    def _ensure_selected_api_layer_loaded(self) -> None:
        """
        Fast API-selection handler.

        This follows the lighter approach from the second script: avoid loading
        heavy remote ArcGIS catalogue layers when the combo box selection changes.
        ArcGIS catalogue entries can be queried directly in run_prestep() using a
        bbox REST request, so forcing a full QGIS provider load here only slows
        down the UI.

        WFS catalogue entries are still loaded on selection because QGIS provides
        dependable field metadata after the WFS provider validates the layer.
        Already-loaded QGIS API layers are simply reused.
        """
        api_name = (self.combo_api.currentText() or "").strip()
        info = self.api_catalog.get(api_name)
        if not info:
            return

        # Reuse already-loaded layers first.
        for lyr in QgsProject.instance().mapLayers().values():
            if not isinstance(lyr, QgsVectorLayer):
                continue
            src = lyr.source() or ""
            if info.get("type_name") and info["type_name"] in src:
                if lyr not in self.api_layers:
                    self.api_layers.append(lyr)
                return
            if info.get("service_url") and info["service_url"] in src:
                if lyr not in self.api_layers:
                    self.api_layers.append(lyr)
                return

        # For ArcGIS catalogue layers, load a concrete layer into QGIS when the
        # user selects it, so DAERA / Natural England / Environment Agency behave
        # like EPA WFS in the Layers panel and map canvas.
        if info.get("kind") == "arcgis" and self.LAZY_LOAD_CATALOG_ARCGIS_ON_SELECTION:
            self.log("ArcGIS catalogue layer selected; lazy display loading is enabled, so no QGIS layer was added.")
            return

        if info.get("kind") == "wfs":
            uri = QgsDataSourceUri()
            uri.setParam("url", info.get("wfs_url") or self._provider_cfg().get("url"))
            uri.setParam("typename", info["type_name"])
            uri.setParam("version", info.get("wfs_version", self.WFS_VERSION))
            layer = QgsVectorLayer(uri.uri(), api_name, "WFS")
        elif info.get("kind") == "arcgis":
            service_url = info.get("service_url", "")
            qgis_layer_url = self._arcgis_qgis_layer_url(service_url)
            provider_name = "arcgisfeatureserver" if "featureserver" in qgis_layer_url.lower() else "arcgismapserver"
            self.log(f"Loading ArcGIS layer into QGIS: {qgis_layer_url}")
            layer = QgsVectorLayer(qgis_layer_url, api_name, provider_name)
        else:
            return

        if not layer.isValid():
            msg = f"Failed to load API layer into QGIS: {api_name}"
            self.log(msg)
            try:
                QgsMessageLog.logMessage(msg, "NearestAnalysis", Qgis.Warning)
            except Exception:
                pass
            return

        QgsProject.instance().addMapLayer(layer)
        self.log(f"Loaded API layer into QGIS: {layer.name()}")
        self.api_layers.append(layer)

    def _get_selected_api_layer(self):
        api_name = (self.combo_api.currentText() or "").strip()
        if not api_name:
            return None

        for lyr in self.api_layers:
            if lyr.name() == api_name:
                return lyr

        info = self.api_catalog.get(api_name, {})
        for lyr in QgsProject.instance().mapLayers().values():
            if isinstance(lyr, QgsVectorLayer):
                src = lyr.source() or ""
                if lyr.name() == api_name:
                    return lyr
                if info.get("type_name") and info["type_name"] in src:
                    return lyr
                if info.get("service_url") and info["service_url"] in src:
                    return lyr
        return None

    def _ea_cache_file(self):
        base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        folder = os.path.join(base, "ireland_ni_crossborder_analysis")
        os.makedirs(folder, exist_ok=True)
        return __import__("pathlib").Path(folder) / "ea_wfs_catalogue_v3.json"

    def _start_ea_catalogue(self, force=False):
        import datetime
        self._ea_generation += 1
        generation = self._ea_generation
        if not force and "ea_arcgis" not in self.GLOBAL_PROVIDER_LAYER_CACHE:
            try:
                saved = json.loads(self._ea_cache_file().read_text(encoding="utf-8"))
                if time.time() - saved["timestamp"] < 7 * 86400 and saved.get("layers"):
                    self.GLOBAL_PROVIDER_LAYER_CACHE["ea_arcgis"] = saved["layers"]
                    self.log(f"EA: loaded {len(saved['layers'])} cached layers (up to 7 days old).")
            except (OSError, ValueError, KeyError, TypeError):
                pass
        if not force and "ea_arcgis" in self.GLOBAL_PROVIDER_LAYER_CACHE:
            self.populate_layers()
            return
        # Display existing catalogue while refreshing; avoid blocking GUI.
        self.populate_layers(skip_remote_ea=True)
        self.log("EA: searching WFS catalogue in background. QGIS remains usable.")
        if self._ea_worker is not None and self._ea_worker.isRunning():
            self._ea_worker.requestInterruption()
            # Do not destroy a running QThread. Keep a reference until it finishes.
            if not hasattr(self, "_ea_retired_workers"):
                self._ea_retired_workers = []
            old = self._ea_worker
            self._ea_retired_workers.append(old)
            old.finished.connect(lambda w=old: self._ea_retired_workers.remove(w) if w in self._ea_retired_workers else None)
        worker = EACatalogueWorker(self.API_PROVIDERS["ea_arcgis"], self)
        self._ea_worker = worker
        def done(layers, summary):
            if generation != self._ea_generation:
                return
            if layers:
                self.GLOBAL_PROVIDER_LAYER_CACHE["ea_arcgis"] = layers
                try:
                    self._ea_cache_file().write_text(json.dumps({"timestamp": time.time(), "layers": layers}), encoding="utf-8")
                except OSError as exc:
                    self.log(f"EA cache write failed: {exc}")
            self.log("EA background discovery finished: " + summary)
            if self._provider_key() == "ea_arcgis":
                self.populate_layers(skip_remote_ea=True)
        worker.completed.connect(done)
        worker.start()

    def on_provider_changed(self) -> None:
        self.current_provider_key = self._provider_key()

        # Keep EPA fast by auto-loading it as before.
        # For non-EPA providers, allow one catalogue discovery when the user
        # explicitly selects that Data Source; otherwise Available Layer stays
        # empty, because the fast-default version intentionally skipped remote
        # ArcGIS Hub / REST catalogue scans at startup.
        self.allow_remote_catalog_discovery = (self.current_provider_key != "epa_ie")

        self.api_pre_filtered.clear()
        self.last_analysis_signature = None

        if self.allow_remote_catalog_discovery:
            self.log(f"Loading catalogue for {self._provider_label()}...")

        if self.current_provider_key == "ea_arcgis":
            self._start_ea_catalogue()
        else:
            self._ea_generation += 1
            self.populate_layers()

    def on_api_selection_changed(self) -> None:
        try:
            self._ensure_selected_api_layer_loaded()
        except Exception as e:
            self.log(f"Auto-load API layer failed: {e}")
        self.update_fields_for_api()

    # -------------------------------
    # Populate layer lists
    # -------------------------------
    def populate_layers(self, skip_remote_ea=False) -> None:
        current_app = self.combo_app.currentText() if hasattr(self, "combo_app") else ""
        current_api = self.combo_api.currentText() if hasattr(self, "combo_api") else ""

        self.combo_app.clear()
        self.combo_api.clear()
        self.shp_layers = []
        self.api_layers = []
        self.wfs_layers_info.clear()
        self.api_catalog.clear()

        seen_app_names = set()
        seen_api_names = set()

        for layer in QgsProject.instance().mapLayers().values():
            if not isinstance(layer, QgsVectorLayer):
                continue

            provider = (layer.providerType() or "").lower()
            name = layer.name() or ""
            src = (layer.source() or "")
            if provider == "ogr" or name.lower().endswith(".shp"):
                if name not in seen_app_names:
                    self.combo_app.addItem(name)
                    self.shp_layers.append(layer)
                    seen_app_names.add(name)

            # Do not mix every ArcGIS layer already loaded in QGIS into
            # the selected provider's catalogue. Available Layer must show
            # only entries discovered for the selected Data Source.
            # Existing layers may still be reused by the API loading logic
            # when their service URL matches a selected catalogue entry.

        # Add provider catalogue layers. EPA loads automatically. Non-EPA providers
        # load when selected or when Refresh is clicked.
        provider_layers = (dict(self.GLOBAL_PROVIDER_LAYER_CACHE.get("ea_arcgis", {}))
                           if skip_remote_ea and self._provider_key() == "ea_arcgis"
                           else self._discover_provider_layers())
        for display_name, info in provider_layers.items():
            if display_name not in seen_api_names:
                self.combo_api.addItem(display_name)
                seen_api_names.add(display_name)
            self.api_catalog[display_name] = info
            if info.get("kind") == "wfs":
                self.wfs_layers_info[display_name] = info.get("type_name")

        if not provider_layers and self._provider_key() != "epa_ie":
            self.log(
                "No catalogue layers were loaded for this Data Source. "
                "Click Refresh to try again, or check network access to the provider catalogue."
            )

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

        if current_app:
            idx = self.combo_app.findText(current_app)
            if idx >= 0:
                self.combo_app.setCurrentIndex(idx)

        if current_api:
            idx = self.combo_api.findText(current_api)
            if idx >= 0:
                self.combo_api.setCurrentIndex(idx)

        try:
            self.on_api_selection_changed()
        except Exception:
            pass

    # -------------------------------
    # Update fields for API
    # -------------------------------
    def update_fields_for_api(self) -> None:
        """
        Populate the export-field list for the currently selected API layer.

        Security note:
        This implementation intentionally avoids WFS DescribeFeatureType XML parsing.
        QGIS plugin repository security scanning blocks parsing untrusted remote XML
        with the standard library XML parser. For WFS layers, fields are read from the QgsVectorLayer which
        QGIS has already loaded/validated for the selected catalogue entry.
        """
        if not hasattr(self, "fields_list"):
            return

        self.fields_list.clear()
        api_name = self.combo_api.currentText()
        if not api_name:
            return

        info = self.api_catalog.get(api_name, {})

        if info.get("kind") == "wfs":
            type_name = info.get("type_name")
            cache_key = (info.get("provider_key"), type_name)

            if cache_key in self.wfs_fields_cache:
                for field_name in self.wfs_fields_cache[cache_key]:
                    self.fields_list.addItem(field_name)
                self.log(f"Loaded {len(self.wfs_fields_cache[cache_key])} WFS fields (cached).")
                return

            fields = []
            try:
                # The selected WFS layer should already have been loaded by
                # on_api_selection_changed() -> _ensure_selected_api_layer_loaded().
                layer = self._get_selected_api_layer()

                if layer is not None and layer.isValid():
                    fields = [
                        field.name()
                        for field in layer.fields()
                        if field.name() and not field.name().lower().endswith("geom")
                    ]

                # Fallback: find a matching loaded QGIS layer by typename in source URI.
                if not fields and type_name:
                    for lyr in QgsProject.instance().mapLayers().values():
                        if not isinstance(lyr, QgsVectorLayer):
                            continue
                        src = lyr.source() or ""
                        if type_name in src and lyr.isValid():
                            fields = [
                                field.name()
                                for field in lyr.fields()
                                if field.name() and not field.name().lower().endswith("geom")
                            ]
                            break

                self.wfs_fields_cache[cache_key] = fields
                for field_name in fields:
                    self.fields_list.addItem(field_name)

                if fields:
                    self.log(f"Loaded {len(fields)} WFS fields from QGIS layer.")
                else:
                    self.log("No WFS fields could be read from the loaded QGIS layer.")
            except Exception as e:
                self.log(f"Failed to load WFS fields from QGIS layer: {e}")
            return

        try:
            service_url = info.get("service_url")
            if not service_url:
                for lyr in self.api_layers:
                    if lyr.name() == api_name:
                        service_url = self._normalize_arcgis_url(lyr.source())
                        break
            if not service_url:
                return

            service_url = self._normalize_arcgis_url(service_url)
            json_url = service_url + "?f=json"
            if json_url in self.arcgis_fields_cache:
                for f in self.arcgis_fields_cache[json_url]:
                    self.fields_list.addItem(f)
                self.log(f"Loaded {len(self.arcgis_fields_cache[json_url])} ArcGIS REST fields (cached).")
                return

            data = self._read_json(service_url, params={"f": "json"}, timeout=self.timeout_meta)
            fields = [f["name"] for f in data.get("fields", []) if "name" in f]

            # If this is a service root, inspect layer 0 by default for fields.
            if not fields and data.get("layers"):
                layer_id = data["layers"][0].get("id", 0)
                layer_url = service_url.rstrip("/") + f"/{layer_id}"
                data = self._read_json(layer_url, params={"f": "json"}, timeout=self.timeout_meta)
                fields = [f["name"] for f in data.get("fields", []) if "name" in f]
                json_url = layer_url + "?f=json"

            self.arcgis_fields_cache[json_url] = fields
            for f in fields:
                self.fields_list.addItem(f)
            self.log(f"Loaded {len(fields)} ArcGIS REST fields.")
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

    def _download_wfs_layer(self, info: dict, buffer_geom_metric: QgsGeometry) -> QgsVectorLayer:
        try:
            rect = buffer_geom_metric.boundingBox()
            bbox = f"{rect.xMinimum()},{rect.yMinimum()},{rect.xMaximum()},{rect.yMaximum()},{self.metric_crs.authid()}"
            params = {
                "service": "WFS",
                "version": info.get("wfs_version", self.WFS_VERSION),
                "request": "GetFeature",
                ("typeNames" if info.get("wfs_version", self.WFS_VERSION).startswith("2.")
                 else "typename"): info.get("type_name"),
                "outputFormat": "application/json",
                "srsName": self.metric_crs.authid(),
                "bbox": bbox,
            }
            r = self.http.get(info.get("wfs_url") or self._provider_cfg().get("url"), params=params, timeout=self.timeout_data)
            r.raise_for_status()
            lyr = self._load_geojson_text_as_layer(r.text, info.get("type_name", "wfs_api"))
            if lyr and lyr.isValid():
                return lyr
            self.log("WFS GeoJSON layer invalid.")
            return None
        except Exception as e:
            self.log(f"WFS download failed: {e}")
            return None

    def _download_arcgis_layer(self, info_or_source, buffer_geom_metric: QgsGeometry) -> QgsVectorLayer:
        try:
            if isinstance(info_or_source, dict):
                source_str = info_or_source.get("service_url", "")
                layer_name = info_or_source.get("title", "arcgis_api")
            else:
                source_str = str(info_or_source)
                layer_name = "arcgis_api"

            source_str = self._normalize_arcgis_url(source_str)
            if not ("/FeatureServer" in source_str or "/MapServer" in source_str):
                self.log("ArcGIS source is not FeatureServer/MapServer.")
                return None

            query_url = self._arcgis_layer_query_url(source_str)
            rect = buffer_geom_metric.boundingBox()
            bbox_str = f"{rect.xMinimum()},{rect.yMinimum()},{rect.xMaximum()},{rect.yMaximum()}"
            epsg_code = self.metric_crs.postgisSrid() if self.metric_crs.isValid() else 29903

            params = {
                "where": "1=1",
                "outFields": "*",
                "geometry": bbox_str,
                "geometryType": "esriGeometryEnvelope",
                "inSR": epsg_code,
                "spatialRel": "esriSpatialRelIntersects",
                "outSR": epsg_code,
                "f": "geojson",
            }
            r = self.http.get(query_url, params=params, timeout=self.timeout_data)
            r.raise_for_status()
            lyr = self._load_geojson_text_as_layer(r.text, layer_name)
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
    def run_prestep(self, app_union_metric: QgsGeometry, buffer_geom_metric: QgsGeometry) -> None:
        """Download API features intersecting buffer and cache as list of QgsFeature in active metric CRS."""
        try:
            api_index = self.combo_api.currentIndex()
            if api_index < 0:
                return

            api_name = self.combo_api.currentText()
            app_layer = self.shp_layers[self.combo_app.currentIndex()]
            app_uri = app_layer.dataProvider().dataSourceUri()
            provider_key = self._provider_key()
            cache_key = self._make_analysis_signature(
                f"{provider_key}:{api_name}",
                app_uri,
                self.buffer_distance_m,
                self.metric_crs.authid(),
            )

            if cache_key in self.api_pre_filtered:
                self.log("Using cached preprocessed data.")
                return

            info = self.api_catalog.get(api_name, {})
            remote_layer = None
            t_download = time.perf_counter()

            if info.get("kind") == "wfs":
                remote_layer = self._download_wfs_layer(info, buffer_geom_metric)
            elif info.get("kind") == "arcgis":
                remote_layer = self._download_arcgis_layer(info, buffer_geom_metric)

            if remote_layer is None:
                source_str = None
                for lyr in self.api_layers:
                    if lyr.name() == api_name:
                        source_str = lyr.source()
                        break
                if source_str:
                    remote_layer = self._download_arcgis_layer(source_str, buffer_geom_metric)

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
                geom_metric = self._transform_geometry(geom, remote_crs, self.metric_crs)
                if geom_metric.isEmpty():
                    continue
                if not geom_metric.intersects(buffer_geom_metric):
                    continue
                new_f = QgsFeature(f)
                new_f.setGeometry(geom_metric)
                feats.append(new_f)
                idx.insertFeature(new_f)
                kept_count += 1

            self.log(f"process remote features: {time.perf_counter() - t_process:.2f}s")
            self.log(f"remote total features iterated: {total_count}")
            self.log(f"remote kept features: {kept_count}")
            self.api_pre_filtered[cache_key] = {"features": feats, "index": idx}
            self.log(f"Found {len(feats)} API features within buffer ({self.metric_crs.authid()}).")

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
        """True-north initial bearing from application centroid to target nearest point.

        Input points are in self.metric_crs. Transform to geographic WGS84 and
        calculate ellipsoidal bearing; do not interpret projected grid north as
        geographic true north. Returns degrees clockwise from true north.
        """
        if p1 is None or p2 is None:
            raise ValueError("Cannot calculate bearing without both points")
        geographic_crs = QgsCoordinateReferenceSystem("EPSG:4326")
        if not self.metric_crs.isValid():
            raise ValueError("Invalid analysis CRS for true-north bearing")
        context = QgsProject.instance().transformContext()
        transform = QgsCoordinateTransform(self.metric_crs, geographic_crs, context)
        source_ll = transform.transform(QgsPointXY(p1))
        target_ll = transform.transform(QgsPointXY(p2))
        calculator = QgsDistanceArea()
        calculator.setSourceCrs(geographic_crs, context)
        if not calculator.setEllipsoid("WGS84"):
            raise ValueError("Could not initialize WGS84 ellipsoidal bearing")
        if calculator.measureLine(source_ll, target_ll) < 1e-6:
            raise ValueError("Bearing undefined: centroid and target point coincide")
        angle_rad = calculator.bearing(source_ll, target_ll)
        return math.degrees(angle_rad) % 360.0

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

    def _find_nearest_feature_spatial_index(self, app_union_metric: QgsGeometry, pre_data: dict):
        """Find the exact nearest feature among all *retrieved* geometries.

        A centroid-based k-nearest shortlist is not sufficient for large or
        irregular application polygons. Instead, get an initial exact upper
        bound from one indexed candidate, then use the spatial index to fetch
        every feature whose bounding box could improve that distance.
        The final decision always uses exact geometry-to-geometry distance.
        """
        feats = pre_data.get("features", [])
        idx = pre_data.get("index")
        if not feats or not app_union_metric or app_union_metric.isEmpty():
            return None, None, None, None, None

        if idx is None:
            idx, _ = self._build_spatial_index_lookup(feats)
        feat_lookup = {f.id(): f for f in feats}
        app_centroid_pt = app_union_metric.centroid().asPoint()

        nearest_feat = None
        min_dist = None
        nearest_pt_on_api = None
        nearest_pt_on_app = None
        tested_ids = set()

        def evaluate(fid):
            nonlocal nearest_feat, min_dist, nearest_pt_on_api, nearest_pt_on_app
            if fid in tested_ids:
                return
            tested_ids.add(fid)
            feat = feat_lookup.get(fid)
            if feat is None:
                return
            geom = feat.geometry()
            if not geom or geom.isEmpty():
                return
            dist = geom.distance(app_union_metric)
            if dist < 0:  # GEOS failure; never treat as a valid minimum.
                return
            if min_dist is None or dist < min_dist:
                p_api, p_app = self._shortest_line_endpoints(geom, app_union_metric)
                if p_api is None or p_app is None:
                    return
                min_dist = dist
                nearest_feat = feat
                nearest_pt_on_api = p_api
                nearest_pt_on_app = p_app

        # Seed an upper bound using a small index shortlist. This affects
        # speed only, not correctness of the final candidate set.
        for fid in idx.nearestNeighbor(QgsPointXY(app_centroid_pt), min(10, len(feats))):
            evaluate(fid)

        if min_dist is None:
            # Defensive fallback for unexpected index or geometry failures.
            for fid in feat_lookup:
                evaluate(fid)
        else:
            # For any geometry with distance < min_dist to the application,
            # its bounding box must intersect the application bounding box
            # expanded by min_dist. Include boundary-touching candidates.
            rect = app_union_metric.boundingBox()
            epsilon = max(1e-7, abs(min_dist) * 1e-10)
            rect.grow(min_dist + epsilon)
            for fid in idx.intersects(rect):
                evaluate(fid)

        return nearest_feat, min_dist, app_centroid_pt, nearest_pt_on_api, nearest_pt_on_app


    # -------------------------------
    # QGIS native result display helpers
    # -------------------------------
    def _remove_existing_result_layers(self) -> None:
        """Remove previous native result layers before adding a fresh result set."""
        try:
            project = QgsProject.instance()
            for lyr in list(project.mapLayers().values()):
                if lyr.name().startswith("Nearest Analysis - "):
                    project.removeMapLayer(lyr.id())
        except Exception as e:
            self.log(f"Failed to remove previous result layers: {e}")

    def _apply_basic_symbol(
        self,
        layer: QgsVectorLayer,
        color_name: str,
        size: float = None,
        width: float = None,
        opacity: float = None,
    ) -> None:
        """Apply a simple QGIS symbol to a memory layer. Styling failures are non-fatal."""
        try:
            symbol = QgsSymbol.defaultSymbol(layer.geometryType())
            if symbol is None:
                return
            symbol.setColor(QColor(color_name))
            if opacity is not None and hasattr(symbol, "setOpacity"):
                symbol.setOpacity(float(opacity))
            if size is not None and hasattr(symbol, "setSize"):
                symbol.setSize(size)
            if width is not None and hasattr(symbol, "setWidth"):
                symbol.setWidth(width)
            layer.renderer().setSymbol(symbol)
            layer.triggerRepaint()
        except Exception as e:
            self.log(f"Result layer styling skipped: {e}")

    def _enable_labeling(self, layer: QgsVectorLayer, field_name: str) -> None:
        """Enable native QGIS text labels for a memory layer."""
        try:
            settings = QgsPalLayerSettings()
            settings.fieldName = field_name
            settings.enabled = True
            layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
            layer.setLabelsEnabled(True)
            layer.triggerRepaint()
        except Exception as e:
            self.log(f"Result layer labeling skipped: {e}")

    def _add_memory_geometry_layer(self, name: str, geom: QgsGeometry, color_name: str = "cyan") -> QgsVectorLayer:
        """Add a single-geometry memory layer in the active metric CRS."""
        if geom is None or geom.isEmpty():
            return None

        # A memory layer requires a concrete geometry type.
        # "Geometry" is not a valid QGIS memory-provider geometry URI.
        geom_type = QgsWkbTypes.geometryType(geom.wkbType())
        if geom_type == QgsWkbTypes.PointGeometry:
            layer_type = "MultiPoint" if geom.isMultipart() else "Point"
        elif geom_type == QgsWkbTypes.LineGeometry:
            layer_type = "MultiLineString" if geom.isMultipart() else "LineString"
        elif geom_type == QgsWkbTypes.PolygonGeometry:
            layer_type = "MultiPolygon" if geom.isMultipart() else "Polygon"
        else:
            raise ValueError(f"Unsupported geometry type for {name}: {geom.wkbType()}")

        layer = QgsVectorLayer(
            f"{layer_type}?crs={self.metric_crs.authid()}",
            name,
            "memory"
        )
        if not layer.isValid():
            raise RuntimeError(f"Failed to create memory layer: {name} ({layer_type})")

        provider = layer.dataProvider()
        feat = QgsFeature(layer.fields())
        feat.setGeometry(QgsGeometry(geom))
        success, added_features = provider.addFeatures([feat])
        if not success or len(added_features) != 1:
            raise RuntimeError(f"Failed to add geometry to memory layer: {name}")

        layer.updateExtents()
        self._apply_basic_symbol(layer, color_name, width=1.2)
        QgsProject.instance().addMapLayer(layer)
        return layer

    def _add_result_layers_to_qgis(
        self,
        app_union: QgsGeometry,
        nearest_feat: QgsFeature,
        app_centroid_pt,
        nearest_pt_on_app,
        nearest_pt_on_api,
        dist,
        angle,
        direction,
        is_inside_case: bool,
    ) -> None:
        """
        Display analysis output using QGIS native memory layers only.

        The layers are stored in the active metric CRS. QGIS on-the-fly CRS
        transformation handles display in the current project CRS.
        """
        self._remove_existing_result_layers()

        nearest_geom = nearest_feat.geometry() if nearest_feat is not None else None

        self._add_memory_geometry_layer(
            "Nearest Analysis - Application Area",
            app_union,
            "blue"
        )
        self._add_memory_geometry_layer(
            "Nearest Analysis - Nearest Feature",
            nearest_geom,
            "cyan"
        )

        # Points layer
        points_layer = QgsVectorLayer(
            f"Point?crs={self.metric_crs.authid()}",
            "Nearest Analysis - Points",
            "memory"
        )
        points_provider = points_layer.dataProvider()
        points_provider.addAttributes([
            QgsField("type", QVariant.String),
            QgsField("distance_m", QVariant.Double),
            QgsField("angle_deg", QVariant.Double),
            QgsField("direction", QVariant.String),
        ])
        points_layer.updateFields()

        def add_point(point_label, pt):
            if pt is None:
                return
            f = QgsFeature(points_layer.fields())
            f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(pt)))
            f["type"] = point_label
            f["distance_m"] = float(dist) if dist is not None else None
            f["angle_deg"] = float(angle) if angle is not None else None
            f["direction"] = direction or ""
            points_provider.addFeatures([f])

        add_point("Application centroid", app_centroid_pt)
        add_point("Nearest point on application", nearest_pt_on_app)
        add_point("Nearest point on API", nearest_pt_on_api)
        points_layer.updateExtents()
        self._apply_basic_symbol(points_layer, "red", size=3.0)
        QgsProject.instance().addMapLayer(points_layer)

        # Direction / shortest line layer
        if not is_inside_case and app_centroid_pt is not None and nearest_pt_on_api is not None:
            line_layer = QgsVectorLayer(
                f"LineString?crs={self.metric_crs.authid()}",
                "Nearest Analysis - Direction Line",
                "memory"
            )
            line_provider = line_layer.dataProvider()
            line_provider.addAttributes([
                QgsField("distance_m", QVariant.Double),
                QgsField("angle_deg", QVariant.Double),
                QgsField("direction", QVariant.String),
            ])
            line_layer.updateFields()

            line_feat = QgsFeature(line_layer.fields())
            line_feat.setGeometry(
                QgsGeometry.fromPolylineXY([
                    QgsPointXY(app_centroid_pt),
                    QgsPointXY(nearest_pt_on_api),
                ])
            )
            line_feat["distance_m"] = float(dist) if dist is not None else None
            line_feat["angle_deg"] = float(angle) if angle is not None else None
            line_feat["direction"] = direction or ""
            line_provider.addFeatures([line_feat])
            line_layer.updateExtents()
            self._apply_basic_symbol(line_layer, "red", width=0.8)
            QgsProject.instance().addMapLayer(line_layer)

        # Result label point layer
        label_pt = app_centroid_pt
        if not is_inside_case and app_centroid_pt is not None and nearest_pt_on_api is not None:
            label_pt = QgsPointXY(
                (app_centroid_pt.x() + nearest_pt_on_api.x()) / 2.0,
                (app_centroid_pt.y() + nearest_pt_on_api.y()) / 2.0,
            )

        if label_pt is not None:
            label_layer = QgsVectorLayer(
                f"Point?crs={self.metric_crs.authid()}",
                "Nearest Analysis - Result Label",
                "memory"
            )
            label_provider = label_layer.dataProvider()
            label_provider.addAttributes([
                QgsField("label", QVariant.String),
                QgsField("distance_m", QVariant.Double),
                QgsField("direction", QVariant.String),
            ])
            label_layer.updateFields()

            label_feat = QgsFeature(label_layer.fields())
            label_feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(label_pt)))
            if is_inside_case:
                label_text = f"{round(float(dist), 2)} m | {direction or 'Undefined'}"
            else:
                label_text = (f"{round(float(dist), 2)} m | {round(float(angle), 2)}° ({direction})"
                              if angle is not None else f"{round(float(dist), 2)} m | Undefined")
            label_feat["label"] = label_text
            label_feat["distance_m"] = float(dist) if dist is not None else None
            label_feat["direction"] = direction or ""
            label_provider.addFeatures([label_feat])
            label_layer.updateExtents()

            # Show the result as text on the map instead of as a visible point.
            # The point geometry is retained only as the label anchor.
            self._apply_basic_symbol(label_layer, "darkRed", size=2.5, opacity=0.0)
            self._enable_labeling(label_layer, "label")
            QgsProject.instance().addMapLayer(label_layer)

        self.log("Result layers added to QGIS map canvas.")

    def _zoom_to_result_extent(self, geoms) -> None:
        """
        Centre the QGIS map canvas on the analysis result.

        Result geometries are in the active metric CRS; their combined extent is
        transformed to the current canvas CRS before setting the canvas extent.
        """
        try:
            from qgis.utils import iface

            canvas = iface.mapCanvas()
            if canvas is None:
                self.log("QGIS map canvas unavailable; auto-centre skipped.")
                return

            extent = None
            for geom in geoms:
                if geom is None or geom.isEmpty():
                    continue
                rect = geom.boundingBox()
                if extent is None:
                    extent = QgsRectangle(rect)
                else:
                    extent.combineExtentWith(rect)

            if extent is None or extent.isEmpty():
                self.log("Result extent is empty; auto-centre skipped.")
                return

            canvas_crs = canvas.mapSettings().destinationCrs()
            if canvas_crs.isValid() and canvas_crs != self.metric_crs:
                tr = QgsCoordinateTransform(self.metric_crs, canvas_crs, QgsProject.instance())
                extent = tr.transformBoundingBox(extent)

            # Add a margin, with a small fallback where the result is very small.
            pad = max(extent.width(), extent.height()) * 0.20
            if pad <= 0:
                pad = 100.0
            extent.grow(pad)

            canvas.setExtent(extent)
            canvas.refresh()
            self.log("Map canvas centred on analysis result.")
        except Exception as e:
            self.log(f"Could not auto-centre map canvas: {e}")


    # -------------------------------
    # Run Analysis
    # -------------------------------
    def run_analysis(self) -> None:
        """Export nearest feature to CSV and display results with native QGIS layers."""
        try:
            app_index = self.combo_app.currentIndex()
            api_name = self.combo_api.currentText()
            provider_key = self._provider_key()

            if app_index < 0 or not api_name:
                QtWidgets.QMessageBox.warning(self, "Error", "Please select both layers.")
                return

            # Ensure the selected API layer is loaded into QGIS and available in Layers.
            try:
                self._ensure_selected_api_layer_loaded()
            except Exception as e:
                self.log(f"Auto-load API layer failed before analysis: {e}")

            app_layer = self.shp_layers[app_index]
            app_uri = app_layer.dataProvider().dataSourceUri()
            api_layer_obj = self._get_selected_api_layer()

            if self._use_cross_border_auto_crs():
                self.metric_crs = self._choose_metric_crs_cross_border(app_layer, api_layer_obj)
                self.log(f"Cross-border CRS auto-selection enabled. Analysis CRS set to: {self.metric_crs.authid()}")
            else:
                self.metric_crs = self._choose_metric_crs_cross_border(app_layer, api_layer_obj)
                self.log("Fast default CRS mode enabled. Analysis CRS set to: EPSG:29903.")
            self.log(f"All computations use {self.metric_crs.authid()} for metric analysis.")

            t0 = time.perf_counter()
            app_feats_metric, app_union = self._layer_geometries_in_metric_crs(app_layer)
            self.log(f"_layer_geometries_in_metric_crs: {time.perf_counter() - t0:.2f}s")

            if not app_union or app_union.isEmpty():
                QtWidgets.QMessageBox.warning(self, "Error", "Application layer has no valid geometry.")
                return

            self.buffer_distance_m = self._get_buffer_distance_m()
            self.log(
                f"Using buffer distance: {self.buffer_distance_m / 1000.0:.2f} km "
                f"({self.buffer_distance_m:.0f} m)"
            )

            self._invalidate_cache_if_needed(
                f"{provider_key}:{api_name}",
                app_uri,
                self.buffer_distance_m,
                self.metric_crs.authid()
            )

            user_buffer_geom = app_union.buffer(self.buffer_distance_m, 8)

            try:
                QtWidgets.QApplication.setOverrideCursor(_wait_cursor())
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

            cache_key = self._make_analysis_signature(
                f"{provider_key}:{api_name}",
                app_uri,
                self.buffer_distance_m,
                self.metric_crs.authid()
            )
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
            spatial_relationship = self._spatial_relationship(app_union, nearest_geom, dist)
            is_inside_case = abs(float(dist)) <= 1e-9

            angle = None
            direction = None
            if is_inside_case:
                direction = "Undefined"
                self.log(f"Zero distance ({spatial_relationship}): true-north bearing undefined.")
            else:
                try:
                    angle = self._azimuth_geographic(app_centroid_pt, nearest_pt_on_api)
                    direction = self._azimuth_to_dir(angle)
                except Exception as bearing_error:
                    self.log(f"True-north bearing unavailable: {bearing_error}")
                    direction = "Undefined"

            selected_fields = [item.text() for item in self.fields_list.selectedItems()] if hasattr(
                self, "fields_list"
            ) else []
            nearest_fields = [field.name() for field in nearest_feat.fields()] if nearest_feat.fields() else []

            if not selected_fields:
                selected_fields = nearest_fields

            export_cols = [f for f in selected_fields if f in nearest_fields]
            export_cols += ["Distance_m", "Direction (°)", "Direction", "Spatial_Relationship", "Analysis_CRS"]

            row = {}
            for fld in selected_fields:
                if fld in nearest_fields:
                    row[fld] = nearest_feat[fld]

            row["Distance_m"] = float(dist)
            row["Direction (°)"] = "" if angle is None else round(angle, 2)
            row["Direction"] = direction if direction is not None else ""
            row["Spatial_Relationship"] = spatial_relationship
            row["Analysis_CRS"] = self.metric_crs.authid()

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
                    attribution = self._current_attribution()
                    if attribution:
                        f.write(f"# {attribution}\n")
                    f.write(f"# Source: {self._provider_label()}\n")
                    writer = csv.DictWriter(f, fieldnames=export_cols, extrasaction="ignore")
                    writer.writeheader()
                    writer.writerow(row)

                self.log(f"CSV saved: {output_csv}")
                self.log("Source attribution added to CSV header.")
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "CSV Export Error", str(e))
                self.log(f"CSV export failed: {e}")
                return

            self._add_result_layers_to_qgis(
                app_union=app_union,
                nearest_feat=nearest_feat,
                app_centroid_pt=app_centroid_pt,
                nearest_pt_on_app=nearest_pt_on_app,
                nearest_pt_on_api=nearest_pt_on_api,
                dist=dist,
                angle=angle,
                direction=direction,
                is_inside_case=is_inside_case,
            )

            zoom_geoms = [app_union, nearest_geom]
            if not is_inside_case and app_centroid_pt is not None and nearest_pt_on_api is not None:
                zoom_geoms.append(
                    QgsGeometry.fromPolylineXY([
                        QgsPointXY(app_centroid_pt),
                        QgsPointXY(nearest_pt_on_api),
                    ])
                )

            self._zoom_to_result_extent(zoom_geoms)

            QtWidgets.QMessageBox.information(
                self,
                "Analysis Completed",
                "CSV exported successfully.\n"
                "Result layers have been added to the QGIS map canvas."
            )

        except Exception as e:
            self.log(f"Analysis failed: {e}")
            self.log(traceback.format_exc())
