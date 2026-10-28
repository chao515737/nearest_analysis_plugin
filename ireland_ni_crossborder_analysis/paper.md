---
title: 'Ireland–Northern Ireland Cross-Border Analysis: A QGIS plugin for cross-jurisdictional nearest-feature analysis'
tags:
  - QGIS
  - GIS
  - cross-border analysis
  - spatial analysis
  - coordinate reference systems
  - geospatial web services
authors:
  - name: Chao Gong
    orcid: 0009-0009-5363-1854
    affiliation: 1
affiliations:
  - name: Quarry Consulting, Ireland
    index: 1
date: 28 October 2026
bibliography: paper.bib
---

# Summary

Ireland–Northern Ireland Cross-Border Analysis is an open-source QGIS plugin for nearest-feature analysis involving geospatial datasets from the Republic of Ireland and Northern Ireland. The plugin combines local application-area geometries with remote vector datasets discovered through geospatial web services. It provides coordinate reference system (CRS) selection for metric analysis, geometric nearest-feature distances, directions referenced to true north, topological relationship labels, and CSV outputs. The current public development release is version 0.1.0.

# Statement of need

Environmental assessments and planning studies near the Ireland–Northern Ireland border may require spatial information from different public authorities, published through different services and in different coordinate reference systems. A user may need to locate relevant datasets, transform geometries, identify the nearest environmental feature, and report a distance and direction. Although QGIS provides general-purpose geometry and proximity tools [@QGIS], assembling these steps into a repeatable cross-jurisdictional workflow can require additional configuration.

The earlier *Ireland Nearest Analysis* plugin [@Gong2026] integrated Irish environmental web-service access, standardisation to EPSG:29903, spatial filtering, nearest-feature computation, and reporting. That publication explicitly identified support for Northern Ireland and additional CRSs as possible future work. The present plugin develops this direction by extending data-source discovery and metric CRS handling beyond the original Ireland-only workflow. It is intended for GIS practitioners and researchers undertaking environmental screening, spatial planning, and related cross-border analyses.

# State of the field

QGIS includes native tools for proximity analysis, while libraries such as GeoPandas [@GeoPandas] and spatial databases such as PostGIS [@PostGIS] support programmable nearest-feature operations. These tools remain appropriate alternatives, especially for locally available data or scripted pipelines. The contribution of this plugin is a QGIS user workflow connecting public web-service discovery, cross-border CRS handling, proximity analysis, and reporting. Its novelty should therefore be assessed as an integrated software workflow and an extension of the previously published plugin, not as a new mathematical nearest-neighbour algorithm.

# Software design

The plugin is implemented in Python using the QGIS Python API and a Qt-based interface. It supports discovery or selection of vector layers from configured providers, including Irish EPA services and selected UK and Northern Ireland data services. For Environment Agency datasets, a background catalogue search and caching mechanism are used to reduce interface blocking during discovery. Service availability and the set of discoverable layers depend on external providers.

The plugin uses a projection-distortion-aware heuristic to select a projected coordinate reference system (CRS) for metric analysis. Because map projections introduce scale distortion, CRS choice can affect planar distance measurements [@Snyder1987]. The implementation compares five candidate systems: Irish Transverse Mercator (EPSG:2157), Irish Grid (EPSG:29903), British National Grid (EPSG:27700), and WGS 84 / UTM zones 29N (EPSG:32629) and 30N (EPSG:32630). It samples the corners and centre of the application-area and target-layer extents after transformation to WGS84. At each sample location, short east–west and north–south test segments are compared using projected Euclidean distances and WGS84 ellipsoidal distances. The candidate with the smallest maximum sampled relative distance error is selected; EPSG:2157 is the fallback if the comparison cannot be completed. This heuristic does not establish a globally optimal CRS or guarantee minimal error for every feature pair. The approach applies established projection and coordinate-conversion principles rather than introducing a new map projection [@IOGPGuidance; @PROJDocumentation].

Candidate features are screened using a spatial index, with an expanded bounding-box query based on an initial distance upper bound, followed by exact geometry-distance comparisons among retrieved candidates. The correctness of the result is conditional on the completeness of the features obtained from the remote service and included in the index.

The reported distance is the shortest geometric distance between the application area and the selected target feature. Direction is calculated from the application-area centroid to the corresponding nearest point on the target, using a true-north bearing. The plugin also reports spatial relationships, including containment, overlap, and boundary contact; zero-distance cases are distinguished from ordinary directional results. Outputs include structured CSV fields describing distance, direction, spatial relationship, and analysis CRS.

# Research impact statement

The Ireland–Northern Ireland Cross-Border Analysis plugin extends the previously published Ireland Nearest Analysis software [@Gong2026] from an Ireland-focused workflow to cross-jurisdictional geospatial analysis. By integrating geospatial services from multiple public authorities, automated coordinate reference system (CRS) selection, and nearest-feature analysis within a unified QGIS environment, the software aims to reduce manual data preparation, coordinate transformation, and spatial analysis steps in environmental assessment and planning workflows.

The software project originated in May 2025 with the development of Ireland Nearest Analysis, initially focusing on Irish environmental geospatial services. The project subsequently established a public development history on GitHub, with the repository documented in the original software publication as publicly available from November 2025. The initial software was formally published in the Journal of Open Research Software in May 2026 [@Gong2026]. The cross-border extension represents a subsequent development phase of the same research software project, with its source code made publicly available in October 2026.

The predecessor software has been applied in environmental assessment workflows in Ireland, as documented in the earlier publication [@Gong2026]. Building upon this foundation, the cross-border extension addresses additional challenges associated with heterogeneous geospatial services and coordinate reference systems across Ireland and Northern Ireland. Its potential applications include environmental impact assessment, ecological screening, infrastructure planning, and spatial analysis near jurisdictional boundaries.

The current release provides these capabilities for further research use and evaluation. Independent research adoption of the cross-border version has not yet been established. Although the projection-selection and nearest-feature procedures are available for inspection in the source code, systematic cross-border benchmarking and verification of remote-service data completeness remain areas for further validation and development.

# Installation and usage

The cross-border plugin source is maintained in the `cross-border-development` branch of the [project repository](https://github.com/chao515737/nearest_analysis_plugin/tree/cross-border-development/ireland_ni_crossborder_analysis). Users should obtain a ZIP archive containing the `ireland_ni_crossborder_analysis` plugin directory as its top-level folder, then select **Plugins → Manage and Install Plugins → Install from ZIP** in a compatible QGIS installation. Downloading the entire multi-plugin GitHub repository as a ZIP is not equivalent to downloading this standalone installable plugin package.

After installation, users can load their own application-area polygon dataset near or across the Ireland–Northern Ireland border, select an available remote dataset, and run the nearest-feature analysis. The plugin exports results for subsequent checking and reporting. Reproducible verification should additionally use fixed test geometries and archived source data because live remote services can change.

# AI usage disclosure

OpenAI ChatGPT was used to assist with generating and revising Python code, debugging, and drafting documentation and manuscript text. 

# Acknowledgements

The author acknowledges the open-source QGIS community and the public organisations providing the geospatial web services used by the plugin.

# License

This project is licensed under the MIT License.
See [LICENSE](LICENSE) for details.

# Data Sources

The plugin accesses publicly available geospatial datasets
from multiple providers in Ireland and the United Kingdom.

Data remain subject to the licensing and attribution
requirements of their respective providers.
