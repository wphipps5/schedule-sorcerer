# Faculty Service Schedule Solver

This directory contains a CP-SAT scheduling tool for assigning faculty to daily clinical services. The solver reads a calendar template, faculty service weights, time-off requests, and optional scheduling preferences, then writes a completed schedule and summary report to `output/`.

The implementation is in `schedule-faculty.py` and uses Google OR-Tools.

## Input Files

All required input files are read from `input/`.

| File | Purpose |
|---|---|
| `template.csv` | Calendar template. Required columns are `Day`, `Date`, and `DotW`, followed by optional faculty columns. Blank faculty cells are available for solver assignment. Fixed non-service values block that faculty member on that date. Fixed service values are treated as preassigned coverage. |
| `staff.csv` | Authoritative faculty roster and service-weight table. The first column must be `STAFF`; every other column is treated as a schedulable service. A weight of `0` prevents solver-created assignments to that service. |
| `preferences.csv` | Optional per-faculty preferences. Supports `DOUBLE-BRST`, `BLOCK-SIZE-PREFERENCE`, and weekday preference columns `M,T,W,R,F`. |
| `time-off.csv` | Additional unavailable dates with columns `Date,Name`. These blackouts apply even if the faculty member does not have a column in `template.csv`. |

## Template Rules

- `DotW` should use `M,T,W,R,F,St,Su`.
- Weekend rows, defined as `St` and `Su`, are not scheduled.
- A weekday is treated as a global closure if every faculty column in the template contains a fixed non-service blocking value.
- Recognized fixed non-service values are `x`, `VACATION`, `CYTOLOGY`, `NON-CLINICAL`, and `PROFESSIONAL`.
- Fixed service values must match service columns in `staff.csv`; they count as existing service assignments.
- Faculty listed in `staff.csv` but missing from `template.csv` are still schedulable and are treated as available on working days except for dates listed in `time-off.csv`.
- Faculty listed in `template.csv` but missing from `staff.csv` are ignored by the solver.

## Staff Weights And Targets

`staff.csv` defines both the solver roster and the services to schedule. Service columns are read dynamically, so adding or removing a service is done by editing the columns in `staff.csv`.

For each service, the solver identifies service-available working days. A service is considered available on a working day if either the template already contains a fixed assignment for that service or at least one eligible faculty member with nonzero weight is available.

Each faculty member receives an integer target for each service. Targets are computed proportionally from the service weights using a largest-remainder allocation, so the total targets for a service equal the number of required service days. Fixed assignments count toward those targets. If fixed assignments exceed a target, the model is made infeasible.

## Hard Constraints

The solver enforces the following requirements:

- Each service has exactly one assigned faculty member on each service-available working day.
- Each faculty member must hit their computed target count for each service.
- A faculty member may cover at most one physical service per day, except for the configured paired breast-service case.
- Faculty with `DOUBLE-BRST=1` may cover both `BRST1` and `BRST2` on the same day, but may not cover either breast service together with another non-breast service.
- Template fixed service assignments are preserved.
- Template blocking values and `time-off.csv` dates prevent solver-created assignments.
- Isolated one-day blocks are disallowed: if a faculty member covers a service on a working day, that same service must also appear on the previous or next working day for that faculty member. Fixed template assignments count for this adjacency rule.
- For breast services, long same-service blocks are capped for non-double-BRST faculty. The default cap is four consecutive working days, but a larger `BLOCK-SIZE-PREFERENCE` raises the cap up to that preference.
- Multiple fixed assignments for the same service on the same date are treated as infeasible.

## Soft Constraints

Soft constraints affect the objective value but do not make a schedule infeasible by themselves.

### Block Length

Each faculty member has a preferred block size from `BLOCK-SIZE-PREFERENCE`. Blank or invalid values default to `3`.

Penalty for a block of length `L` with preference `P`:

| Condition | Formula |
|---|---|
| `L < P` | `(P - L)^2 * PENALTY_UNIT` |
| `L = 1` | Short-block penalty plus `EXTRA_LEN1` |
| `L = P` | `0` |
| `L > P` | `(L - P) * PENALTY_OVER_DAY` |

### Weekday Preferences

`preferences.csv` may include weekday columns `M,T,W,R,F`.

- `1` rewards assignments on that weekday.
- `0` penalizes assignments on that weekday.
- Blank is neutral.

These are scoring preferences only. They do not make a faculty member available or unavailable.

### Weekly Service Mix

For faculty not configured for double breast coverage, the solver strongly discourages covering multiple distinct services within the same Monday-Friday week.

### Weekend-Spanning Blocks

A same-service block that continues from Friday to Monday is penalized.

### Double Breast Reward

When both `BRST1` and `BRST2` exist and a faculty member is configured with `DOUBLE-BRST=1`, the solver rewards assigning both breast services on the same day.

## Objective Constants

```python
PENALTY_UNIT = 3
EXTRA_LEN1 = 10
PENALTY_OVER_DAY = 8
REWARD_DOUBLE = 6
PENALTY_WEEKEND_SPAN = 10
PENALTY_WEEKLY_SERVICE_MIX = 100
DAY_PREF_REWARD = 3
DAY_PREF_PENALTY = 3
MAX_CONSECUTIVE_SERVICE_DAYS = 4
```

Lower objective values are better. Negative terms are rewards.

## Output Files

Output files are written to `output/` with timestamps.

| File | Contents |
|---|---|
| `schedule_YYYYMMDD_HHMMSS.csv` | Completed schedule. Existing template values are preserved; blank cells are filled with solver assignments. A paired breast assignment is written as `BRST1+BRST2`. |
| `report_YYYYMMDD_HHMMSS.txt` | Solver status, objective value, runtime, best bound, service targets, assigned counts, block-length summaries, weekend-span counts, and double breast usage. |

The output schedule uses faculty order from `staff.csv`. Faculty in `staff.csv` but missing from `template.csv` are included as output columns.

## Running The Solver

From this directory:

```sh
python schedule-faculty.py
```

Optional runtime limit:

```sh
MAX_SOLVE_SECONDS=300
python schedule-faculty.py
```

If `MAX_SOLVE_SECONDS` is not set, the default limit is 900 seconds. The solver uses 8 parallel search workers and logs CP-SAT search progress.

The script imports OR-Tools and attempts to install it with `pip` if it is missing.

## Notes

- The solver may return `FEASIBLE` instead of `OPTIMAL` if it reaches the runtime limit before proving optimality.
- Changing objective constants changes schedule tradeoffs but does not relax hard constraints.
- Model feasibility depends on the interaction between service targets, fixed assignments, time off, block rules, and service weights.
