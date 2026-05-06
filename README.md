# Schedule Sorcerer

Schedule Sorcerer is a pair of constraint-programming schedulers for pathology training and service coverage workflows. The project uses Google OR-Tools CP-SAT models to turn structured CSV inputs into complete schedules while enforcing hard requirements and optimizing schedule quality.

The repository contains two independent schedulers:

| Directory | Purpose |
|---|---|
| [`schedule-faculty/`](schedule-faculty/) | Builds daily faculty service coverage schedules from a calendar template, service weights, time-off requests, and scheduling preferences. |
| [`schedule-residents/`](schedule-residents/) | Builds 52-week resident rotation schedules from curriculum requirements, a partial template, vacation preferences, blackout rules, block limits, and service coverage targets. |

Each subdirectory includes its own solver script, input CSV files, and detailed README.

## Requirements

- Python 3.8 or newer
- Google OR-Tools

The solver scripts attempt to install `ortools` with `pip` if it is missing.

## Repository Layout

```text
schedule-sorcerer/
  schedule-faculty/
    schedule-faculty.py
    input/
    README.md
  schedule-residents/
    schedule-residents.py
    input/
    README.md
```

## Faculty Scheduler

The faculty scheduler assigns faculty to daily services. It reads:

- a calendar template with fixed assignments and unavailable days
- a staff/service weight table
- time-off requests
- optional block-size, double-coverage, and weekday preferences

Run from `schedule-faculty/`:

```sh
python schedule-faculty.py
```

Outputs are written to `schedule-faculty/output/` as timestamped schedule and report files.

See [`schedule-faculty/README.md`](schedule-faculty/README.md) for input schemas, constraints, objective terms, and run options.

## Resident Scheduler

The resident scheduler assigns residents to weekly rotations across a 52-week academic year. It reads:

- resident cohort and rotation requirement data
- rotation staffing, capacity, and service minimums
- a partially fixed weekly template
- vacation preferences
- global and resident-specific blackout rules
- block-count and minimum-block-size rules

Run from `schedule-residents/`:

```sh
python schedule-residents.py
```

Useful optional arguments:

```sh
python schedule-residents.py --time 300
python schedule-residents.py --feasibility-only
python schedule-residents.py --hint-file output/template-a_solution.csv
```

Outputs are written to `schedule-residents/output/` as a completed schedule plus metrics reports.

See [`schedule-residents/README.md`](schedule-residents/README.md) for input schemas, constraints, objective terms, and run options.

## Modeling Approach

Both schedulers separate hard feasibility requirements from soft quality goals.

Hard constraints include items such as coverage requirements, fixed template assignments, time off, capacity limits, rotation totals, and blackout rules. Soft objective terms guide the solver toward better schedules by penalizing less desirable patterns such as staffing imbalance, short service blocks, coverage shortages, or avoidable fragmentation.

This structure allows the models to distinguish impossible schedules from valid schedules that can still be improved with additional solve time or adjusted objective weights.

## Notes

- These tools are designed around CSV-based workflows so schedules can be reviewed and edited outside the solver.
- Solver status may be `FEASIBLE` rather than `OPTIMAL` if the runtime limit is reached before optimality is proven.
- Changing objective weights changes scheduling tradeoffs but does not relax hard constraints.
- Feasibility depends on the interaction between all input files; fixed template assignments can make a model infeasible if they conflict with requirements, blackouts, or capacity limits.
