# RivaPlan — Tool documentation

RivaPlan is an experimental, open-source **QGIS Processing plugin** for river planform analysis. **Seven tools** are documented below, with input/output guidance, advanced controls and representative interface and result screenshots.

<p align="center"><img src="images/introduce_rivaplan.PNG" alt="RivaPlan provider with seven tools in QGIS Processing Toolbox" width="900"></p>

## Processing tools

| Tool | Purpose | User guide |
|---|---|---|
| Bank lines to centerline | Create a corridor centerline from paired banklines using Voronoi geometry and Dijkstra tracing; includes adjustable densification and bank-exclusion buffer. | [Centerline generation](centerline.md) |
| Reverse Line Direction | Reverse a line's vertex order while preserving shape and attributes. | [Reverse Line Direction](reverse_line_direction.md) |
| River Width | Measure perpendicular bank-to-bank widths with automatic/manual transect search and `NO_WIDTH` diagnostic output. | [River Width](width.md) |
| Centerline shifting | Compare older/newer centerlines with signed displacement and QC review flags. | [Centerline shifting](centerline_shifting.md) |
| Bankline Shifting | Measure old-to-new bank movement with inward/outward classification and QC review flags. | [Bankline Shifting](bankline_shifting.md) |
| Line sinuosity | Compute fixed or sliding-window sinuosity with completeness status. | [Sinuosity](sinuosity.md) |
| Local Curvature | Calculate angle-gradient and/or three-point circumcircle curvature. | [Local Curvature](curvature.md) |

## Quick-start example

1. Open QGIS and install or enable **RivaPlan**. As an experimental plugin, it may require enabling **Show also experimental plugins** in the Plugin Manager.
2. Load a matching pair of left/right river-bankline vector layers from [`sample_data/`](../sample_data/) if supplied with your checkout. Confirm their CRS and geometry validity.
3. Open **Bank lines to centerline** and select one continuous left-bank and one continuous right-bank feature. Use a projected CRS with metre units. Start with **5 m densification** and **0.50 m bank-exclusion buffer**.
4. Run the algorithm, inspect the generated centerline and check its Processing Log for warnings.
5. Check upstream/downstream orientation; use **Reverse Line Direction** if the vertex order is reversed.
6. Run **River Width** on the centerline and same-date banks. Check `QC_Status`, especially `NO_WIDTH`.
7. For multi-date analyses, supply old/new centerlines or banks to the migration tools; review `QC_Flags` and use a consistent downstream direction.
8. Run sinuosity and curvature on a continuous centerline, using comparable window/sampling settings across dates.

> The folder above is a repository-relative location. The natural and synthetic datasets are being organized for the v2.1.1 documentation update; verify that the referenced files are present before treating them as published downloads.

## System requirements and limitations

- **Declared QGIS minimum:** 3.28, per plugin metadata. This is a declared requirement, **not evidence that every algorithm has been tested against QGIS 3.28**.
- **Verified development environment:** Windows QGIS 3.40.13; automated regression checks have also passed in Ubuntu CI for the earlier seven-tool release. The newly revised adjustable-buffer centerline algorithm requires fresh runtime testing before v2.1.1 publication.
- **CRS:** metric analyses require a projected working CRS with metre units.
- **Centerline model:** two distinct, continuous banklines bounding one corridor; does not individually resolve multiple branches in entire river networks.
- **Capacity:** no universal maximum vertex count or complete network-size benchmark has been established. Runtime depends on reach complexity and vertex density.
- **Quality control:** flagged measurements require visual review; a QC flag is not automatic proof of error.

## Further information

- [Main repository and installation instructions](../README.md)
- [Sample datasets](../sample_data/) — available as the dataset folder is populated
- [Regression tests](../tests/)
- [Project source code](https://github.com/Rohit-Sharma-21/RivaPlan)
