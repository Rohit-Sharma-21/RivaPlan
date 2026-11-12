# RivaPlan sample data

This directory contains example river-bank datasets for learning and testing the **RivaPlan** QGIS Processing plugin. It includes two natural river reaches (Ghaghara and Rapti) and nine synthetic channel configurations. These files are intended to support the [main project README](../README.md) and the [tool guides](../docs/README.md).

## Folder structure

```text
sample_data/
├── README.md                        ← this document
└── input/
    ├── Ghaghara/
    │   ├── ghaghara_lb_1989.gpkg
    │   ├── ghaghara_rb_1989.gpkg
    │   ├── ghaghara_lb_2025.gpkg
    │   └── ghaghara_rb_2025.gpkg
    ├── Rapti/
    │   ├── rapti_lb_1989.gpkg
    │   ├── rapti_rb_1989.gpkg
    │   ├── rapti_lb_2025.gpkg
    │   └── rapti_rb_2025.gpkg
    └── Synthetic_Channels/
        ├── S01_Strat/
        ├── S02_GW/
        ├── S03_SW/
        ├── S04_GM/
        ├── S05_SM/
        ├── S06_GW_GM/
        ├── S07_GW_SM/
        ├── S08_SW_GM/
        └── S09_SW_SM/
```

> **Directory note:** The names above follow the current local folder structure. `S01_Strat` is retained as shown in the folder screenshot; the intended configuration is **Straight**. Each synthetic subfolder contains its own input files; their exact filenames and reference-centerline availability should be checked before the public release.

## 1. Natural river datasets

| River | Year | Left bank | Right bank | Description |
|---|---:|---|---|---|
| Ghaghara | 1989 | `ghaghara_lb_1989.gpkg` | `ghaghara_rb_1989.gpkg` | Outer boundaries of the selected river corridor |
| Ghaghara | 2025 | `ghaghara_lb_2025.gpkg` | `ghaghara_rb_2025.gpkg` | Outer boundaries of the selected river corridor |
| Rapti | 1989 | `rapti_lb_1989.gpkg` | `rapti_rb_1989.gpkg` | Opposing banklines of the selected river reach |
| Rapti | 2025 | `rapti_lb_2025.gpkg` | `rapti_rb_2025.gpkg` | Opposing banklines of the selected river reach |

Here `lb` means **left bank** and `rb` means **right bank**, viewed in the downstream direction. The year identifies the observation represented by the digitized bankline.

**CRS:** The previously documented natural-reach datasets use **EPSG:32644 (WGS 84 / UTM zone 44N)**. Confirm the CRS in QGIS for each layer rather than assigning it based only on this README. Synthetic datasets may use a different projected CRS; use the CRS stored in the actual files.

For the Ghaghara River, the banks define the **outer channel corridor**; islands, bars and secondary threads within the corridor are not represented as separate banks. The generated centerline is consequently a corridor-scale centerline, not a centerline for each individual thread.

## 2. Synthetic channel configurations

The nine configurations provide controlled test geometries representing combinations of planform curvature and channel-width variation.

| Folder | Code | Geometry |
|---|---|---|
| `S01_Strat` | S01 | Straight channel |
| `S02_GW` | S02 | Gradual width variation |
| `S03_SW` | S03 | Sudden width variation |
| `S04_GM` | S04 | Gentle meander |
| `S05_SM` | S05 | Strong meander |
| `S06_GW_GM` | S06 | Gradual width variation + gentle meander |
| `S07_GW_SM` | S07 | Gradual width variation + strong meander |
| `S08_SW_GM` | S08 | Sudden width variation + gentle meander |
| `S09_SW_SM` | S09 | Sudden width variation + strong meander |

**GW:** gradual width; **SW:** sudden width; **GM:** gentle meander; **SM:** strong meander.

These configurations are intended to evaluate how centerline extraction responds to varying channel geometry. If the corresponding **known/true reference centerlines** are provided in each folder, they can also be used for positional-accuracy comparisons. Do not treat a generated RivaPlan centerline as an independent known reference.

> **Research-data distinction:** These nine folders should contain the original synthetic configurations used for the primary nine-case validation, **not** the separately generated synthetic channels used for the length-sensitivity experiment. Their quantitative error statistics should not be mixed.

## 3. Quick-start: generate a river centerline

1. Open QGIS and enable **RivaPlan** in the Processing Toolbox. If installing through the official repository, enable experimental plugins in the Plugin Manager settings.
2. Load the left and right bank GeoPackages for the **same river and year**; for example, `input/Ghaghara/ghaghara_lb_2025.gpkg` and `input/Ghaghara/ghaghara_rb_2025.gpkg`.
3. Open **Processing Toolbox → RivaPlan → Bank lines to centerline**.
4. Select the left and right bank layers. Each layer should contain **one valid, continuous line feature** covering the intended reach; the two banklines must not intersect.
5. In **Advanced Parameters**, use an appropriate **projected CRS in metres** (the left-bank CRS is used by default), **Bankline densification interval = 5 m** and **Bank-exclusion buffer distance = 0.50 m** initially.
6. Select an output GeoPackage or temporary layer and click **Run**.
7. Inspect the resulting centerline against both banks. Review processing messages, particularly warnings concerning channel-polygon containment, bank crossings or disconnected graph routes. A successful run does not replace visual quality control.
8. If a narrow or highly sinuous channel produces disconnected Voronoi linework, try a smaller positive bank-exclusion buffer with otherwise unchanged inputs, and compare results carefully. The buffer does **not** guarantee a valid result for every geometry.

See the [centerline-generation guide](../docs/centerline.md) for a full explanation of the algorithm and its parameters.

## 4. Other reproducible workflows

- **Reverse Line Direction:** Verify upstream/downstream orientation independently. Use [Reverse Line Direction](../docs/reverse_line_direction.md) to reverse a line's vertex order if needed; its geometry remains unchanged.
- **River Width:** Use the generated centerline and the **same-date** left and right banks. Choose a chainage interval and inspect the `NO_WIDTH` flags for unresolved intersections. [Guide](../docs/width.md).
- **Centerline migration:** Generate centerlines for both dates and supply the correct older/newer lines. Interpret signed movement according to the downstream orientation of the reference line and examine QC flags. [Guide](../docs/centerline_shifting.md).
- **Bankline migration:** Compare **the same bank side** between 1989 and 2025 using a representative reference centerline; distinguish signed lateral displacement from inward/outward bank adjustment. [Guide](../docs/bankline_shifting.md).
- **Sinuosity and curvature:** Use a continuous, direction-checked centerline with consistent analysis intervals when comparing reaches or dates. [Sinuosity](../docs/sinuosity.md) · [Curvature](../docs/curvature.md).

## 5. Input requirements and limitations

- Distance-based tools require a suitable **projected CRS with metre units**. Never treat geographic degrees as metres.
- The centerline generator expects **exactly one continuous feature in each bank layer**, valid geometry, and two distinct, nonintersecting bank boundaries forming a single analysis corridor.
- The approach is intended for **reach-scale corridor analysis**. It does not automatically extract each channel in a braided network or determine river flow direction.
- Large files, dense vertices, complex bends and very small densification intervals may increase memory use or processing time. **No universal maximum supported vertex count has been established**.
- Outputs should be visually inspected. QC flags indicate measurements requiring review rather than automatically proving that they are wrong.
- These sample files are demonstrations, not a guarantee of accuracy for other reaches or datasets.

## 6. Data citation and provenance

When using these datasets, cite the RivaPlan software and the accompanying manuscript or dataset source as applicable. The natural river-bank datasets were prepared for RivaPlan research and demonstration; the synthetic geometries were generated for controlled centerline-validation experiments. **Detailed imagery sources, acquisition dates, digitization methods, synthetic generation parameters, redistribution permissions and dataset DOI (if any) should be documented before public release.**

**Project repository:** https://github.com/Rohit-Sharma-21/RivaPlan

**Software documentation:** [docs/README.md](../docs/README.md)

**Note:** The software license does not automatically establish the reuse license for every sample dataset. Specify data licensing separately if the dataset is intended for redistribution.
