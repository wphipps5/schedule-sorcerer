import sys
import subprocess
import importlib

def ensure_package(pkg, pip_name=None):
    try:
        importlib.import_module(pkg)
    except ImportError:
        print(f"Installing {pkg}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pip_name or pkg])

ensure_package("ortools")

import csv
import os
from collections import defaultdict
from datetime import datetime
from ortools.sat.python import cp_model

INPUT_DIR = "input"
OUTPUT_DIR = "output"

TEMPLATE_FILE = os.path.join(INPUT_DIR, "template.csv")
STAFF_FILE = os.path.join(INPUT_DIR, "staff.csv")
TIME_OFF_FILE = os.path.join(INPUT_DIR, "time-off.csv")
PREFERENCES_FILE = os.path.join(INPUT_DIR, "preferences.csv")

os.makedirs(OUTPUT_DIR, exist_ok=True)

_ts         = datetime.now().strftime("%Y%m%d_%H%M%S")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, f"schedule_{_ts}.csv")

WEEKEND    = {"St", "Su"}
FIXED_VALS = {"x", "vacation", "cytology", "non-clinical", "professional"}
BRST_PAIR  = ("BRST1", "BRST2")
NO_FIVE_DAY_BLOCK_SERVICES = set(BRST_PAIR)

DEFAULT_BLOCK_PREF = 3
PENALTY_UNIT       = 3
EXTRA_LEN1         = 10
PENALTY_OVER_DAY   = 8
REWARD_DOUBLE      = 6
PENALTY_WEEKEND_SPAN = 10
PENALTY_WEEKLY_SERVICE_MIX = 100
DAY_PREF_REWARD    = 3
DAY_PREF_PENALTY   = 3
MAX_CONSECUTIVE_SERVICE_DAYS = 4

# ----------------------------------------------------------
# LOAD TEMPLATE
# ----------------------------------------------------------
days               = []
template_faculty   = []

with open(TEMPLATE_FILE, encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)
    template_faculty = [h for h in reader.fieldnames if h not in ("Day", "Date", "DotW")]
    for row in reader:
        days.append(dict(row))

date_to_index = {row["Date"]: i for i, row in enumerate(days)}

# ----------------------------------------------------------
# LOAD STAFF — dynamic service columns
# ----------------------------------------------------------
staff_weights = {}   # staff_weights[f][svc] = float weight
services      = []   # ordered list of service names

with open(STAFF_FILE, encoding="utf-8-sig") as f:
    reader     = csv.DictReader(f)
    services   = [h for h in reader.fieldnames if h != "STAFF"]
    for row in reader:
        name = row["STAFF"].strip()
        staff_weights[name] = {}
        for svc in services:
            try:
                staff_weights[name][svc] = float(row[svc])
            except (ValueError, KeyError):
                staff_weights[name][svc] = 0.0

all_faculty = list(staff_weights.keys())

service_set = set(services)

# ----------------------------------------------------------
# LOAD PREFERENCES
# ----------------------------------------------------------
block_pref = {f: DEFAULT_BLOCK_PREF for f in all_faculty}
double_brst = {f: False for f in all_faculty}
weekday_pref = {f: {} for f in all_faculty}

if os.path.exists(PREFERENCES_FILE):
    with open(PREFERENCES_FILE, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get("STAFF", "").strip()
            if not name:
                continue
            try:
                block_pref[name] = int(row.get("BLOCK-SIZE-PREFERENCE", "") or DEFAULT_BLOCK_PREF)
            except ValueError:
                block_pref[name] = DEFAULT_BLOCK_PREF
            double_brst[name] = (row.get("DOUBLE-BRST", "").strip() == "1")
            for dotw in ("M", "T", "W", "R", "F"):
                val = row.get(dotw, "").strip()
                if val in {"0", "1"}:
                    weekday_pref[name][dotw] = int(val)
else:
    print(f"WARNING: {PREFERENCES_FILE} not found; using default preferences")

# ----------------------------------------------------------
# LOAD TIME OFF AND STAMP BLACKOUTS
# ----------------------------------------------------------
blackout_dates = defaultdict(set)

if os.path.exists(TIME_OFF_FILE):
    with open(TIME_OFF_FILE, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            date = row.get("Date", "").strip()
            name = row.get("Name", "").strip()
            if not date or not name:
                continue
            blackout_dates[name].add(date)
            if date not in date_to_index:
                print(f"WARNING: time-off date {date} for {name} is outside template horizon")
                continue
            if name in template_faculty:
                days[date_to_index[date]][name] = "x"
else:
    print(f"WARNING: {TIME_OFF_FILE} not found; no extra blackout dates applied")

# ----------------------------------------------------------
# VALIDATE FACULTY MATCH
# ----------------------------------------------------------
template_set = set(template_faculty)
staff_set    = set(all_faculty)

for name in template_set - staff_set:
    print(f"WARNING: {name} in template but not in staff.csv — will be ignored")
for name in staff_set - template_set:
    print(f"NOTE: {name} in staff.csv but not in template — treated as fully available")

# faculty is the authoritative list from staff.csv
faculty = all_faculty

# ----------------------------------------------------------
# FIXED SERVICE ASSIGNMENTS FROM TEMPLATE
# ----------------------------------------------------------
fixed_assign = defaultdict(lambda: defaultdict(set))  # fixed_assign[f][svc] = set(day indices)
fixed_count = defaultdict(lambda: defaultdict(int))   # fixed_count[svc][d] = n fixed faculty
fixed_day_services = defaultdict(set)                 # fixed_day_services[f, d] = set(services)

for d, row in enumerate(days):
    for f in template_faculty:
        val = row.get(f, "").strip().upper()
        if val not in service_set:
            continue
        if f not in staff_set:
            print(f"WARNING: fixed {val} for {f} on {row['Date']} ignored because {f} is not in staff.csv")
            continue
        if staff_weights[f].get(val, 0.0) <= 0:
            print(f"WARNING: fixed {val} for {f} on {row['Date']} despite zero staff weight")
        fixed_assign[f][val].add(d)
        fixed_count[val][d] += 1
        fixed_day_services[f, d].add(val)

# ----------------------------------------------------------
# IDENTIFY WORKING DAYS
# ----------------------------------------------------------
def is_fixed(val):
    return val.strip().lower() in FIXED_VALS

working_days = []
for i, row in enumerate(days):
    if row["DotW"] in WEEKEND:
        continue
    if all(is_fixed(row.get(f, "")) for f in template_faculty):
        continue  # global closure: every template faculty member is blocked
    working_days.append(i)

prev_wd = {}
next_wd = {}
for pos, d in enumerate(working_days):
    prev_wd[d] = working_days[pos - 1] if pos > 0 else None
    next_wd[d] = working_days[pos + 1] if pos < len(working_days) - 1 else None

def spans_weekend(d1, d2):
    return d2 - d1 > 1

def week_key(d):
    return datetime.strptime(days[d]["Date"], "%Y-%m-%d").isocalendar()[:2]

week_days = defaultdict(list)
for d in working_days:
    week_days[week_key(d)].append(d)

# ----------------------------------------------------------
# AVAILABILITY: blank template days only, plus time-off blackouts
# Faculty not in template are available every working day except time-off dates
# ----------------------------------------------------------
def is_available(f, d):
    if days[d]["Date"] in blackout_dates.get(f, set()):
        return False
    if f not in template_faculty:
        return True   # no template column -> fully available unless blacked out
    val = days[d].get(f, "").strip()
    return val == ""

# ----------------------------------------------------------
# COMPUTE PROPORTIONAL TARGETS PER SERVICE
# ----------------------------------------------------------
total_working = len(working_days)

targets = defaultdict(dict)  # targets[f][svc] = int

print(f"Working days in horizon : {total_working}")
print(f"Services                : {services}")
print()

for svc in services:
    total_weight = sum(staff_weights[f][svc] for f in faculty)
    service_days = [
        d for d in working_days
        if fixed_count[svc].get(d, 0) > 0
        or any(staff_weights[f][svc] > 0 and is_available(f, d) for f in faculty)
    ]
    required_days = len(service_days)
    print(f"SERVICE: {svc}  (total weight: {total_weight})")
    print(f"  Required service days: {required_days}")

    raw_targets = {}
    base_targets = {}
    if total_weight > 0 and required_days > 0:
        for f in faculty:
            w = staff_weights[f][svc]
            raw = (w / total_weight * required_days) if w > 0 else 0.0
            raw_targets[f] = raw
            base_targets[f] = int(raw)
        remainder = required_days - sum(base_targets.values())
        ranked = sorted(
            faculty,
            key=lambda f: (raw_targets.get(f, 0.0) - base_targets.get(f, 0), raw_targets.get(f, 0.0)),
            reverse=True,
        )
        for f in ranked[:remainder]:
            base_targets[f] += 1
    else:
        for f in faculty:
            raw_targets[f] = 0.0
            base_targets[f] = 0

    print(f"  {'Faculty':<15} {'Weight':>8}  {'Target':>7}  {'Available':>10}  {'Pref':>4}  {'Dbl':>3}")
    print("  " + "-" * 61)
    for f in faculty:
        w = staff_weights[f][svc]
        targets[f][svc] = base_targets[f]
        avail_n = sum(1 for d in working_days if is_available(f, d))
        fixed_n = len(fixed_assign[f][svc])
        print(
            f"  {f:<15} {w:>8.1f}  {targets[f][svc]:>7}  {avail_n:>10}  "
            f"{block_pref.get(f, DEFAULT_BLOCK_PREF):>4}  {int(double_brst.get(f, False)):>3}  "
            f"fixed={fixed_n}"
        )
    print()

# ----------------------------------------------------------
# MODEL
# ----------------------------------------------------------
model     = cp_model.CpModel()
penalties = []

# x[f, d, svc] = 1 → faculty f assigned to service svc on working day d
x = {}
for f in faculty:
    for svc in services:
        remaining_target = targets[f][svc] - len(fixed_assign[f][svc])
        if remaining_target <= 0:
            continue
        for d in working_days:
            if is_available(f, d):
                x[f, d, svc] = model.NewBoolVar(f"x_{f}_{d}_{svc}")

def get_x(f, d, svc):
    return x.get((f, d, svc))

# ----------------------------------------------------------
# HARD: each faculty covers at most one physical service per day.
# DOUBLE-BRST faculty may cover BRST1 and BRST2 together, but never GYN+BRST.
# ----------------------------------------------------------
for f in faculty:
    for d in working_days:
        day_svc_vars = [x[f, d, svc] for svc in services if (f, d, svc) in x]
        fixed_svcs = fixed_day_services.get((f, d), set())
        can_double = (
            double_brst.get(f, False)
            and BRST_PAIR[0] in services
            and BRST_PAIR[1] in services
        )
        if not can_double:
            if len(fixed_svcs) > 1:
                print(f"ERROR: {f} has multiple fixed services on {days[d]['Date']}: {sorted(fixed_svcs)}")
                model.Add(0 == 1)
            if len(day_svc_vars) > 1:
                model.Add(sum(day_svc_vars) <= max(0, 1 - len(fixed_svcs)))
            continue

        brst1 = get_x(f, d, BRST_PAIR[0])
        brst2 = get_x(f, d, BRST_PAIR[1])
        non_brst_vars = [
            x[f, d, svc]
            for svc in services
            if svc not in BRST_PAIR and (f, d, svc) in x
        ]
        for non_brst in non_brst_vars:
            if brst1 is not None:
                model.Add(non_brst + brst1 <= 1)
            if brst2 is not None:
                model.Add(non_brst + brst2 <= 1)
        if non_brst_vars:
            model.Add(sum(non_brst_vars) <= 1)
        other_vars = [
            x[f, d, svc]
            for svc in services
            if svc not in BRST_PAIR and (f, d, svc) in x
        ]
        if len(other_vars) > 1:
            model.Add(sum(other_vars) <= 1)

# ----------------------------------------------------------
# SOFT: day-of-week preferences from preferences.csv
# A value of 1 rewards assignment on that weekday, 0 penalizes it,
# and blank is neutral.
# ----------------------------------------------------------
for f in faculty:
    prefs = weekday_pref.get(f, {})
    if not prefs:
        continue
    for d in working_days:
        dotw = days[d]["DotW"]
        if dotw not in prefs:
            continue
        for svc in services:
            xfd = get_x(f, d, svc)
            if xfd is None:
                continue
            if prefs[dotw] == 1:
                penalties.append(xfd * -DAY_PREF_REWARD)
            elif prefs[dotw] == 0:
                penalties.append(xfd * DAY_PREF_PENALTY)

# ----------------------------------------------------------
# SOFT: strongly discourage non-DOUBLE-BRST faculty from
# covering multiple distinct services in the same Mon-Fri week.
# ----------------------------------------------------------
for f in faculty:
    if double_brst.get(f, False):
        continue
    for wk, ds in week_days.items():
        service_used_terms = []
        for svc in services:
            if any(d in fixed_assign[f][svc] for d in ds):
                service_used_terms.append(1)
                continue

            svc_vars = [x[f, d, svc] for d in ds if (f, d, svc) in x]
            if not svc_vars:
                continue

            used = model.NewBoolVar(f"used_{f}_{svc}_{wk[0]}_{wk[1]}")
            model.AddMaxEquality(used, svc_vars)
            service_used_terms.append(used)

        if len(service_used_terms) <= 1:
            continue

        n_services = model.NewIntVar(0, len(service_used_terms), f"nsvc_{f}_{wk[0]}_{wk[1]}")
        model.Add(n_services == sum(service_used_terms))
        extra_services = model.NewIntVar(0, len(service_used_terms) - 1, f"extra_svc_{f}_{wk[0]}_{wk[1]}")
        model.Add(extra_services >= n_services - 1)
        penalties.append(extra_services * PENALTY_WEEKLY_SERVICE_MIX)

# ----------------------------------------------------------
# HARD: exactly 1 person per service per service-available day
# ----------------------------------------------------------
for svc in services:
    for d in working_days:
        svc_vars = [x[f, d, svc] for f in faculty if (f, d, svc) in x]
        n_fixed = fixed_count[svc].get(d, 0)
        if n_fixed > 1:
            print(f"ERROR: {svc} has {n_fixed} fixed assignments on {days[d]['Date']}")
            model.Add(0 == 1)
        if svc_vars or n_fixed:
            model.Add(sum(svc_vars) == 1 - n_fixed)
        else:
            eligible = [
                f for f in faculty
                if staff_weights[f][svc] > 0 and is_available(f, d)
            ]
            if eligible:
                print(
                    f"WARNING: {svc} on {days[d]['Date']} is service-available, "
                    "but no assignment variables were created"
                )

# ----------------------------------------------------------
# HARD: each faculty hits their target for each service
# ----------------------------------------------------------
for f in faculty:
    for svc in services:
        fvars = [x[f, d, svc] for d in working_days if (f, d, svc) in x]
        fixed_n = len(fixed_assign[f][svc])
        remaining_target = targets[f][svc] - fixed_n
        if fixed_n > targets[f][svc]:
            print(
                f"ERROR: {f} has {fixed_n} fixed {svc} assignments, "
                f"exceeding target {targets[f][svc]}"
            )
            model.Add(0 == 1)
            continue
        if remaining_target == 0:
            continue
        if not fvars:
            print(f"WARNING: {f} needs {remaining_target} more {svc} days but has no available days!")
            model.Add(0 == remaining_target)
            continue
        model.Add(sum(fvars) == remaining_target)

# ----------------------------------------------------------
# BLOCK STARTS per (faculty, service)
# ----------------------------------------------------------
block_start = {}

for f in faculty:
    for svc in services:
        for d in working_days:
            xfd = get_x(f, d, svc)
            if xfd is None:
                continue

            prev     = prev_wd[d]
            prev_xfd = get_x(f, prev, svc) if prev is not None else None

            s = model.NewBoolVar(f"start_{f}_{svc}_{d}")
            block_start[f, svc, d] = s

            model.Add(s <= xfd)
            if prev_xfd is not None:
                model.Add(s <= 1 - prev_xfd)
                model.Add(s >= xfd - prev_xfd)
            else:
                model.Add(s == xfd)

# ----------------------------------------------------------
# SOFT: per-faculty block-length penalties from preferences.csv
# ----------------------------------------------------------
def block_length_penalty(length, preferred):
    if length < preferred:
        dist = preferred - length
        penalty = dist * dist * PENALTY_UNIT
        if length == 1:
            penalty += EXTRA_LEN1
        return penalty
    if length > preferred:
        return (length - preferred) * PENALTY_OVER_DAY
    return 0

for f in faculty:
    pref = block_pref.get(f, DEFAULT_BLOCK_PREF)
    for svc in services:
        for pos, d in enumerate(working_days):
            s = block_start.get((f, svc, d))
            if s is None:
                continue

            max_len = 1
            while pos + max_len < len(working_days):
                nd = working_days[pos + max_len]
                if get_x(f, nd, svc) is None:
                    break
                max_len += 1

            for length in range(1, max_len + 1):
                run_days = working_days[pos + 1:pos + length]
                run_vars = [get_x(f, rd, svc) for rd in run_days]
                after_day = working_days[pos + length] if pos + length < len(working_days) else None
                after_var = get_x(f, after_day, svc) if after_day is not None else None

                y = model.NewBoolVar(f"pref_len{length}_{f}_{svc}_{d}")
                model.Add(y <= s)
                for rv in run_vars:
                    model.Add(y <= rv)
                if after_var is not None:
                    model.Add(y <= 1 - after_var)
                    model.Add(y >= s + sum(run_vars) - after_var - length + 1)
                else:
                    model.Add(y >= s + sum(run_vars) - length + 1)

                penalty = block_length_penalty(length, pref)
                if penalty:
                    penalties.append(y * penalty)

# ----------------------------------------------------------
# HARD: no isolated 1-day blocks.
# If a faculty member covers a service on a working day, the same
# service must also appear on the previous or next working day.
# Fixed template service assignments count toward adjacency.
# ----------------------------------------------------------
for f in faculty:
    for svc in services:
        for d in working_days:
            xfd = get_x(f, d, svc)
            fixed_here = d in fixed_assign[f][svc]
            if xfd is None and not fixed_here:
                continue

            prev = prev_wd[d]
            nxt = next_wd[d]
            neighbor_terms = []

            fixed_neighbor = (
                (prev is not None and prev in fixed_assign[f][svc])
                or (nxt is not None and nxt in fixed_assign[f][svc])
            )
            if fixed_neighbor:
                continue

            if prev is not None:
                prev_x = get_x(f, prev, svc)
                if prev_x is not None:
                    neighbor_terms.append(prev_x)
            if nxt is not None:
                next_x = get_x(f, nxt, svc)
                if next_x is not None:
                    neighbor_terms.append(next_x)

            if fixed_here:
                if neighbor_terms:
                    model.Add(sum(neighbor_terms) >= 1)
                else:
                    print(f"ERROR: fixed {svc} for {f} on {days[d]['Date']} would create a 1-day block")
                    model.Add(0 == 1)
            elif neighbor_terms:
                model.Add(sum(neighbor_terms) >= 1).OnlyEnforceIf(xfd)
            else:
                model.Add(xfd == 0)

# ----------------------------------------------------------
# HARD: cap long same-service blocks.
# Faculty with a preferred block size above the default cap may reach
# that preference, but the model still prevents longer runaway blocks.
# Fixed template service assignments count toward the limit.
# ----------------------------------------------------------
for f in faculty:
    if double_brst.get(f, False):
        continue
    max_consecutive = max(
        MAX_CONSECUTIVE_SERVICE_DAYS,
        block_pref.get(f, DEFAULT_BLOCK_PREF),
    )
    block_window = max_consecutive + 1
    for svc in services:
        if svc not in NO_FIVE_DAY_BLOCK_SERVICES:
            continue
        for pos in range(0, len(working_days) - block_window + 1):
            ds = working_days[pos:pos + block_window]
            fixed_n = sum(1 for d in ds if d in fixed_assign[f][svc])
            svc_vars = [x[f, d, svc] for d in ds if (f, d, svc) in x]
            if fixed_n > max_consecutive:
                print(
                    f"ERROR: fixed template creates >{max_consecutive} "
                    f"consecutive {svc} days for {f}, starting {days[ds[0]]['Date']}"
                )
                model.Add(0 == 1)
            elif svc_vars:
                model.Add(sum(svc_vars) <= max_consecutive - fixed_n)

# ----------------------------------------------------------
# SOFT: penalize weekend-spanning consecutive assignments
# ----------------------------------------------------------
for f in faculty:
    for svc in services:
        for pos, d in enumerate(working_days[:-1]):
            nxt = working_days[pos + 1]
            if not spans_weekend(d, nxt):
                continue
            xfd   = get_x(f, d,   svc)
            xfnxt = get_x(f, nxt, svc)
            if xfd is None or xfnxt is None:
                continue
            wspan = model.NewBoolVar(f"wspan_{f}_{svc}_{d}")
            model.Add(wspan <= xfd)
            model.Add(wspan <= xfnxt)
            model.Add(wspan >= xfd + xfnxt - 1)
            penalties.append(wspan * PENALTY_WEEKEND_SPAN)

# ----------------------------------------------------------
# SOFT: reward DOUBLE-BRST days
# ----------------------------------------------------------
double_day = {}
if BRST_PAIR[0] in services and BRST_PAIR[1] in services:
    for f in faculty:
        if not double_brst.get(f, False):
            continue
        for d in working_days:
            brst1 = get_x(f, d, BRST_PAIR[0])
            brst2 = get_x(f, d, BRST_PAIR[1])
            if brst1 is None or brst2 is None:
                continue
            y = model.NewBoolVar(f"double_brst_{f}_{d}")
            double_day[f, d] = y
            model.Add(y <= brst1)
            model.Add(y <= brst2)
            model.Add(y >= brst1 + brst2 - 1)
            penalties.append(y * -REWARD_DOUBLE)

# ----------------------------------------------------------
# OBJECTIVE
# ----------------------------------------------------------
model.Minimize(sum(penalties) if penalties else 0)

# ----------------------------------------------------------
# SOLVE
# ----------------------------------------------------------
n_x_vars      = len(x)
n_total_vars  = len(model.Proto().variables)
n_constraints = len(model.Proto().constraints)

print(f"Model: {n_x_vars:,} assignment vars  |  {n_total_vars:,} total vars  |  {n_constraints:,} constraints")
print("Solving...")

solver = cp_model.CpSolver()
solver.parameters.max_time_in_seconds = float(os.environ.get("MAX_SOLVE_SECONDS", "900"))
solver.parameters.num_search_workers  = 8
solver.parameters.log_search_progress = True

result = solver.Solve(model)

print(f"\nStatus    : {solver.StatusName(result)}")
print(f"Objective : {solver.ObjectiveValue():.0f}")
print(f"Runtime   : {solver.WallTime():.1f}s")

if result not in (cp_model.FEASIBLE, cp_model.OPTIMAL):
    print("\nNo solution found — exiting.")
    sys.exit(1)

# ----------------------------------------------------------
# REPORT
# ----------------------------------------------------------
assign = defaultdict(lambda: defaultdict(set))  # assign[f][svc] = set of day indices
for f in faculty:
    for svc in services:
        assign[f][svc].update(fixed_assign[f][svc])
        for d in working_days:
            if (f, d, svc) in x and solver.BooleanValue(x[f, d, svc]):
                assign[f][svc].add(d)

REPORT_FILE = os.path.join(OUTPUT_DIR, f"report_{_ts}.txt")

def _build_report_lines():
    lines = []
    lines.append(f"Generated : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Status    : {solver.StatusName(result)}")
    lines.append(f"Objective : {solver.ObjectiveValue():.0f}")
    lines.append(f"Runtime   : {solver.WallTime():.1f}s")
    lines.append(f"Best bound: {solver.BestObjectiveBound():.0f}  "
                 f"(gap {(solver.ObjectiveValue()-solver.BestObjectiveBound())/max(solver.ObjectiveValue(),1)*100:.1f}%)")
    for svc in services:
        lines.append(f"\n{svc} assignments vs targets:")
        hdr = (f"  {'Faculty':<15} {'Target':>7}  {'Assigned':>9}  {'Pref':>4}  {'Blocks':>7}  "
               f"{'Len1':>5}  {'Len2':>5}  {'Len3':>5}  {'Len4':>5}  {'Len5':>5}  "
               f"{'Len6+':>6}  {'WkndSpan':>9}")
        lines.append(hdr)
        lines.append("  " + "-" * 101)
        for f in faculty:
            if targets[f][svc] == 0:
                continue
            n_assigned = len(assign[f][svc])
            blocks  = []
            current = []
            for d in working_days:
                if d in assign[f][svc]:
                    current.append(d)
                else:
                    if current:
                        blocks.append(current)
                        current = []
            if current:
                blocks.append(current)
            n_blocks = len(blocks)
            n_len1   = sum(1 for b in blocks if len(b) == 1)
            n_len2   = sum(1 for b in blocks if len(b) == 2)
            n_len3   = sum(1 for b in blocks if len(b) == 3)
            n_len4   = sum(1 for b in blocks if len(b) == 4)
            n_len5   = sum(1 for b in blocks if len(b) == 5)
            n_len6p  = sum(1 for b in blocks if len(b) >= 6)
            n_wspan  = sum(
                1 for b in blocks
                for i in range(len(b) - 1)
                if spans_weekend(b[i], b[i + 1])
            )
            lines.append(f"  {f:<15} {targets[f][svc]:>7}  {n_assigned:>9}  "
                         f"{block_pref.get(f, DEFAULT_BLOCK_PREF):>4}  {n_blocks:>7}  "
                         f"{n_len1:>5}  {n_len2:>5}  {n_len3:>5}  {n_len4:>5}  "
                         f"{n_len5:>5}  {n_len6p:>6}  {n_wspan:>9}")
    if double_day:
        lines.append("\nDOUBLE-BRST summary:")
        for f in faculty:
            if not double_brst.get(f, False):
                continue
            n_double = sum(
                1 for d in working_days
                if (f, d) in double_day and solver.BooleanValue(double_day[f, d])
            )
            lines.append(f"  {f:<15} {n_double:>5} double days")
    return lines

report_lines = _build_report_lines()
for line in report_lines:
    print(line)

with open(REPORT_FILE, "w", encoding="utf-8") as rf:
    rf.write("\n".join(report_lines) + "\n")
print(f"Report written: {REPORT_FILE}")

# ----------------------------------------------------------
# WRITE OUTPUT CSV
# all_faculty order from staff.csv; faculty not in template get blank columns
# ----------------------------------------------------------
with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as out:
    writer = csv.writer(out)
    writer.writerow(["Day", "Date", "DotW"] + faculty)

    for i, row in enumerate(days):
        out_row = [row["Day"], row["Date"], row["DotW"]]
        for f in faculty:
            existing = row.get(f, "").strip()
            if existing:
                out_row.append(existing)
            else:
                assigned_svcs = [svc for svc in services if i in assign[f][svc]]
                if BRST_PAIR[0] in assigned_svcs and BRST_PAIR[1] in assigned_svcs:
                    out_row.append(f"{BRST_PAIR[0]}+{BRST_PAIR[1]}")
                else:
                    out_row.append(assigned_svcs[0] if assigned_svcs else "")
        writer.writerow(out_row)

print(f"\nOutput written: {OUTPUT_FILE}")
