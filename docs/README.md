# RivaPlan Documentation

This directory contains detailed documentation for the **RivaPlan QGIS plugin**.

RivaPlan provides six processing tools for automated river planform morphology analysis.

---

## Processing Tools

### 1. Centerline Generation

Generate a representative river centerline from paired left and right river banklines using a filtered Voronoi network and Dijkstra shortest-path approach.

[Read the Centerline Generation documentation](centerline.md)

---

### 2. River Width

Measure river width at user-defined chainage intervals using transects generated perpendicular to the river centerline.

[Read the River Width documentation](width.md)

---

### 3. Bankline Shifting

Quantify lateral migration between banklines representing different time periods.

[Read the Bankline Shifting documentation](bankline_shifting.md)

---

### 4. Centerline Shifting

Measure lateral displacement between river centerlines representing different time periods.

[Read the Centerline Shifting documentation](centerline_shifting.md)

---

### 5. Sinuosity

Calculate river sinuosity using fixed-segment or sliding-window analysis.

[Read the Sinuosity documentation](sinuosity.md)

---

### 6. Local Curvature

Estimate local river-centerline curvature using the angle-gradient method, three-point circumcircle method, or both approaches.

[Read the Local Curvature documentation](curvature.md)

---

## Screenshots

Interface and example-output screenshots used throughout the documentation are stored in:

`docs/images/`

---

## Sample Data

Example datasets for testing RivaPlan are available in the repository:

`sample_data/`

---

## Main Repository

For installation instructions, project overview, citation information, source code, and licensing, return to the:

[Main RivaPlan repository](../README.md)