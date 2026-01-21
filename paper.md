---
title: "Nearest Analysis: A QGIS Plugin for Automated Nearest-Feature Spatial Analysis"
tags:
  - QGIS
  - GIS
  - Spatial Analysis
  - Geoscience
  - Python
authors:
  - name: Chao Gong
    affiliation: 1
affiliations:
  - name: Geography Department, Maynooth University
    index: 1
date: 2026-01-15
bibliography: paper.bib
---

## Abstract

Nearest Analysis is an open-source QGIS plugin designed to automate nearest-feature spatial analysis between a local project area ("Application Area") and remote geospatial datasets accessed via public web APIs, such as EPA Web Feature Services (WFS) and ArcGIS REST Feature Services. The plugin standardizes coordinate reference systems, retrieves and spatially filters relevant remote features within a defined buffer, computes nearest distances and geographic azimuths (0° = North, clockwise), and exports results to a CSV file. An optional visualization is produced using Matplotlib to illustrate spatial relationships.

The software was developed to support reproducible environmental site assessments and regulatory mapping workflows in Ireland, where proximity analysis between development sites and environmental receptors is a routine but often manual GIS task.

## Statement of Need

Nearest-feature analysis is a common requirement in environmental impact assessment, land-use planning, and regulatory screening. While QGIS provides tools for proximity analysis on local datasets, workflows involving remote regulatory datasets typically require multiple manual steps, including data download, coordinate system harmonization, spatial filtering, and post-processing. These steps are time-consuming, error-prone, and difficult to reproduce consistently across projects.

Nearest Analysis addresses this need by providing a single, integrated workflow within QGIS that connects directly to public geospatial APIs, automates coordinate system standardization, and performs transparent and repeatable nearest-feature computations. The plugin enables users to conduct proximity analyses using authoritative remote datasets without requiring scripting or advanced GIS preprocessing, supporting reproducibility in both professional and research-oriented geospatial workflows.

## Software Description

The plugin is implemented in Python using the QGIS API and PyQt5, and integrates several established open-source geospatial libraries. Its core workflow consists of the following stages:

**Input selection:** Users select a local vector layer representing the application area and a remote dataset accessed via EPA WFS or ArcGIS REST services.

**Coordinate standardization:** All spatial operations are normalized to EPSG:29903 (Irish National Grid) to ensure consistent distance and azimuth calculations. If input layers lack explicit CRS metadata, a default geographic CRS assumption is applied prior to reprojection.

**Remote data retrieval:** Features from the selected API layer are retrieved and spatially filtered to a user-defined buffer distance (default 100 km) around the application area.

**Proximity computation:** The plugin identifies the single nearest feature based on minimum geometric distance to the application area boundary, computes the distance in meters, and calculates a geographic azimuth following surveying conventions (0° = North, clockwise) using representative application-area reference points.

**Output generation:** Results are exported to a CSV file, and an optional Matplotlib figure visualizes the application area, nearest feature, centroid, and connecting direction arrow.

The plugin supports both WFS-based and ArcGIS REST-based services and includes field selection for customizable output. Design decisions emphasize reproducibility, transparency, and practical applicability in regulatory GIS contexts.

## Example Use Case

A typical use case involves screening a proposed development site against environmental datasets published by national agencies. After loading the application area into QGIS, a user selects an EPA WFS dataset (e.g., hydrographic or protected site layers) and runs the analysis. The plugin automatically retrieves nearby features, computes the nearest distance and direction, and produces a CSV suitable for inclusion in environmental assessment reports, along with an optional visualization for interpretive support.

Beyond illustrative examples, the software has been applied in real-world environmental assessment and geospatial consultancy workflows in Ireland, supporting reproducible proximity screening using authoritative regulatory datasets.

## Availability and Reuse

The Nearest Analysis plugin is openly available under the MIT License.

- **Source code:** https://github.com/chao515737/nearest_analysis_plugin  
- **Archived release (DOI):** https://doi.org/10.5281/zenodo.18262401  
- **Version:** v1.0.0  
- **Platform:** QGIS 3.x (tested with QGIS 3.28+)

The software is intended for reuse in environmental assessment, planning analysis, and geospatial research contexts where reproducible proximity analysis is required.

## Acknowledgements

The author gratefully acknowledges the Geography Department at Maynooth University for institutional support. Special thanks are extended to Dr Conor Cahalane and Dr Kevin Credit for their academic guidance, constructive feedback, and valuable discussions during the development and testing of this software. All final code has been reviewed, documented, and validated by the author.
