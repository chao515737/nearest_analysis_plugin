---
title: "Nearest Analysis: A QGIS Plugin for Automated Nearest-Feature Spatial Analysis"
tags:
  - QGIS
  - Python
  - GIS
  - Spatial Analysis
  - Geoscience
authors:
  - name: Chao Gong
    affiliation: "1"
affiliations:
  - name: "Department of Civil, Structural and Environmental Engineering, Trinity College Dublin"
    index: 1
date: 2025-10-28
bibliography: paper.bib
---

# Summary

**Nearest Analysis** is an open-source QGIS plugin developed to automate the process of nearest-feature spatial analysis between a local "Application Area" and remote geospatial data sources such as the EPA WFS or ArcGIS REST Feature Service. The tool standardizes coordinate systems, downloads relevant data within a user-defined buffer, and computes both the nearest distance and geographic azimuth (0° = North, clockwise). It then exports the result to a CSV file and optionally displays a Matplotlib figure showing the relationship between the application area and its nearest feature.

This plugin simplifies and automates tasks that are traditionally labor-intensive in QGIS, allowing environmental engineers, planners, and GIS professionals to perform reproducible proximity analyses directly from public web-based datasets.

# Statement of need

Assessing the proximity between project sites and environmental features is a key step in geospatial analysis, environmental impact assessments, and land-use planning. Traditionally, such analyses require manual data downloads, CRS transformations, and distance computations, which can be error-prone and time-consuming.  

The *Nearest Analysis* plugin addresses this gap by offering an intuitive interface that directly connects QGIS to public geospatial APIs (e.g., EPA WFS, ArcGIS Feature Services). It enables automated, transparent, and reproducible nearest-feature computations without requiring users to write code or perform complex GIS preprocessing.

# Functionality

The plugin was developed using **Python** and **PyQt5** within the QGIS Plugin Builder environment. It leverages the following open-source libraries:

- **QGIS API** (`qgis.core`, `QgsProject`) for accessing layers loaded in QGIS.
- **GeoPandas** and **Shapely** for spatial data operations and geometry calculations.
- **OWSLib** and **Requests** for connecting to remote WFS and ArcGIS REST endpoints.
- **Matplotlib** for visualizing results as interactive figures.

Key functionalities include:

- Support for both local and online vector data (e.g., EPA WFS, ArcGIS REST Feature Services).
- Automatic re-projection of all layers to **EPSG:29903 (Irish National Grid)**.
- Pre-download of features within a user-specified buffer.
- Computation of the nearest distance, azimuth angle (°), and direction (N, NE, E, SE, S, SW, W, NW).
- CSV export containing the nearest feature and its distance and direction.
- Optional visualization of results using Matplotlib, including centroids, connecting arrows, and buffer areas.

# Implementation details

All spatial operations are handled using GeoPandas and Shapely.  
The workflow is as follows:

1. The plugin reads the selected local layer (“Application Area”) and converts it to EPSG:29903.
2. It downloads features from the selected remote API (EPA WFS or ArcGIS REST).
3. Only features within the buffer distance (default 100 km, user-adjustable) are retained.
4. The nearest feature is computed for each application area polygon.
5. The nearest distance, azimuth, and direction are written to a CSV file.
6. A Matplotlib window is generated to visualize the results.

The plugin’s design follows reproducibility and transparency principles, including provenance disclosure of AI-assisted code generation (OpenAI GPT-family), with all final logic verified and rewritten by the author.

# Example usage

After installation in QGIS, users can:
1. Open the *Nearest Analysis* plugin from the QGIS Plugin menu.
2. Select a local vector layer (e.g., shapefile) as the “Application Area.”
3. Choose a remote API layer (e.g., EPA WFS dataset).
4. Optionally adjust the buffer distance or output fields.
5. Run the analysis to generate:
   - A CSV file containing the nearest feature and its distance/direction.
   - A Matplotlib map illustrating the spatial relationship.

# Impact and applications

The plugin enables:
- Faster, reproducible proximity analysis for environmental assessment and infrastructure planning.
- Direct integration with public datasets (e.g., Irish EPA geoserver) without manual downloads.
- Consistent coordinate systems and units for accurate geospatial computation.
- Immediate visual and tabular outputs for professional reporting.

# Acknowledgements

This work was developed at Trinity College Dublin, with valuable discussions and feedback from Rory Brickenden.  
Initial prototypes were assisted by a large language model (OpenAI GPT family), and all final code has been refactored, documented, and validated by the author.

# References

See `paper.bib` for citations of referenced software and libraries.
