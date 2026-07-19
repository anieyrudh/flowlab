# Result Cell-Data Support Evidence - 2026-06-14

FlowLab now parses and exposes both point fields and cell fields from supported
VTK/VTU result files.

## Supported Additions

- Legacy ASCII VTK `CELL_DATA` with `SCALARS`, `VECTORS`, and OpenFOAM-style
  `FIELD` arrays.
- Legacy ASCII OpenFOAM patch `POLYDATA` files with `POLYGONS`, including
  point and cell `FIELD` arrays from inlet, outlet, and wall exports.
- ASCII VTU `CellData` `DataArray` values.
- Normalized `cellData.scalars` and `cellData.vectors` alongside existing
  `pointData`.
- Linear triangle (`5`), polygon surface (`7`), quad (`9`), tetrahedron (`10`),
  hexahedron (`12`), wedge (`13`), and pyramid (`14`) cells in both legacy VTK
  and VTU readers.
- Unique `fields` lists when the same field name appears in both point and cell
  data.
- Browser overlays use point fields first and fall back to cell fields when
  point fields are absent.
- Probe sampling uses nearest point for point fields and nearest cell center for
  cell fields.
- The result timeline uses the same selected point/cell field values when it
  computes per-snapshot min, max, and mean trend summaries.

## Real Output Check

Using the OpenFOAM smoke output from
`/tmp/flowlab-openfoam-mesh-quality-smoke.json`, FlowLab parsed
`VTK/case_50.vtk` and found:

- Fields: `U`, `cellID`, `p`
- Point scalar fields: `p`
- Cell scalar fields: `cellID`, `p`
- Cell vector fields: `U`
- Cell `p` tuple count: `12`
- Cell `U` tuple count: `12`

This improves visualization coverage for OpenFOAM and other solvers that emit
primary fields as cell data, common linear unstructured cells, or OpenFOAM
patch-level surface `POLYDATA`. It does not add binary VTK/VTU support or
arbitrary high-order/polyhedral cell support.
