# Changelog

Notable RivaPlan changes are recorded by version. Version numbers follow the `MAJOR.MINOR.PATCH` convention. RivaPlan remains an experimental QGIS Processing plugin.

## [2.1.1] - 2026-11-12

### Focused centerline-generation update

- Made the **bank-exclusion buffer distance** user-adjustable in **Bank lines to centerline** (default: **0.50 m**). Reducing this distance may retain additional Voronoi connections in narrow or geometrically complex corridors; it does not guarantee a successful route for every input.
- Refined centerline processing by clipping the Voronoi linework to the channel polygon before segment cleaning and filtering, then tracing a continuous route directly through the retained graph using Dijkstra's algorithm.
- Added diagnostic processing-log checks for whether the generated centerline remains within the channel polygon and crosses either input bankline.
- Improved the centerline tool's advanced-parameter descriptions and processing feedback.

### Documentation and reproducibility

- Expanded the GitHub documentation for all seven existing tools, including interface screenshots, input/output descriptions, advanced settings, quick-start guidance, and known limitations.
- Organized example input datasets for the Ghaghara and Rapti Rivers (1989 and 2025) and nine synthetic channel configurations (S01–S09), with banklines, reference centerlines, and channel polygons where supplied.
- Re-ran the existing standalone regression suite after the centerline-script change: **16/16 checks passed on Windows**. The adjustable-buffer behavior was also checked interactively in QGIS. These results do not establish a maximum supported dataset size or complete compatibility across QGIS versions.

**Scope:** v2.1.1 is a targeted maintenance update to the centerline-generation tool plus documentation and sample data. The other six analysis algorithms are not claimed to have undergone new methodological changes in this release.

## [2.1.0] - 2026-11-11

### Major seven-tool and quality-control update

- Added **Reverse Line Direction**, expanding the plugin from six to **seven** Processing tools.
- Improved **Bank lines to centerline** with more robust cross-bank end-cap selection, input-geometry validation, adjustable bankline densification (**5 m default**), and per-stage timing logs.
- Improved **River Width** through configurable transect search distances and more reliable opposing-bank intersection selection, with status reporting for unresolved measurements.
- Revised **Centerline shifting** and **Bankline Shifting** with downstream-oriented signed-displacement conventions, improved intersection handling, and diagnostic quality-control flags including `SHARP_BEND`, `SHIFT_SPIKE`, and `IRREGULAR_PROGRESSION` (including old/new progression checks where applicable).
- Added inward/outward bank-adjustment interpretation to bankline-shifting outputs.
- Improved **Line sinuosity** fixed/sliding-window analysis and handling of incomplete windows.
- Improved **Local Curvature** input validation, numerical safeguards, and the angle-gradient/three-point method workflows.
- Added 16 automated regression checks and a GitHub Actions workflow. The Windows regression suite and Ubuntu CI passed following the QGIS field-type compatibility correction.

Version 2.1.0 was uploaded to the official QGIS Plugin Repository on 11 November 2026. Approval/public availability should be checked on the repository listing rather than inferred from upload alone.

## [2.0.0] - 2026-07-15

### Earlier six-tool public release

- Provided six river planform Processing tools: centerline generation, river width, bankline shifting, centerline shifting, sinuosity, and local curvature.
- Made RivaPlan available through the official QGIS Plugin Repository as an experimental plugin.

## Earlier development

Earlier experimental and reviewer-access versions preceded the QGIS repository releases. The earlier reviewer-access Zenodo archive is a separate version and should not be cited as the DOI for v2.1.0 or v2.1.1.
