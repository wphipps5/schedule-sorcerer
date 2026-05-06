# Pathology Residency Schedule Solver

This directory contains a CP-SAT scheduling tool for building a 52-week pathology residency rotation schedule. The solver combines a partially fixed template, rotation requirements, vacation preferences, block limits, blackout rules, and service coverage targets to produce a complete resident-by-week schedule.

The implementation is in `schedule-residents.py` and uses Google OR-Tools.

## Scheduling Model

The schedule horizon is 52 weeks. Each resident is assigned exactly one rotation per week. A partially completed template can fix known assignments before optimization, and the solver fills the remaining blank weeks.

The model supports cohort-level curriculum requirements with resident-specific modifiers. Rotation totals are enforced over the full year, including both template-fixed and solver-created assignments.

## Cohorts

Residents are grouped into training cohorts. The default input files use six cohort labels:

| Cohort | Description |
|---|---|
| `AP1` | First-year anatomic pathology |
| `AP2` | Second-year anatomic pathology |
| `AP3` | Third-year or customized anatomic pathology track |
| `CP1` | First-year clinical pathology |
| `CP2` | Second-year clinical pathology |
| `CP3` | Third-year or customized clinical pathology track |

The solver does not hard-code cohort counts. It reads residents and cohort labels from `input/residents.csv`.

## Input Files

All required input files are read from `input/`.

### `rotations.csv`

Defines all rotations, cohort-specific min/max week ranges, preferred staffing, capacity, and required minimum staffing.

| Column | Description |
|---|---|
| `Rotation` | Rotation name. Values are normalized to uppercase. |
| `AP1_min` / `AP1_max` through `CP3_min` / `CP3_max` | Base minimum and maximum week counts for each cohort. |
| `Preferred_n` | Preferred number of residents assigned to the rotation each week. `NA` disables the preference term. |
| `Capacity_max` | Hard per-week maximum headcount. `NA` means no maximum. |
| `Required_min` | Soft minimum coverage target used for service shortage penalties. |

### `residents.csv`

Lists residents, their cohort, and per-rotation modifiers. The first two columns are `Resident` and `Cohort`; rotation columns contain signed integer offsets added to the cohort base min/max values from `rotations.csv`.

For example, a modifier of `1` raises both the minimum and maximum requirement for that resident on that rotation by one week. A modifier of `-1` lowers both by one week. Blank cells are treated as zero.

Trailing summary columns such as `Non_Elective` and `Sum` may be present for human review. The solver uses only columns matching rotation names.

### `template-a.csv`

Partial schedule template. Required columns are `Week`, `Date`, followed by one column per resident. Each resident-week cell is either blank or contains a fixed rotation name.

Blank cells are available for solver assignment. Nonblank cells are fixed and cannot be changed by the solver.

The script currently solves the template listed in `TEMPLATES`, which defaults to `input/template-a.csv`.

### `vac_pref.csv`

Vacation preference file. Each row contains a `Resident`, `Cohort`, and up to four vacation slots:

| Column Pattern | Description |
|---|---|
| `Vac1`, `Vac2`, `Vac3`, `Vac4` | Primary preferred dates. |
| `Vac1_Alt`, `Vac2_Alt`, `Vac3_Alt`, `Vac4_Alt` | Optional alternate dates. |

Dates must match dates in the template. For each slot with at least one valid open date, the solver enforces exactly one vacation assignment among the listed primary/alternate dates. If the template already fixes vacation on one of the slot dates, that slot is treated as satisfied.

### `rotation_blocks.csv`

Defines block-count limits and minimum block sizes by rotation.

| Column | Description |
|---|---|
| `Rotation` | Rotation name. |
| `Max_Blocks` | Maximum number of separate blocks allowed across the year. Template blocks count toward this limit. |
| `Min_Block_Size` | Minimum consecutive weeks required when a solver-created block starts. |

### `blackout_by_week.csv`

Global rotation blackout file. Rows are weeks; columns are rotations. A cell marked `x` means that rotation cannot be assigned to any resident in that week.

Template-fixed assignments are checked against these blackouts during diagnostic output.

### `resident_blackouts.csv`

Resident-specific rotation exclusion windows.

| Column | Description |
|---|---|
| `Resident` | Resident identifier matching `residents.csv`. |
| `Rotation` | Rotation to block. |
| `Start_Week` | First blocked week, 1-indexed. |
| `End_Week` | Last blocked week, 1-indexed and inclusive. |

## Hard Constraints

The solver enforces the following constraints:

- Each resident receives exactly one rotation per week.
- Template-fixed assignments are preserved.
- Each resident's annual rotation totals must fall within the min/max ranges computed from cohort requirements plus resident-specific modifiers.
- If a resident's computed maximum for a rotation is zero, the solver cannot add that rotation outside any already-fixed template placements.
- Global rotation blackouts are enforced.
- Resident-specific blackout windows are enforced when `USE_RESIDENT_BLACKOUT` is enabled.
- The solver cannot add additional `VA` weeks outside the template when `USE_NO_EXTRA_VA` is enabled.
- Weekly rotation capacity cannot exceed `Capacity_max`.
- Solver-created rotation blocks cannot exceed the remaining block allowance after template blocks are counted.
- Solver-created block starts must satisfy `Min_Block_Size`.
- Vacation preference slots are enforced when `USE_VACATION` is enabled.
- For rotations listed in `VAC_WRAP_ROTATIONS`, an open gap between two blocks of the same rotation must be assigned `VACATION`. Template-fixed non-vacation gap weeks are exempt from this wrap rule.
- A resident cannot cover more than one service in a week.

## Coverage Variables

The model separates assignment from service coverage. A resident directly assigned to a rotation can cover that rotation. For selected rotations, residents or cohorts configured for `OTHER` coverage may also cover a service while assigned to `OTHER`.

This mechanism lets the service minimum objective count coverage rather than only direct rotation assignments. Using `OTHER` to cover a service is penalized so direct assignment is preferred when possible.

## Soft Objective Terms

When not running in feasibility-only mode, the objective minimizes weighted penalties:

- Deviation from `Preferred_n` weekly staffing targets.
- Shortage below `Required_min` service coverage targets.
- Extra block starts beyond the first block for a resident-rotation pair.
- Service coverage supplied while assigned to `OTHER`.

Service minimum shortage weights are higher for selected high-priority services and default to a lower weight for other services.

## Configuration Constants

Important constants near the top of `schedule-residents.py`:

```python
FEASIBILITY_ONLY = False
USE_VACATION = True
USE_BLOCKS = True
USE_SERVICE_MIN = True
USE_RESIDENT_BLACKOUT = True
USE_NO_EXTRA_VA = True
BLOCK_FRAG_WEIGHT = 3
```

`VAC_WRAP_ROTATIONS`, `OTHER_COVER_ALLOWED`, `OTHER_COVER_RESIDENTS`, and `OTHER_COVER_COHORTS` define optional behavior for wrap rules and `OTHER`-based service coverage.

## Running The Solver

From this directory:

```sh
python schedule-residents.py
```

Useful optional arguments:

```sh
python schedule-residents.py --time 300
python schedule-residents.py --seed 7
python schedule-residents.py --run-id trial1
python schedule-residents.py --feasibility-only
python schedule-residents.py --hint-file output/template-a_solution.csv
```

If `--time` is not supplied, the default runtime limit is 3600 seconds. The solver uses 8 parallel search workers and logs CP-SAT search progress.

The script imports OR-Tools and attempts to install it with `pip` if it is missing.

## Outputs

Outputs are written to `output/`.

| File | Contents |
|---|---|
| `template-a_solution.csv` | Completed 52-week schedule in the same row/column layout as the template. |
| `template-a_metrics.txt` | Human-readable metrics report with solver status, model size, runtime, objective, coverage, block statistics, vacation satisfaction, and resident minimum checks. |
| `template-a_metrics.csv` | Single-row machine-readable metrics summary. |

If `--run-id` is supplied, the run ID is appended to the output filename stem. If the primary solution CSV is locked, the script writes a timestamped fallback solution file.

## Solver Hints

The solver can warm-start from an existing completed schedule using `--hint-file`. The hint file should have the same structure as a solution CSV: `Week`, `Date`, followed by resident columns.

If no hint file is supplied, the script builds a greedy initial hint from template assignments, vacation preferences, minimum rotation requirements, and `OTHER` padding.

## Notes

- The solver may return `FEASIBLE` instead of `OPTIMAL` if it reaches the runtime limit before proving optimality.
- `--feasibility-only` disables objective penalties and stops after the first feasible solution.
- Feasibility depends on the interaction between template-fixed assignments, rotation totals, vacation slots, block limits, blackout rules, capacity, and coverage requirements.
- Changing objective weights changes schedule tradeoffs but does not relax hard constraints.
