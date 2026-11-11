# Changelog

All notable changes to **RivaPlan** will be documented in this file.

RivaPlan follows semantic versioning in the form:

`MAJOR.MINOR.PATCH`

---

## [2.1.0] - 2026-11-11

### Updated release (pre-publication)

- Added the **Reverse Line Direction** tool for general line layers.
- Improved bankline-to-centerline end-cap handling, geometry validation, adjustable densification spacing (5 m default), and per-stage processing-time reporting.
- Improved river-width intersection selection and adjustable transect search distances.
- Updated centerline and bankline shifting with downstream-oriented direction conventions and diagnostic **SHARP_BEND**, **SHIFT_SPIKE**, and **IRREGULAR_PROGRESSION** flags.
- Added separate inward/outward bank-adjustment attributes to bankline-shifting results.
- Updated line-sinuosity window handling, including incomplete terminal windows, and improved curvature validation and numerical safeguards.
- Expanded tool help text and clarifications for channel-corridor analysis.

*Compatibility verification, automated tests and release packaging remain to be completed before publication.*

---

## [2.0.0] - 2026-07-15

### Previous public release

RivaPlan v2.0.0 is distributed through the official QGIS Plugin Repository.

The plugin provides tools for:

- River centerline generation
- River width measurement
- Bankline shifting analysis
- Centerline shifting analysis
- Sinuosity analysis
- Local curvature estimation

---

## Earlier Development

Earlier versions of RivaPlan were used during development and testing prior to the current public QGIS plugin release.