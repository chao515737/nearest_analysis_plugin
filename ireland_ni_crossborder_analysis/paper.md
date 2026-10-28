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

The plugin implements a projection-distortion-aware approach to selecting a coordinate reference system (CRS) for cross-border proximity analysis. Map projection theory establishes that planar distance measurements are affected by projection scale distortion, making CRS selection important for spatial analysis [@Snyder1987].

The software evaluates candidate projected CRSs, including Irish Transverse Mercator, Irish Grid, British National Grid, and neighbouring UTM zones. It estimates relative distance distortion using sampled locations within the analysis extent and compares projected distances against WGS84 ellipsoidal distances. The candidate with the lowest estimated maximum relative distortion is selected for the analysis.

The implementation follows established principles of coordinate conversion and cartographic projection rather than introducing a new map projection method [@IOGP2026; @PROJDocumentation]. Candidate features are screened using a spatial index, with an expanded bounding-box query based on an initial distance upper bound, followed by exact geometry-distance comparisons among retrieved candidates. The correctness of the result is conditional on the completeness of the features obtained from the remote service and included in the index.

The reported distance is the shortest geometric distance between the application area and the selected target feature. Direction is calculated from the application-area centroid to the corresponding nearest point on the target, using a true-north bearing. The plugin also reports spatial relationships, including containment, overlap, and boundary contact; zero-distance cases are distinguished from ordinary directional results. Outputs include structured CSV fields describing distance, direction, spatial relationship, and analysis CRS.

# Research impact statement

This release extends the scope of the previously published *Ireland Nearest Analysis* software [@Gong2026] from an Ireland-focused workflow toward cross-jurisdictional use. Its potential research value is in reducing manual coordination of heterogeneous public geospatial services and CRS decisions in environmental and planning analysis. The earlier publication documents use of the predecessor software in Irish environmental assessment; that evidence should not be interpreted as independent evidence of research adoption of this new cross-border release. Further validation of remote-data completeness, CRS selection, and cross-border case studies is planned as development continues.

# Installation and usage

The cross-border plugin source is maintained in the `cross-border-development` branch of the [project repository](https://github.com/chao515737/nearest_analysis_plugin/tree/cross-border-development/ireland_ni_crossborder_analysis). Users should obtain a ZIP archive containing the `ireland_ni_crossborder_analysis` plugin directory as its top-level folder, then select **Plugins → Manage and Install Plugins → Install from ZIP** in a compatible QGIS installation. Downloading the entire multi-plugin GitHub repository as a ZIP is not equivalent to downloading this standalone installable plugin package.

After installation, users can load their own application-area polygon dataset near or across the Ireland–Northern Ireland border, select an available remote dataset, and run the nearest-feature analysis. The plugin exports results for subsequent checking and reporting. Reproducible verification should additionally use fixed test geometries and archived source data because live remote services can change.

# AI usage disclosure

Generative AI tools were used to assist with software code development, debugging, and drafting documentation and manuscript text. The author is responsible for reviewing, testing, maintaining, and verifying the software and the accuracy of this paper. This disclosure should be updated before submission to accurately describe the tools and the extent of human review and validation performed.

# Acknowledgements

The author acknowledges the open-source QGIS community and the public organisations providing the geospatial web services used by the plugin.

# References
