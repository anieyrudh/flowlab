"""Body-fitted all-hex Y-junction emitted directly as an OpenFOAM polyMesh.

The Cartesian Y-junction in :mod:`server.flowlab.y_junction` admits whole cells
whose centres fall inside the declared circular primitives, so its walls are
staircases whose area tends to ``4/pi`` times the analytic area at *every* cell
size.  That bias never refines away.  This module replaces the realization (not
the declared geometry) with a body-fitted, logically structured, all-hexahedral
mesh whose wall points sit exactly on the analytic cylinders, and writes
``constant/polyMesh`` itself so the FlowLab cell index *is* the OpenFOAM cell
index.

Topology - septum-split trifurcation butterfly (SSTB)
----------------------------------------------------
Three equal-radius cylinders meet at the origin: the inlet along ``-x`` and two
branches at ``+/-30`` degrees.  Their pairwise surface intersections are planar
ellipses lying in the three axis-bisector planes, and all three bisector planes
contain the ``z`` axis.  The two triple points ``(0, 0, +/-R)`` are the ends of
the ``z``-axis chord shared by all three seams.  That gives an exact
decomposition of the junction core into three *chisel-ended* O-grid sweeps:

* the three internal separating surfaces are the planar half-ellipse regions
  bounded by half a seam ellipse and the central ``z``-axis segment;
* each leg region is its own cylinder truncated by the two bisector planes that
  bound its azimuthal sector, i.e. a cylinder with a hinged wedge ("chisel")
  end, hinged on the ``z`` diameter of its own cross-section;
* the cut planes sit beyond every seam - inlet at ``x = -R``, branches at axial
  ``2R`` - so each leg is a clean full circle there.

Two invariants differ from :mod:`server.flowlab.full_ogrid`:

1. ``y = 0`` (the septum) and ``z = 0`` must be *mesh planes*, not block
   diagonals.  The core diamond is therefore rotated 45 degrees so its corners
   sit at 45/135/225/315 degrees; the core block becomes an axis-aligned square
   whose ``first = 0`` and ``second = 0`` grid lines are exact.  That requires
   ``coreCellsPerSide`` even and ``circumferentialCells % 8 == 0``.
2. The conformity relation is re-derived for the rotated orientation: each side
   of the rotated core square still subtends exactly one 90-degree arc of the
   wall, so ``coreCellsPerSide == circumferentialCells // 4`` still holds.

Irregular internal edges are unavoidable and legal here: a ball with three disc
ports is not a single structured index space.  Six cells meet around the
central ``z``-axis edge, and two cells meet around each re-entrant seam edge.
No cell is collapsed to fake structure.

Cell ownership comes from a forward cursor walk over the declared block order
``inlet-leg -> upper-branch-leg -> lower-branch-leg -> junction-core`` and is
never inferred from coordinates.  Point identity across legs comes from shared
logical labels; there is no coordinate-tolerance matching anywhere.

This module is product geometry source, not validation evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from .ogrid_polymesh import (
    SIDE_FACE_BY_EDGE,
    SWEEP_END_FACE,
    SWEEP_START_FACE,
    OGridBlock,
    OGridBlockSet,
    OGridFrame,
    OGridPatch,
    OGridRegion,
    write_polymesh,
)


__all__ = [
    "JUNCTION_OGRID_ARTIFACT_ID",
    "PATCH_ORDER",
    "REGION_ORDER",
    "Y_JUNCTION_OGRID_ARTIFACT_SCHEMA",
    "Y_JUNCTION_OGRID_REPRESENTATION",
    "Y_JUNCTION_OGRID_SCHEMA",
    "YJunctionOGridSpec",
    "write_polymesh",
    "y_junction_block_set",
]


Y_JUNCTION_OGRID_SCHEMA = "flowlab.y-junction-ogrid-polymesh.v1"
Y_JUNCTION_OGRID_REPRESENTATION = "septum-split-trifurcation-butterfly-direct-polymesh"
Y_JUNCTION_OGRID_ARTIFACT_SCHEMA = "flowlab.generated-region-artifact.v1"
# Deliberately distinct from ``generated:y-junction:junction-core:v1`` so the
# retained Cartesian decomposition is never silently reused for this one.
JUNCTION_OGRID_ARTIFACT_ID = "generated:y-junction-ogrid:junction-core:v1"

PATCH_ORDER = ("inlet", "outletUpper", "outletLower", "walls")
PATCH_TYPES = {
    "inlet": "patch",
    "outletUpper": "patch",
    "outletLower": "patch",
    "walls": "wall",
}
REGION_ORDER = ("inlet-leg", "upper-branch-leg", "lower-branch-leg", "junction-core")

# Cross-section quads are emitted counter-clockwise with the outward radial
# edge always spanning local corners 1 and 2, so the wall face of a swept cell
# is a single fixed hex face index.
WALL_FACE = SIDE_FACE_BY_EDGE[1]

_ROOT_THREE = math.sqrt(3.0)
# tan(15 deg): slope of an inlet/branch bisector plane against the inlet axis.
_INLET_SEAM_SLOPE = 2.0 - _ROOT_THREE
# cot(30 deg): slope of the septum plane against a branch axis.
_CROTCH_SEAM_SLOPE = _ROOT_THREE


def _positive_finite(value: float, label: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{label} must be a finite positive SI value.")
    return number


def _integer_at_least(value: int, minimum: int, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"{label} must be an integer of at least {minimum}.")
    return value


@dataclass(frozen=True)
class YJunctionOGridSpec:
    """Exact SI geometry and logical resolution for one body-fitted Y-junction.

    ``inlet_length_m`` and ``branch_length_m`` are measured from the junction
    origin along the respective axis to that leg's port plane.  The junction
    core occupies the inlet axis out to ``radius_m`` and each branch axis out to
    ``2 * radius_m``, so both lengths must exceed their cut plane.
    """

    radius_m: float
    inlet_length_m: float
    branch_length_m: float
    annular_radial_cells: int
    circumferential_cells: int
    core_cells_per_side: int
    inlet_leg_axial_cells: int
    branch_leg_axial_cells: int
    junction_axial_cells: int
    branch_angle_degrees: float = 30.0
    annular_radial_expansion: float = 1.0

    def __post_init__(self) -> None:
        _positive_finite(self.radius_m, "Y-junction O-grid radius")
        _positive_finite(self.inlet_length_m, "Y-junction O-grid inlet length")
        _positive_finite(self.branch_length_m, "Y-junction O-grid branch length")
        _integer_at_least(self.annular_radial_cells, 2, "Y-junction O-grid annularRadialCells")
        _integer_at_least(self.circumferential_cells, 16, "Y-junction O-grid circumferentialCells")
        _integer_at_least(self.core_cells_per_side, 4, "Y-junction O-grid coreCellsPerSide")
        _integer_at_least(self.inlet_leg_axial_cells, 1, "Y-junction O-grid inletLegAxialCells")
        _integer_at_least(self.branch_leg_axial_cells, 1, "Y-junction O-grid branchLegAxialCells")
        _integer_at_least(self.junction_axial_cells, 2, "Y-junction O-grid junctionAxialCells")
        _positive_finite(
            self.annular_radial_expansion, "Y-junction O-grid annularRadialExpansion"
        )
        if not math.isclose(
            float(self.branch_angle_degrees), 30.0, rel_tol=0.0, abs_tol=1.0e-12
        ):
            raise ValueError(
                "The body-fitted Y-junction requires exactly +/-30-degree branches."
            )
        if self.circumferential_cells % 8 != 0:
            raise ValueError(
                "Y-junction O-grid circumferentialCells must be divisible by eight so the "
                "septum plane y=0 and the symmetry plane z=0 are both mesh planes."
            )
        if self.core_cells_per_side != self.circumferential_cells // 4:
            raise ValueError(
                "Y-junction O-grid coreCellsPerSide must equal circumferentialCells/4 so "
                "every rotated-core-to-wall interface is conformal."
            )
        if self.core_cells_per_side % 2 != 0:
            raise ValueError(
                "Y-junction O-grid coreCellsPerSide must be even so the rotated core square "
                "carries the septum plane as a grid line."
            )
        if self.inlet_length_m <= self.inlet_cut_m:
            raise ValueError(
                "Y-junction O-grid inlet length must exceed the inlet cut plane at one radius."
            )
        if self.branch_length_m <= self.branch_cut_m:
            raise ValueError(
                "Y-junction O-grid branch length must exceed the branch cut plane at two radii."
            )

    # -- declared geometry -------------------------------------------------

    @property
    def angle_radians(self) -> float:
        return math.radians(self.branch_angle_degrees)

    @property
    def inlet_direction(self) -> tuple[float, float, float]:
        """Unit vector from the inlet port toward the junction origin."""

        return (1.0, 0.0, 0.0)

    @property
    def upper_direction(self) -> tuple[float, float, float]:
        return (_ROOT_THREE / 2.0, 0.5, 0.0)

    @property
    def lower_direction(self) -> tuple[float, float, float]:
        return (_ROOT_THREE / 2.0, -0.5, 0.0)

    @property
    def inlet_cut_m(self) -> float:
        """Axial distance from the origin to the inlet cut plane (``x = -R``)."""

        return self.radius_m

    @property
    def branch_cut_m(self) -> float:
        """Axial distance from the origin to each branch cut plane (``2R``)."""

        return 2.0 * self.radius_m

    @property
    def core_radius_m(self) -> float:
        return self.radius_m / 4.0

    # -- logical resolution ------------------------------------------------

    @property
    def circumferential_cells_per_quadrant(self) -> int:
        return self.circumferential_cells // 4

    def annular_radial_fractions(self) -> tuple[float, ...]:
        """Return the ``annularRadialCells + 1`` core-to-wall interface fractions.

        ``annular_radial_expansion`` is the width ratio of the wall-adjacent
        radial cell to the core-adjacent one, matching ``simpleGrading``.  A
        value below one clusters cells at the wall, which is the only lever
        that changes the *shape* of the wall-adjacent cell at the re-entrant
        crotch, where the septum plane shears the sweep by 60 degrees.
        """

        radial = self.annular_radial_cells
        ratio = self.annular_radial_expansion ** (1.0 / (radial - 1)) if radial > 1 else 1.0
        widths = [ratio**index for index in range(radial)]
        total = sum(widths)
        fractions = [0.0]
        cursor = 0.0
        for width in widths[:-1]:
            cursor += width
            fractions.append(cursor / total)
        fractions.append(1.0)
        return tuple(fractions)

    @property
    def cross_section_cell_count(self) -> int:
        core = self.core_cells_per_side
        return core * core + self.circumferential_cells * self.annular_radial_cells

    @property
    def cross_section_point_count(self) -> int:
        core = self.core_cells_per_side
        return (core + 1) * (core + 1) + self.circumferential_cells * self.annular_radial_cells

    @property
    def total_axial_cells(self) -> int:
        return (
            self.inlet_leg_axial_cells
            + 2 * self.branch_leg_axial_cells
            + 3 * self.junction_axial_cells
        )

    @property
    def cell_count(self) -> int:
        return self.cross_section_cell_count * self.total_axial_cells

    # -- analytic geometry -------------------------------------------------

    def seam_geometry(self) -> dict[str, Any]:
        """Exact seam vertices and extents of the declared circular primitives."""

        radius = self.radius_m
        return {
            "crotchPlane": "y=0",
            "crotchEllipseSemiAxesM": [2.0 * radius, radius],
            "crotchVertexM": [2.0 * radius, 0.0, 0.0],
            "crotchBranchAxialExtentM": _CROTCH_SEAM_SLOPE * radius,
            "inletBranchSeamVertexM": [
                -_INLET_SEAM_SLOPE * radius,
                radius,
                0.0,
            ],
            "inletBranchSeamRadiusM": radius / math.sin(math.radians(75.0)),
            "inletBranchInletAxialExtentM": _INLET_SEAM_SLOPE * radius,
            "triplePointsM": [[0.0, 0.0, radius], [0.0, 0.0, -radius]],
            "crotchFluidInteriorAngleDegrees": 300.0,
            "inletBranchFluidInteriorAngleDegrees": 210.0,
        }

    def wall_geometry(self) -> dict[str, float | int]:
        """Analytic and chordal wall areas of the declared union surface.

        The analytic area is the exact area of the union of the three cylinder
        surfaces truncated at their seams.  The chordal area is the area the
        body-fitted mesh realizes exactly: an ``N``-gon prism approximation of
        each cylinder, integrated against the seam by the trapezoid rule.
        """

        radius = self.radius_m
        ring = self.circumferential_cells
        analytic = 2.0 * math.pi * radius * (
            self.inlet_length_m + 2.0 * self.branch_length_m
        ) - radius * radius * (8.0 * _INLET_SEAM_SLOPE + 4.0 * _CROTCH_SEAM_SLOPE)

        chord = 2.0 * radius * math.sin(math.pi / ring)
        extent = 0.0
        for leg, length in (
            ("inlet", self.inlet_length_m),
            ("upper", self.branch_length_m),
            ("lower", self.branch_length_m),
        ):
            for index in range(ring):
                first, _second = _octant_circle_point(radius, index, ring)
                extent += length - abs(_roof_station(leg, first))
        chordal = chord * extent
        return {
            "wallFacetCount": ring,
            "analyticWallAreaM2": analytic,
            "chordalWallAreaM2": chordal,
            "areaRelativeDeficit": 1.0 - chordal / analytic,
        }

    def topology_manifest(self) -> dict[str, Any]:
        core = self.core_cells_per_side
        cross_cells = self.cross_section_cell_count
        return {
            "schema": Y_JUNCTION_OGRID_SCHEMA,
            "representation": Y_JUNCTION_OGRID_REPRESENTATION,
            "spatialDimension": 3,
            "cellTypes": ["hex"],
            "cellIdentity": "flowlab_mesh_order",
            "meshAuthority": "flowlab-direct-polymesh",
            "ownershipSource": "declared-block-order-cursor-walk",
            "geometryDerivedOwnershipAllowed": False,
            "blockCount": 30,
            "sectionCount": 6,
            "coreOrientation": "rotated-45-degree-core-square",
            "meshPlanes": ["y=0", "z=0"],
            "resolution": {
                "annularRadialCells": self.annular_radial_cells,
                "annularRadialExpansion": self.annular_radial_expansion,
                "circumferentialCells": self.circumferential_cells,
                "circumferentialCellsPerQuadrant": self.circumferential_cells_per_quadrant,
                "coreCellsPerSide": core,
                "inletLegAxialCells": self.inlet_leg_axial_cells,
                "branchLegAxialCells": self.branch_leg_axial_cells,
                "junctionAxialCells": self.junction_axial_cells,
                "crossSectionCellCount": cross_cells,
                "cellCount": self.cell_count,
            },
            "regions": [
                {"name": "inlet-leg", "role": "edge", "axialCells": self.inlet_leg_axial_cells},
                {
                    "name": "upper-branch-leg",
                    "role": "edge",
                    "axialCells": self.branch_leg_axial_cells,
                },
                {
                    "name": "lower-branch-leg",
                    "role": "edge",
                    "axialCells": self.branch_leg_axial_cells,
                },
                {
                    "name": "junction-core",
                    "role": "junction",
                    "axialCells": 3 * self.junction_axial_cells,
                    "artifactIdentity": {
                        "schema": Y_JUNCTION_OGRID_ARTIFACT_SCHEMA,
                        "artifactId": JUNCTION_OGRID_ARTIFACT_ID,
                        "generated": True,
                        "schematicOwner": None,
                    },
                },
            ],
            "patches": {
                "inlet": {"type": "patch", "faceCount": cross_cells},
                "outletUpper": {"type": "patch", "faceCount": cross_cells},
                "outletLower": {"type": "patch", "faceCount": cross_cells},
                "walls": {
                    "type": "wall",
                    "faceCount": self.circumferential_cells * self.total_axial_cells,
                },
            },
            "irregularEdges": {
                "centralAxisChord": {
                    "fromM": [0.0, 0.0, -self.radius_m],
                    "toM": [0.0, 0.0, self.radius_m],
                    "cellsPerEdge": 6,
                    "legal": True,
                },
                "seamEdges": {
                    "count": 3,
                    "cellsPerEdge": 2,
                    "reentrant": True,
                    "legal": True,
                },
            },
            "seamGeometry": self.seam_geometry(),
            "wallGeometry": self.wall_geometry(),
        }


# ---------------------------------------------------------------------------
# Rotated butterfly cross-section
# ---------------------------------------------------------------------------


def _octant_circle_point(radius: float, index: int, count: int) -> tuple[float, float]:
    """Return ``radius * (cos, sin)`` at angle ``2*pi*index/count``.

    The angle is reduced into the first octant and mapped back by exact
    coordinate swaps and negations, so the axis points are exactly on axis and
    the ring is bit-exactly symmetric about both axes.  That symmetry is what
    lets the septum plane and the ``z=0`` plane be exact mesh planes and lets
    the upper and lower branch cross-sections share septum points by label.
    """

    index %= count
    octant, remainder = divmod(index * 8, count)
    if remainder == 0:
        # Octant boundaries are named by an exact table rather than by a
        # trigonometric evaluation, because cos(pi/4) and sin(pi/4) differ by
        # one unit in the last place and would break the mirror identity.
        diagonal = radius * math.sqrt(0.5)
        return (
            (radius, 0.0),
            (diagonal, diagonal),
            (0.0, radius),
            (-diagonal, diagonal),
            (-radius, 0.0),
            (-diagonal, -diagonal),
            (0.0, -radius),
            (diagonal, -diagonal),
        )[octant]
    if octant % 2 == 0:
        angle = 2.0 * math.pi * remainder / (8.0 * count)
    else:
        angle = 2.0 * math.pi * (count - remainder) / (8.0 * count)
    first = radius * math.cos(angle)
    second = radius * math.sin(angle)
    return (
        (first, second),
        (second, first),
        (-second, first),
        (-first, second),
        (-first, -second),
        (-second, -first),
        (second, -first),
        (first, -second),
    )[octant]


def _core_boundary_lattice(index: int, count: int, core: int) -> tuple[int, int]:
    """Return the rotated-core lattice ``(iu, iv)`` under wall angle ``index``.

    The rotated core square's corners sit at 45/135/225/315 degrees, so each of
    its four sides subtends exactly one 90-degree arc.  With
    ``core == count // 4`` the side has as many points as the arc, which is the
    conformity relation for this orientation.
    """

    quarter = count // 4
    walk = (index - count // 8) % count
    side, local = divmod(walk, quarter)
    if side == 0:
        return (core - local, core)
    if side == 1:
        return (0, core - local)
    if side == 2:
        return (local, 0)
    return (core, local)


@dataclass(frozen=True)
class _Butterfly:
    """Rotated five-block butterfly with logical side and mirror structure."""

    points: tuple[tuple[float, float], ...]
    cells: tuple[tuple[int, int, int, int], ...]
    block_cells: tuple[tuple[str, tuple[int, ...]], ...]
    wall_cells: frozenset[int]
    signs: tuple[int, ...]
    mirror_first: tuple[int, ...]


def _angular_sign(index: int, count: int) -> int:
    quarter = count // 4
    index %= count
    if index in (quarter, 3 * quarter):
        return 0
    return 1 if index < quarter or index > 3 * quarter else -1


def _butterfly(spec: YJunctionOGridSpec) -> _Butterfly:
    """Return the rotated butterfly cross-section in ``(first, second)`` axes."""

    core = spec.core_cells_per_side
    ring = spec.circumferential_cells
    radial = spec.annular_radial_cells
    radius = spec.radius_m
    half = spec.core_radius_m / math.sqrt(2.0)

    labels: dict[tuple[Any, ...], int] = {}
    points: list[tuple[float, float]] = []
    signs: list[int] = []

    def register(key: tuple[Any, ...], point: tuple[float, float], sign: int) -> int:
        if key in labels:
            raise ValueError("Y-junction butterfly registered a cross-section point twice.")
        labels[key] = len(points)
        points.append(point)
        signs.append(sign)
        return labels[key]

    def core_point(iu: int, iv: int) -> tuple[float, float]:
        return (half * (2 * iu - core) / core, half * (2 * iv - core) / core)

    for iv in range(core + 1):
        for iu in range(core + 1):
            sign = 0 if 2 * iu == core else (1 if 2 * iu > core else -1)
            register(("c", iu, iv), core_point(iu, iv), sign)

    def ring_key(radial_index: int, angular_index: int) -> tuple[Any, ...]:
        angular_index %= ring
        if radial_index == 0:
            return ("c", *_core_boundary_lattice(angular_index, ring, core))
        return ("a", radial_index, angular_index)

    fractions = spec.annular_radial_fractions()
    for radial_index in range(1, radial + 1):
        fraction = fractions[radial_index]
        for angular_index in range(ring):
            inner = points[labels[ring_key(0, angular_index)]]
            outer = _octant_circle_point(radius, angular_index, ring)
            register(
                ("a", radial_index, angular_index),
                (
                    inner[0] + fraction * (outer[0] - inner[0]),
                    inner[1] + fraction * (outer[1] - inner[1]),
                ),
                _angular_sign(angular_index, ring),
            )

    cells: list[tuple[int, int, int, int]] = []
    block_cells: list[tuple[str, tuple[int, ...]]] = []
    wall_cells: set[int] = set()

    centre: list[int] = []
    for iv in range(core):
        for iu in range(core):
            centre.append(len(cells))
            cells.append(
                (
                    labels[("c", iu, iv)],
                    labels[("c", iu + 1, iv)],
                    labels[("c", iu + 1, iv + 1)],
                    labels[("c", iu, iv + 1)],
                )
            )
    block_cells.append(("center", tuple(centre)))

    quadrant_cells = spec.circumferential_cells_per_quadrant
    for quadrant in range(4):
        entries: list[int] = []
        for radial_index in range(radial):
            for local in range(quadrant_cells):
                angular_index = ring // 8 + quadrant * quadrant_cells + local
                index = len(cells)
                entries.append(index)
                cells.append(
                    (
                        labels[ring_key(radial_index, angular_index)],
                        labels[ring_key(radial_index + 1, angular_index)],
                        labels[ring_key(radial_index + 1, angular_index + 1)],
                        labels[ring_key(radial_index, angular_index + 1)],
                    )
                )
                if radial_index == radial - 1:
                    wall_cells.add(index)
        block_cells.append((f"wall-{quadrant}", tuple(entries)))

    mirror: list[int] = [0] * len(points)
    for key, index in labels.items():
        if key[0] == "c":
            mirror[index] = labels[("c", core - key[1], key[2])]
        else:
            mirror[index] = labels[("a", key[1], (ring // 2 - key[2]) % ring)]

    butterfly = _Butterfly(
        points=tuple(points),
        cells=tuple(cells),
        block_cells=tuple(block_cells),
        wall_cells=frozenset(wall_cells),
        signs=tuple(signs),
        mirror_first=tuple(mirror),
    )
    _validate_butterfly(butterfly, spec)
    return butterfly


def _signed_area(points: tuple[tuple[float, float], ...], quad: tuple[int, ...]) -> float:
    total = 0.0
    for index in range(4):
        current = points[quad[index]]
        following = points[quad[(index + 1) % 4]]
        total += current[0] * following[1] - current[1] * following[0]
    return total / 2.0


def _validate_butterfly(butterfly: _Butterfly, spec: YJunctionOGridSpec) -> None:
    """Fail closed on any cross-section defect that would break the sweep."""

    if len(butterfly.points) != spec.cross_section_point_count:
        raise ValueError("Y-junction butterfly point count disagrees with the declared spec.")
    if len(butterfly.cells) != spec.cross_section_cell_count:
        raise ValueError("Y-junction butterfly cell count disagrees with the declared spec.")
    claimed = [index for _name, indices in butterfly.block_cells for index in indices]
    if claimed != list(range(len(butterfly.cells))):
        raise ValueError("Y-junction butterfly blocks do not partition the cross-section.")
    for index, quad in enumerate(butterfly.cells):
        if len(set(quad)) != 4:
            raise ValueError(f"Y-junction butterfly cell {index} is collapsed.")
        if _signed_area(butterfly.points, quad) <= 0.0:
            raise ValueError(f"Y-junction butterfly cell {index} is not counter-clockwise.")
        sides = {butterfly.signs[label] for label in quad}
        if 1 in sides and -1 in sides:
            raise ValueError(
                f"Y-junction butterfly cell {index} straddles the septum plane; the septum "
                "must be a mesh line."
            )
    for index, sign in enumerate(butterfly.signs):
        first = butterfly.points[index][0]
        if sign == 0 and first != 0.0:
            raise ValueError("Y-junction butterfly hinge point is not exactly on the septum.")
        if sign != 0 and first * sign <= 0.0:
            raise ValueError("Y-junction butterfly point side disagrees with its coordinate.")
        partner = butterfly.mirror_first[index]
        if butterfly.mirror_first[partner] != index:
            raise ValueError("Y-junction butterfly septum mirror is not an involution.")
        if butterfly.points[partner] != (-first, butterfly.points[index][1]):
            raise ValueError("Y-junction butterfly septum mirror is not exact.")


# ---------------------------------------------------------------------------
# Chisel-ended sweeps
# ---------------------------------------------------------------------------


def _roof_station(leg: str, first: float) -> float:
    """Axial station where cross-section offset ``first`` meets its chisel plane.

    For the inlet both halves face a branch, so the roof is symmetric.  For a
    branch one half faces the inlet across the inlet/branch bisector and the
    other faces the crotch across the septum; which half is which is set by the
    frame's ``normal``, and the two branches use opposite conventions because a
    triangle of three legs cannot orient all three shared surfaces alike.
    """

    if leg == "inlet":
        return -_INLET_SEAM_SLOPE * abs(first)
    if leg == "upper":
        if first > 0.0:
            return _INLET_SEAM_SLOPE * first
        return -_CROTCH_SEAM_SLOPE * first
    if leg != "lower":
        raise ValueError(f"Y-junction O-grid leg {leg!r} is unknown.")
    if first > 0.0:
        return _CROTCH_SEAM_SLOPE * first
    return -_INLET_SEAM_SLOPE * first


def _leg_frames(spec: YJunctionOGridSpec) -> dict[str, OGridFrame]:
    """Right-handed sweep frames whose ``first`` axis carries the septum split.

    Each frame's ``binormal`` is ``+z`` and its ``normal`` is chosen so that the
    ``first > 0`` half of the inlet and the ``first > 0`` half of the upper
    branch land on the same bisector plane with the *same* parametrization, and
    likewise for the inlet and lower branch.  A triangle of three legs cannot
    have all three shared surfaces agree by identity, so the septum between the
    two branches pairs upper index ``i`` with lower index ``mirror_first[i]``.
    """

    root = _ROOT_THREE / 2.0
    return {
        "inlet": OGridFrame(
            origin=(0.0, 0.0, 0.0),
            tangent=(1.0, 0.0, 0.0),
            normal=(0.0, 1.0, 0.0),
            binormal=(0.0, 0.0, 1.0),
        ),
        "upper": OGridFrame(
            origin=(0.0, 0.0, 0.0),
            tangent=(root, 0.5, 0.0),
            normal=(-0.5, root, 0.0),
            binormal=(0.0, 0.0, 1.0),
        ),
        "lower": OGridFrame(
            origin=(0.0, 0.0, 0.0),
            tangent=(root, -0.5, 0.0),
            normal=(0.5, root, 0.0),
            binormal=(0.0, 0.0, 1.0),
        ),
    }


def _roof_key(leg: str, butterfly: _Butterfly, index: int) -> tuple[Any, ...]:
    """Return the shared logical label of a leg's chisel-end point."""

    sign = butterfly.signs[index]
    if sign == 0:
        return ("hinge", index)
    if leg == "inlet":
        return ("roof", "inlet-upper" if sign > 0 else "inlet-lower", index)
    if leg == "upper":
        return ("roof", "inlet-upper", index) if sign > 0 else ("roof", "septum", index)
    if sign > 0:
        return ("roof", "septum", butterfly.mirror_first[index])
    return ("roof", "inlet-lower", index)


def _roof_point(
    key: tuple[Any, ...], butterfly: _Butterfly
) -> tuple[float, float, float]:
    """Return the physical position of a chisel-end label.

    Each shared label is evaluated by exactly one closed-form expression, so
    both legs that reference it read the identical coordinate; nothing is ever
    matched by tolerance.
    """

    if key[0] == "hinge":
        return (0.0, 0.0, butterfly.points[key[1]][1])
    surface, index = key[1], key[2]
    first, second = butterfly.points[index]
    if surface == "inlet-upper":
        return (-_INLET_SEAM_SLOPE * first, first, second)
    if surface == "inlet-lower":
        return (_INLET_SEAM_SLOPE * first, first, second)
    return (-2.0 * first, 0.0, second)


def _section_layers(
    spec: YJunctionOGridSpec,
    butterfly: _Butterfly,
) -> dict[str, list[list[tuple[Any, ...]]]]:
    """Return, per swept section, the logical label of every point in every layer.

    Labels - not coordinates - are what make the leg/core cut planes and the
    three chisel-end surfaces conformal.
    """

    count = len(butterfly.points)
    indices = range(count)
    sections: dict[str, list[list[tuple[Any, ...]]]] = {}

    inlet_leg = [
        [("leg", "inlet", layer, index) for index in indices]
        for layer in range(spec.inlet_leg_axial_cells)
    ]
    inlet_leg.append([("cut", "inlet", index) for index in indices])
    sections["inlet-leg"] = inlet_leg

    for leg in ("upper", "lower"):
        branch_leg: list[list[tuple[Any, ...]]] = [
            [("cut", leg, index) for index in indices]
        ]
        branch_leg.extend(
            [("leg", leg, layer, index) for index in indices]
            for layer in range(1, spec.branch_leg_axial_cells + 1)
        )
        sections[f"{leg}-branch-leg"] = branch_leg

    junction = spec.junction_axial_cells
    inlet_core: list[list[tuple[Any, ...]]] = [[("cut", "inlet", index) for index in indices]]
    inlet_core.extend(
        [("core", "inlet", layer, index) for index in indices]
        for layer in range(1, junction)
    )
    inlet_core.append([_roof_key("inlet", butterfly, index) for index in indices])
    sections["junction-inlet"] = inlet_core

    for leg in ("upper", "lower"):
        branch_core: list[list[tuple[Any, ...]]] = [
            [_roof_key(leg, butterfly, index) for index in indices]
        ]
        branch_core.extend(
            [("core", leg, layer, index) for index in indices]
            for layer in range(1, junction)
        )
        branch_core.append([("cut", leg, index) for index in indices])
        sections[f"junction-{leg}"] = branch_core

    return sections


def _label_point(
    key: tuple[Any, ...],
    spec: YJunctionOGridSpec,
    butterfly: _Butterfly,
    frames: dict[str, OGridFrame],
) -> tuple[float, float, float]:
    """Evaluate one logical point label to its unique physical position."""

    kind = key[0]
    if kind in ("hinge", "roof"):
        return _roof_point(key, butterfly)

    radius = spec.radius_m
    junction = spec.junction_axial_cells
    if kind == "cut":
        leg, index = key[1], key[2]
        first, second = butterfly.points[index]
        station = -radius if leg == "inlet" else 2.0 * radius
        return frames[leg].point(station, first, second)

    leg = key[1]
    layer = key[2]
    index = key[3]
    first, second = butterfly.points[index]
    if kind == "leg":
        if leg == "inlet":
            start = -spec.inlet_length_m
            stop = -radius
            station = start + (stop - start) * layer / spec.inlet_leg_axial_cells
        else:
            start = 2.0 * radius
            stop = spec.branch_length_m
            station = start + (stop - start) * layer / spec.branch_leg_axial_cells
        return frames[leg].point(station, first, second)

    if kind != "core":
        raise ValueError(f"Y-junction O-grid point label kind {kind!r} is unknown.")
    if leg == "inlet":
        start = -radius
        stop = _roof_station(leg, first)
        station = start + (stop - start) * layer / junction
    else:
        start = _roof_station(leg, first)
        stop = 2.0 * radius
        station = start + (stop - start) * layer / junction
    return frames[leg].point(station, first, second)


def y_junction_block_set(spec: YJunctionOGridSpec) -> OGridBlockSet:
    """Return the body-fitted Y-junction as declared blocks, patches, regions.

    Blocks are emitted in the fixed order ``inlet-leg``, ``upper-branch-leg``,
    ``lower-branch-leg``, ``junction-core`` so a forward cursor walk gives each
    region one contiguous cell range.  Ownership is never derived from geometry.
    """

    if not isinstance(spec, YJunctionOGridSpec):
        raise ValueError("The body-fitted Y-junction requires a YJunctionOGridSpec.")
    butterfly = _butterfly(spec)
    frames = _leg_frames(spec)
    sections = _section_layers(spec, butterfly)

    labels: dict[tuple[Any, ...], int] = {}
    points: list[tuple[float, float, float]] = []

    def label(key: tuple[Any, ...]) -> int:
        existing = labels.get(key)
        if existing is not None:
            return existing
        index = len(points)
        labels[key] = index
        points.append(_label_point(key, spec, butterfly, frames))
        return index

    section_order = (
        ("inlet-leg", "inlet-leg", "inlet", None),
        ("upper-branch-leg", "upper-branch-leg", None, "outletUpper"),
        ("lower-branch-leg", "lower-branch-leg", None, "outletLower"),
        ("junction-inlet", "junction-core", None, None),
        ("junction-upper", "junction-core", None, None),
        ("junction-lower", "junction-core", None, None),
    )

    blocks: list[OGridBlock] = []
    patch_faces: dict[str, list[tuple[str, int, int]]] = {name: [] for name in PATCH_ORDER}
    region_blocks: dict[str, list[str]] = {name: [] for name in REGION_ORDER}

    for section, region, start_patch, end_patch in section_order:
        layers = [[label(key) for key in layer] for layer in sections[section]]
        intervals = len(layers) - 1
        for block_name, cell_indices in butterfly.block_cells:
            name = f"{section}-{block_name}"
            cells: list[tuple[int, int, int, int, int, int, int, int]] = []
            for interval in range(intervals):
                low = layers[interval]
                high = layers[interval + 1]
                for cell_index in cell_indices:
                    quad = butterfly.cells[cell_index]
                    local = len(cells)
                    cells.append(
                        (
                            low[quad[0]], low[quad[1]], low[quad[2]], low[quad[3]],
                            high[quad[0]], high[quad[1]], high[quad[2]], high[quad[3]],
                        )
                    )
                    if start_patch is not None and interval == 0:
                        patch_faces[start_patch].append((name, local, SWEEP_START_FACE))
                    if end_patch is not None and interval == intervals - 1:
                        patch_faces[end_patch].append((name, local, SWEEP_END_FACE))
                    if cell_index in butterfly.wall_cells:
                        patch_faces["walls"].append((name, local, WALL_FACE))
            blocks.append(OGridBlock(name=name, cells=tuple(cells)))
            region_blocks[region].append(name)

    patches = tuple(
        OGridPatch(name=name, type=PATCH_TYPES[name], faces=tuple(patch_faces[name]))
        for name in PATCH_ORDER
    )
    regions = tuple(
        OGridRegion(name=name, block_names=tuple(region_blocks[name])) for name in REGION_ORDER
    )
    block_set = OGridBlockSet(
        points=tuple(points), blocks=tuple(blocks), patches=patches, regions=regions
    )

    if block_set.cell_count != spec.cell_count:
        raise ValueError("Y-junction O-grid cell count disagrees with the declared spec.")
    expected_points = (
        # Every interior layer of every section, plus the three shared cut
        # planes, plus the three chisel-end half sections and the shared hinge.
        len(butterfly.points)
        * (
            spec.inlet_leg_axial_cells
            + 2 * spec.branch_leg_axial_cells
            + 3 * (spec.junction_axial_cells - 1)
            + 3
        )
        + 3 * (len(butterfly.points) - _hinge_count(butterfly)) // 2
        + _hinge_count(butterfly)
    )
    if len(points) != expected_points:
        raise ValueError("Y-junction O-grid point count disagrees with the declared topology.")
    return block_set


def _hinge_count(butterfly: _Butterfly) -> int:
    return sum(1 for sign in butterfly.signs if sign == 0)


def y_junction_polymesh(
    spec: YJunctionOGridSpec, *, root: str = "constant/polyMesh"
) -> dict[str, str]:
    """Return the ``constant/polyMesh`` text for one body-fitted Y-junction."""

    return y_junction_block_set(spec).to_polymesh(root=root)


def y_junction_manifest(spec: YJunctionOGridSpec) -> dict[str, Any]:
    """Return the declared topology plus realized block and region ranges."""

    block_set = y_junction_block_set(spec)
    manifest = spec.topology_manifest()
    region_ranges = block_set.region_ranges()
    block_ranges = block_set.block_ranges()
    manifest["pointCount"] = len(block_set.points)
    manifest["cellCount"] = block_set.cell_count
    manifest["blocks"] = [
        {
            "name": block.name,
            "cellStart": block_ranges[block.name][0],
            "cellCount": block_ranges[block.name][1],
        }
        for block in block_set.blocks
    ]
    for region in manifest["regions"]:
        start, count = region_ranges[region["name"]]
        region["cellStart"] = start
        region["cellCount"] = count
        region["ownershipSource"] = (
            "dedicated-generated-artifact"
            if region["name"] == "junction-core"
            else "declared-block-order-cursor-walk"
        )
    return manifest
