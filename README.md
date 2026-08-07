<p align="center">
  <img src="logo.png" alt="RivaPlan Logo" width="140">
</p>

<h1 align="center">RivaPlan</h1>

<p align="center">
  <b>An automated QGIS toolbox for river planform morphology analysis</b>
</p>

---

## About RivaPlan

**RivaPlan** is an open-source QGIS Processing plugin developed for automated and reproducible analysis of river planform morphology from vector river-bank data.

The plugin integrates a set of tools for generating representative river centrelines and quantifying important channel characteristics, including river width, bankline migration, centreline migration, sinuosity, and local curvature.

RivaPlan is designed to reduce repetitive GIS operations and provide a consistent workflow for river morphology and channel-change analysis within the QGIS environment.

---

## Tools Available in RivaPlan

| Tool | Description |
|---|---|
| **Centreline Generation** | Generates a representative river centreline from paired river banklines. |
| **River Width** | Measures channel width along the river using centreline-based transects. |
| **Bankline Shifting** | Quantifies lateral displacement between river banklines from different time periods. |
| **Centreline Shifting** | Measures spatial migration between river centrelines from different time periods. |
| **Sinuosity** | Calculates river sinuosity using reach-based analysis windows. |
| **Local Curvature** | Estimates local centreline curvature and identifies bend direction. |

---

## Installation

RivaPlan is available through the **official QGIS Plugin Repository**.

### Install directly from QGIS

1. Open **QGIS**.
2. Go to **Plugins → Manage and Install Plugins**.
3. Search for **RivaPlan**.
4. Select **RivaPlan**.
5. Click **Install Plugin**.
6. Open the **Processing Toolbox**.
7. The RivaPlan algorithms will appear under the **RivaPlan** processing provider.

> **Note:** RivaPlan is currently distributed as an experimental plugin. If it does not appear in the Plugin Manager, enable the option to display experimental plugins.

---

## General Workflow

A typical RivaPlan workflow consists of:

1. Prepare the river banklines.
2. Generate the river centreline.
3. Calculate river width.
4. Quantify bankline and/or centreline migration between different dates.
5. Calculate river sinuosity.
6. Estimate local curvature and bend direction.

The individual RivaPlan algorithms can also be used independently depending on the requirements of the analysis.

---

## Input Data

RivaPlan primarily works with vector river-bank and centreline datasets.

For reliable distance-based measurements:

- A **projected coordinate reference system (CRS)** is recommended.
- A metric CRS should be used when measurements are required in metres.
- River banklines should adequately represent the opposing channel margins.
- Input geometries should be checked for major topology errors before processing.

Specific input requirements are provided within each RivaPlan processing algorithm.

---

## Sample Data

Example datasets for testing RivaPlan are provided in the:

```text
sample_data/