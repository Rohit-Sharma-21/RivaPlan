# Centerline Shifting

The **Centerline shifting** tool quantifies lateral migration between river centerlines representing two different time periods.

The newer centerline is used as the reference for generating regularly spaced chainage points and perpendicular transects.

---

## Tool Location

In QGIS, open:

**Processing Toolbox → RivaPlan → Centerline shifting**

---

## Input Parameters

| Parameter | Description |
|---|---|
| **Chainage Interval** | Distance between consecutive measurement locations. The default value is 100 m. |
| **New Centerline** | Centerline representing the more recent time period. |
| **Old Centerline** | Centerline representing the earlier time period. |
| **Working CRS (advanced)** | Optional projected CRS in metres; defaults to newer centerline CRS. |
| **Transect search method** | Automatic full extent diagonal on each side, or manual. |
| **Manual search distance on EACH side (m)** | Optional search limit; default 500 m if manual mode is selected. |
| **Centerline Shifting (Hub Lines)** | Output line layer representing migration between the new and old centerlines. |

---

## Tool Interface

<p align="center">
  <img src="images/centerline_shifting_tool.PNG" alt="RivaPlan Centerline Shifting tool" width="900">
</p>

---

## Method

Centerline shifting is calculated through the following workflow:

1. The new and old centerlines are reprojected to the selected working CRS.

2. Chainage points are generated at the specified interval along the **new centerline**.

3. Perpendicular transects are generated at each chainage location.

4. The transects are intersected with the old centerline.

5. Candidate connections are constructed between the new-centerline chainage points and the corresponding old-centerline intersections.

6. Where multiple intersections occur for one chainage, the shortest connection to the principal chainage point is retained.

7. The length of the retained connection is calculated as the centerline-shifting magnitude.

8. The local orientation of the new centerline and the direction of the shifting line are compared to classify movement as Left or Right.

9. A signed shifting value is generated for directional analysis.

---

## Output

The output is a line layer connecting corresponding positions on the new and old centerlines.

<p align="center">
  <img src="images/centerline_shifting_output.PNG" alt="Centerline shifting output generated using RivaPlan" width="900">
</p>

Important output attributes include:

| Attribute | Description |
|---|---|
| **Chainage** | Distance along the new centerline. |
| **Shift** | Magnitude of centerline displacement. |
| **Direction** | Direction of movement: Left, Right, or No Shift. |
| **SignedShift** | Signed newer-vs-older displacement relative to downstream-oriented reference: **negative downstream-left; positive downstream-right**. |
| **QC_Status** | `VALID`, `REVIEW` or an unresolved-intersection status. |
| **QC_Flags** | Diagnostic reasons such as `SHARP_BEND`, `SHIFT_SPIKE` or `IRREGULAR_PROGRESSION`. |

---

## Reference Centerline

The **new centerline** acts as the reference geometry.

Therefore:

- chainage locations are generated along the new centerline,
- perpendicular transects are oriented according to the new centerline, and
- shifting distances are measured from the new centerline toward the old centerline.

This convention should be used consistently when comparing different time periods.

---

## Chainage Interval

The selected chainage interval determines how frequently centerline migration is sampled.

Smaller intervals provide greater spatial detail, whereas larger intervals provide a more generalized representation of river migration.

---

## Input Data Recommendations

For reliable results:

- The old and new centerlines should represent approximately the same river reach.
- Centerlines should follow a consistent upstream-to-downstream orientation where possible.
- Correct major geometry errors before analysis.
- Use a projected CRS.
- A metre-based CRS is recommended for measurements in metres.
- Select a chainage interval suitable for the river size and degree of curvature.

---

## Notes

If several intersections occur at one chainage, the algorithm retains the intersection producing the shortest connection to the corresponding new-centerline chainage point.

Intermediate processing layers are temporary; the final hub-line layer is returned as the primary output.

## Advanced parameters and review flags

| Setting | Default | Meaning |
|---|---:|---|
| Sharp-bend threshold | 10° | Flags abrupt local directional changes near a transect (`SHARP_BEND`). |
| Shift-spike multiplier | 2.0 | Compares a measured shift against nearby samples (`SHIFT_SPIKE`). |
| Centerline progression lower factor | 0.5 | Lower expected along-old-centerline progression relative to the chainage step. |
| Centerline progression upper factor | 1.5 | Upper expected along-old-centerline progression (`IRREGULAR_PROGRESSION` if violated). |

QC flags signal measurements needing inspection; valid measured displacement lines are not automatically deleted. Multiple line intersections are resolved by selecting the nearest crossing **along the perpendicular transect**. Ensure the newer centerline runs upstream to downstream before interpreting signed displacement. Reversing reference direction changes the sign convention.

[Return to tool documentation](README.md)
