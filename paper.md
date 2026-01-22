---
title: "Nearest Analysis: A QGIS Plugin for Automated Nearest-Feature Spatial Analysis"
tags:
  - QGIS
  - GIS
  - Spatial analysis
  - Environmental assessment
  - Reproducible workflows
authors:
  - name: Chao Gong
    affiliation: 1
affiliations:
  - name: Geography Department, Maynooth University
    index: 1
date: 2026-01-15
bibliography: paper.bib
---

# (1) Overview

## Abstract

Nearest Analysis is an open-source QGIS plugin that automates nearest-feature spatial analysis between a local project area and authoritative remote geospatial datasets accessed via public web services. The software integrates coordinate reference system standardisation, remote data retrieval, spatial filtering, distance computation, and azimuth calculation into a single reproducible workflow. It supports both Web Feature Services (WFS) and ArcGIS REST Feature Services and exports structured results for reporting and reuse. The plugin has been developed and applied in real-world environmental assessment workflows in Ireland and is openly available under the MIT License.

## Keywords

QGIS; spatial analysis; environmental assessment; GIS automation; reproducible workflows

## Introduction

Nearest-feature analysis is a common requirement in environmental impact assessment, land-use planning, and regulatory screening. Practitioners are frequently required to assess the proximity of a proposed development site to environmental receptors such as protected areas, water bodies, or infrastructure datasets published by regulatory authorities. While the QGIS Geographic Information System provides tools for proximity analysis on local datasets [1], workflows that involve remote regulatory data typically require multiple manual steps, including data download, coordinate system harmonisation, spatial filtering, and post-processing. These steps are time-consuming, error-prone, and difficult to reproduce consistently across projects.

Nearest Analysis was developed to address this gap by providing a single, integrated workflow within QGIS that directly connects to public geospatial APIs and automates the full nearest-feature analysis process. The software has been used in applied environmental assessment and geospatial consultancy workflows in Ireland, where transparency and reproducibility are critical for regulatory reporting. Compared to existing proximity tools, Nearest Analysis uniquely combines remote data access via Web Feature Services, coordinate standardisation, distance and azimuth computation, and structured output generation in one reproducible workflow without requiring scripting or advanced GIS preprocessing.

## Implementation and Architecture

The plugin is implemented in Python as a QGIS plugin within the QGIS Geographic Information System [1], using the QGIS Python API and PyQt5 for the graphical user interface. It integrates established open-source geospatial libraries, including GeoPandas for vector data handling [2], OWSLib for interaction with OGC-compliant Web Feature Services [3], Shapely for geometric operations, and Matplotlib for optional visualisation output [4]. The software architecture follows a modular design that separates user input handling, data retrieval, spatial processing, and output generation.

The core workflow consists of the following stages:

- **Input selection:** The user selects a local vector layer representing the application area and specifies a remote dataset accessed via a WFS endpoint or an ArcGIS REST Feature Service.
- **Coordinate standardisation:** All spatial operations are normalised to EPSG:29903 (Irish National Grid) to ensure consistent distance and azimuth calculations.
- **Remote data retrieval:** Features from the selected remote dataset are retrieved using OWSLib [3] and spatially filtered to a user-defined buffer distance (default 100 km) around the application area.
- **Proximity computation:** The software identifies the single nearest feature based on minimum geometric distance, computes the distance in metres, and calculates a geographic azimuth following surveying conventions (0° = North, clockwise).
- **Output generation:** Results are exported to a CSV file suitable for inclusion in environmental assessment reports. Optional visualisations illustrate the spatial relationship between the application area and the nearest feature using Matplotlib [4].

Design decisions prioritise reproducibility, transparency, and practical applicability in regulatory GIS contexts.

## Quality control

The software has been tested in QGIS 3.x environments (tested with QGIS 3.28 and later) on standard desktop operating systems. Functional testing was conducted using multiple application-area geometries and both WFS-based and ArcGIS REST-based datasets to verify correct coordinate transformation, distance computation, and azimuth calculation. Example workflows and output files are provided in the code repository to allow users to quickly confirm correct installation and operation. Example outputs and the graphical user interface of the plugin are shown in Figures 1–3. The application area shown in the figures is a simplified and anonymised polygon used for illustrative purposes only and does not represent a real or identifiable site.

### Figures

![Figure 1: Graphical user interface of the Nearest Analysis QGIS plugin showing selection of the application area, remote dataset, attribute fields, and processing log.](figures/figure1_gui.png)

![Figure 2: Example spatial output generated by the Nearest Analysis plugin illustrating the application area, the nearest feature, and the computed distance and geographic azimuth.](figures/figure2_spatial_output.png)

![Figure 3: Example tabular output generated by the Nearest Analysis plugin showing the computed nearest-feature distance and direction exported as a CSV file.](figures/figure3_tabular_output.png)

# (2) Availability

## Operating system

Windows, macOS, and Linux (via QGIS 3.x)

## Programming language

Python (QGIS 3.x Python environment)

## Additional system requirements

QGIS 3.x installation with network access for querying remote geospatial services

## Dependencies

GeoPandas [2]; OWSLib [3]; Matplotlib [4]

## List of contributors

Chao Gong – software design, implementation, testing, and documentation

## Software location

### Archive

- **Name:** Zenodo  
- **Persistent identifier:** https://doi.org/10.5281/zenodo.18262401  
- **Licence:** MIT License  
- **Publisher:** Chao Gong  
- **Version published:** v1.0.0  
- **Date published:** 15/01/2026  

### Code repository

- **Name:** GitHub  
- **Identifier:** https://github.com/chao515737/nearest_analysis_plugin  
- **Licence:** MIT License  
- **Date published:** 17/11/2025  

## Language

English

# (3) Reuse potential

Nearest Analysis can be reused in environmental assessment, planning analysis, and geospatial research contexts where reproducible proximity analysis is required. The software is applicable to workflows that involve comparing local project areas with authoritative remote geospatial datasets accessed via Web Feature Services or ArcGIS REST services. Its modular design allows users to adapt the plugin for alternative datasets, coordinate reference systems, or extended output formats. Users may report issues, request features, or contribute enhancements via the GitHub repository.

## Acknowledgements

The author gratefully acknowledges the Geography Department at Maynooth University for institutional support. Special thanks are extended to Dr Conor Cahalane and Dr Kevin Credit for academic guidance and constructive feedback during the development of this software.

## Funding statement

The author received no specific funding for this work.

## Competing interests

The author declares that they have no competing interests.

# References

[1] QGIS Development Team 2023 QGIS Geographic Information System. Open Source Geospatial Foundation Project. Available at: https://qgis.org  

[2] Jordahl, K, Van den Bossche, J, Fleischmann, M, McBride, J, Wasserman, J, Badaracco, A, Gerard, J, Snow, A D and Tratner, J 2020 GeoPandas: Python tools for geographic data. Zenodo. DOI: https://doi.org/10.5281/zenodo.3946761  

[3] Kralidis, T, McKenna, J, Fosnight, E and Kolas, D 2018 OWSLib: OGC Web Service utility library. Zenodo. DOI: https://doi.org/10.5281/zenodo.593069  

[4] Hunter, J D 2007 Matplotlib: A 2D graphics environment. *Computing in Science & Engineering* 9(3): 90–95. DOI: https://doi.org/10.1109/MCSE.2007.55  
