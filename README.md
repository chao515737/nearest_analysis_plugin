# nearest_analysis_plugin# Nearest Analysis QGIS Plugin

This QGIS plugin performs nearest-feature analysis between a local "Application Area"
and remote datasets (EPA WFS or ArcGIS Feature Service).  
It calculates the nearest distance and geographic azimuth (0° = North, clockwise)
and exports the nearest feature to CSV.

## Requirements
- QGIS with PyQt5
- geopandas, shapely, requests, owslib, matplotlib

## Usage
1. Load your shapefile (application area) into QGIS.
2. Choose an API layer (EPA or ArcGIS).
3. Run the analysis to generate a CSV and plot.

## License
This project is licensed under the MIT License – see [LICENSE](./LICENSE) for details.
