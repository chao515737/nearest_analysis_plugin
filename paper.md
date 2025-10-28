---
title: 'Nearest Analysis: A QGIS plugin for nearest-feature analysis with WFS and ArcGIS REST'
tags:
  - QGIS
  - GIS
  - Python
  - Geospatial
  - Environmental data
authors:
  - name: Chao Gong
    affiliation: 1
affiliations:
  - name: Geography Department, Maynooth University
    index: 1
date: 2025-10-28
bibliography: paper.bib
---

# Summary

`Nearest Analysis` is a QGIS plugin that automates nearest-feature analysis between a local “Application Area” (vector layer) and remote datasets served via EPA WFS and ArcGIS REST services. The plugin normalizes all computations to EPSG:29903 (Irish Grid), pre-downloads features within a configurable buffer from the application boundary (default 2 km), computes the nearest distance and geographic azimuth (0° = North, clockwise), and exports a CSV of the single nearest feature. A Matplotlib view is provided for quick visual inspection (centroid, nearest points, and an arrow).

# Statement of need

Spatial analysts and environmental researchers frequently need to quantify distances and directions from a project boundary to relevant environmental features (e.g., waterbodies, geoheritage sites, greenways). Conventional workflows often involve manual data retrieval, projection harmonization, and spatial querying, which are time-consuming and error-prone. This plugin streamlines those steps within QGIS: it queries authoritative EPA WFS / ArcGIS endpoints directly, enforces a consistent projected CRS (EPSG:29903), and produces minimal, reproducible outputs (CSV + plot). The result is faster iteration, reduced manual handling, and improved reproducibility for site-screening and reporting.

# Functionality

- **Remote data sources:** EPA WFS (via `owslib`) and ArcGIS Feature/Map Services (via REST).
- **CRS normalization:** all layers are reprojected to **EPSG:29903 (Irish Grid)** for metric distance.
- **Configurable search extent:** pre-download features within a buffer from the application boundary.
- **Nearest metrics:** computes **nearest distance** and **geographic azimuth** (0° = North, clockwise) and a cardinal direction.
- **Outputs:** exports a **single nearest feature** to CSV (selected attributes + distance/azimuth/direction); optional Matplotlib visualization.

# Implementation

The plugin is implemented in Python using QGIS (PyQt5, `qgis.core`) with `geopandas`/`shapely` for geometry handling, `owslib` and `requests` for WFS/REST access, and Matplotlib for visualization.  
Key design choices:
- **Pre-download and filter** remote features by the buffered application boundary to reduce unnecessary processing.
- **Robust CRS handling**: if a source layer lacks a CRS, EPSG:4326 is assumed, then reprojected to EPSG:29903.
- **Minimal output**: one-row CSV with the nearest feature for clear downstream use in reports.

# Installation and usage

1. Install QGIS (v3.x).  
2. Place the plugin folder in your QGIS user plugins directory (or install from source via the QGIS Plugin Manager’s “Install from ZIP”).  
3. In QGIS, load your local **Application Area** layer.  
4. In the plugin dialog, choose one API layer (EPA WFS or ArcGIS REST), optionally select fields, and set the **buffer distance** (default 2 km).  
5. Run analysis → select an output folder → a CSV is generated; a Matplotlib figure can be displayed.

**Dependencies:** `geopandas`, `shapely`, `requests`, `owslib`, `matplotlib` (plus QGIS’s PyQt5 and `qgis.core`).  
**CRS:** all computations use EPSG:29903 (Irish Grid).

# Quality control

- The plugin includes logging for each step (CRS normalization, field loading, pre-download counts, CSV export).  
- Typical checks include: (i) CRS correctness, (ii) buffer-filter sanity (features count within extent), and (iii) visual inspection in the Matplotlib view and QGIS canvas.  
- We recommend validating results on a small test area and comparing measured distances (in meters) against known ground truth or QGIS’s native tools.

# Availability

- **Repository:** (add your public Git URL here)  
- **License:** OSI-approved license (e.g., MIT).  
- **Issue tracking:** enabled on the repository for bugs and feature requests.

# Acknowledgements

We thank collaborators and colleagues for feedback on requirements and testing. Early prototyping benefited from assistance by GPT-based tools; the final implementation was refactored and validated by the author.

# References
