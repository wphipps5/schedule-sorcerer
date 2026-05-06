# ---------------------------------------------------------
# AUTO‑INSTALL REQUIRED PACKAGES
# ---------------------------------------------------------
import sys
import subprocess
import importlib

def ensure_package(pkg, pip_name=None):
    try:
        importlib.import_module(pkg)
    except ImportError:
        print(f"Installing missing package: {pkg}")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pip_name or pkg])

ensure_package("ortools")

# ---------------------------------------------------------
# CONSTRAINT I/O
# ---------------------------------------------------------
FEASIBILITY_ONLY = False   # set True to skip all penalties and find first feasible solution
USE_VACATION = True
USE_BLOCKS = True
USE_SERVICE_MIN = True
BLOCK_FRAG_WEIGHT = 3      # penalty per extra block start beyond the first (fragmentation)
USE_RESIDENT_BLACKOUT = True
USE_NO_EXTRA_VA = True

VAC_WRAP_ROTATIONS = {"CHEM","HEMEP","HEMEC","IMM","MGP","MICRO","TM1","TM2","SCH","SCHLM","VIR","KCME","BWNW"}

OTHER_COVER_ALLOWED = {"GI","GU","BRST","BST","GYN","HNL"}
OTHER_COVER_RESIDENTS = {"Lane-F","Loberg-M","Giles-A"}  # specific residents allowed
OTHER_COVER_COHORTS = set()    # cohorts allowed (e.g. {"AP3","CP3"})

# ---------------------------------------------------------
# IMPORTS
# ---------------------------------------------------------
import csv
import os
import textwrap
from collections import defaultdict
from ortools.sat.python import cp_model

INPUT_DIR = "input"
OUTPUT_DIR = "output"

ROTATIONS_FILE = os.path.join(INPUT_DIR,"rotations.csv")
RESIDENTS_FILE = os.path.join(INPUT_DIR,"residents.csv")
VAC_FILE = os.path.join(INPUT_DIR,"vac_pref.csv")
BLOCK_FILE = os.path.join(INPUT_DIR,"rotation_blocks.csv")
BLACKOUT_FILE = os.path.join(INPUT_DIR,"blackout_by_week.csv")
RESIDENT_BLACKOUT_FILE = os.path.join(INPUT_DIR, "resident_blackouts.csv")

TEMPLATES = [
    os.path.join(INPUT_DIR,"template-a.csv")
]

WEEKS = 52

os.makedirs(OUTPUT_DIR,exist_ok=True)

# ---------------------------------------------------------
# LOAD ROTATIONS
# ---------------------------------------------------------
rotations = []
preferred = {}
capacity = {}
required_min = {}
ranges = defaultdict(dict)

with open(ROTATIONS_FILE,encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)

    for row in reader:
        rot = row["Rotation"].strip().upper()
        rotations.append(rot)

        preferred[rot] = None if row["Preferred_n"]=="NA" else int(row["Preferred_n"])
        capacity[rot]  = None if row["Capacity_max"]=="NA" else int(row["Capacity_max"])
        required_min[rot] = int(row["Required_min"])

        for cohort in ["AP1","AP2","AP3","CP1","CP2","CP3"]:
            ranges[rot][cohort] = (
                int(row[f"{cohort}_min"]),
                int(row[f"{cohort}_max"])
            )

# ---------------------------------------------------------
# LOAD BLOCK LIMITS
# ---------------------------------------------------------
max_blocks = {}
min_block_size = {}

with open(BLOCK_FILE,encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)
    for row in reader:
        rot = row["Rotation"].strip().upper()
        max_blocks[rot] = int(row["Max_Blocks"])
        min_block_size[rot] = int(row.get("Min_Block_Size",1))

# ---------------------------------------------------------
# LOAD BLACKOUTS
# ---------------------------------------------------------
blackout = defaultdict(set)

with open(BLACKOUT_FILE,encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)

    for row in reader:
        week = int(row["Week"]) - 1

        for rot,val in row.items():
            if rot != "Week" and val.lower() == "x":
                blackout[week].add(rot.upper())

# ---------------------------------------------------------
# LOAD RESIDENT-SPECIFIC BLACKOUTS
# ---------------------------------------------------------
resident_blackouts = []

with open(RESIDENT_BLACKOUT_FILE, encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)

    for row in reader:
        resident_blackouts.append({
            "resident": row["Resident"].strip(),
            "rotation": row["Rotation"].strip().upper(),
            "start": int(row["Start_Week"]) - 1,
            "end": int(row["End_Week"]) - 1
        })

# ---------------------------------------------------------
# LOAD RESIDENTS
# ---------------------------------------------------------
residents = []
cohort = {}
modifiers = defaultdict(dict)

with open(RESIDENTS_FILE,encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)

    for row in reader:

        r = row["Resident"].strip()
        residents.append(r)
        cohort[r] = row["Cohort"].strip()

        for rot in rotations:
            val = row.get(rot,"")
            modifiers[r][rot] = int(val) if val not in ("",None) else 0

# ---------------------------------------------------------
# LOAD VACATION PREFS
# ---------------------------------------------------------
vac_options = defaultdict(list)

with open(VAC_FILE,encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)

    for row in reader:
        r = row["Resident"].strip()

        for i in range(1,5):

            opts = []

            main = row.get(f"Vac{i}","").strip()
            alt  = row.get(f"Vac{i}_Alt","").strip()

            if main: opts.append(main)
            if alt:  opts.append(alt)

            if opts:
                vac_options[r].append(opts)

# ---------------------------------------------------------
# VALIDATION HELPERS
# ---------------------------------------------------------
def validate_totals():

    print("\n--- CHECKING TOTAL WEEK RANGES ---")

    for r in residents:

        total_min = 0
        total_max = 0

        for rot in rotations:

            mn = ranges[rot][cohort[r]][0] + modifiers[r][rot]
            mx = ranges[rot][cohort[r]][1] + modifiers[r][rot]

            total_min += mn
            total_max += mx

        if total_min > 52 or total_max < 52:
            print(f"ERROR: {r} impossible totals min={total_min} max={total_max}")

validate_totals()

# ---------------------------------------------------------
# SOLUTION CALLBACK
# ---------------------------------------------------------
class _SolutionCallback(cp_model.CpSolverSolutionCallback):
    def __init__(self):
        super().__init__()
        self._n = 0
        self._best_obj = float('inf')
        self._time_to_best = 0.0

    def on_solution_callback(self):
        self._n += 1
        obj = self.ObjectiveValue()
        if obj < self._best_obj:
            self._best_obj = obj
            self._time_to_best = self.WallTime()

# ---------------------------------------------------------
# SOLVER
# ---------------------------------------------------------
def solve_with_template(template_file, seed=None, time_limit=None, run_id=None,
                        feasibility_only=False, hint_file=None):

    print("\n================================================")
    print("Solving with:", template_file)
    print("================================================")

    template = defaultdict(dict)
    dates = []

    date_to_week = {}

    with open(template_file,encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            w = int(row["Week"]) - 1
            dates.append(row["Date"])
            date_to_week[row["Date"]] = w
            for r in residents:
                val = row.get(r,"").strip().upper()
                if val:
                    template[r][w] = val

    # -----------------------------------------------------
    # DEBUG: WRAP FEASIBILITY CHECK
    # -----------------------------------------------------
    print("\n--- WRAP DEBUG CHECK ---")

    for r in residents:

        vac_weeks = [w for w in template[r] if template[r][w] == "VACATION"]

        for rot in VAC_WRAP_ROTATIONS:

            weeks = [w for w in template[r] if template[r][w] == rot]

            if not weeks:
                continue

            # count template blocks
            blocks = 1
            for i in range(1, len(weeks)):
                if weeks[i] != weeks[i-1] + 1:
                    blocks += 1

            if blocks >= 2 and len(vac_weeks) == 0:
                print(
                    f"WRAP IMPOSSIBLE: {r} has {blocks} template blocks of {rot} "
                    f"but no vacation in template"
                )

            if blocks > max_blocks.get(rot, 99):
                print(
                    f"BLOCK LIMIT VIOLATION: {r} has {blocks} template blocks of {rot} "
                    f"but max_blocks={max_blocks.get(rot)}"
                )

    # -----------------------------------------------------
    # TEMPLATE VALIDATION
    # -----------------------------------------------------
    print("\n--- TEMPLATE ROTATION COUNTS ---")

    for r in residents:

        counts = defaultdict(int)

        for w,rot in template[r].items():
            counts[rot] += 1

        for rot in rotations:

            base_min = ranges[rot][cohort[r]][0] + modifiers[r][rot]
            base_max = ranges[rot][cohort[r]][1] + modifiers[r][rot]

            placed = counts.get(rot,0)

            if placed > base_max:
                print(f"ERROR: {r} has {placed} {rot} but max is {base_max}")

    print("\n--- TEMPLATE VS BLACKOUT ---")

    for r in residents:
        for w,rot in template[r].items():

            if rot in blackout[w]:
                print(f"ERROR blackout conflict: {r} week {w+1} rotation {rot}")

    print("\n--- TEMPLATE TOTAL CHECK ---")
    for r in residents:
        counts = defaultdict(int)
        for w, rot in template[r].items():
            counts[rot] += 1

        for rot in rotations:
            base_min, base_max = ranges[rot][cohort[r]]
            mod = modifiers[r][rot]
            mn = max(0, base_min + mod)
            mx = max(0, base_max + mod)

            if counts[rot] > mx:
                print("TEMPLATE VIOLATION:", r, rot, counts[rot], ">", mx)



    # -----------------------------------------------------
    # COMPUTE REMAINING RANGES
    # -----------------------------------------------------
    min_req = defaultdict(dict)
    max_req = defaultdict(dict)
    for r in residents:
        c = cohort[r]
        for rot in rotations:
            base_min,base_max = ranges[rot][c]
            mod = modifiers[r][rot]
            mn = max(0, base_min + mod)
            mx = max(0, base_max + mod)
            placed = sum(
                1 for w in template[r]
                if template[r][w] == rot
            )
            min_req[r][rot] = max(0, mn - placed)
            max_req[r][rot] = max(0, mx - placed)

    print("\n--- VA RANGE CHECK ---")
    for r in residents:
        if max_req[r].get("VA", 0) > 0:
            print("WARNING:", r, "still allowed to take additional VA weeks")

    print("\n--- VACATION TOTAL CHECK ---")
    for r in residents:
        template_vac = sum(1 for w in template[r] if template[r][w] == "VACATION")
        required_vac = template_vac + max_req[r].get("VACATION", 0)
        print(r, "needs", required_vac, "vacation weeks")

    # -----------------------------------------------------
    # MODEL
    # -----------------------------------------------------
    model = cp_model.CpModel()

    penalties = []

    x = {}
    for r in residents:
        for w in range(WEEKS):
            for rot in rotations:
                x[r,w,rot] = model.NewBoolVar(f"x_{r}_{w}_{rot}")

    # -----------------------------------------------------
    # COVER VARIABLES (allow OTHER to cover services)
    # -----------------------------------------------------
    cover = {}
    for r in residents:
        for w in range(WEEKS):
            for rot in rotations:
                cover[r,w,rot] = model.NewBoolVar(f"cover_{r}_{w}_{rot}")

    # -----------------------------------------------------
    # ONE ROTATION PER WEEK
    # -----------------------------------------------------
    for r in residents:
        for w in range(WEEKS):
            model.Add(sum(x[r,w,rot] for rot in rotations) == 1)

    # -----------------------------------------------------
    # COVERAGE LINK: controlled OTHER coverage
    # -----------------------------------------------------
    for r in residents:
        allow_other_cover = (
            r in OTHER_COVER_RESIDENTS
            or cohort[r] in OTHER_COVER_COHORTS
        )

        for w in range(WEEKS):
            for rot in rotations:
                if rot not in OTHER_COVER_ALLOWED:
                    model.Add(cover[r,w,rot] <= x[r,w,rot])
                elif allow_other_cover:
                    model.Add(cover[r,w,rot] <= x[r,w,rot] + x[r,w,"OTHER"])

                    # penalty if OTHER is used instead of real assignment
                    other_cover = model.NewBoolVar(f"other_cover_{r}_{w}_{rot}")
                    model.Add(other_cover <= cover[r,w,rot])
                    model.Add(other_cover <= 1 - x[r,w,rot])
                    model.Add(other_cover >= cover[r,w,rot] - x[r,w,rot])
                    penalties.append(other_cover * 2)
                else:
                    model.Add(cover[r,w,rot] <= x[r,w,rot])

    # prevent a resident covering multiple services in one week
    for r in residents:
        for w in range(WEEKS):
            model.Add(sum(cover[r,w,rot] for rot in rotations) <= 1)

    # -----------------------------------------------------
    # TEMPLATE FIXES
    # -----------------------------------------------------
    for r in residents:
        for w,rot in template[r].items():

            if (r,w,rot) in x:
                model.Add(x[r,w,rot] == 1)

    # -----------------------------------------------------
    # HINT
    # -----------------------------------------------------
    if hint_file and os.path.exists(hint_file):
        print(f"\nLoading warm-start hint from: {hint_file}")
        with open(hint_file, encoding="utf-8") as hf:
            n_hints = 0
            for row in csv.DictReader(hf):
                w = int(row["Week"]) - 1
                for r in residents:
                    rot = row.get(r, "").strip().upper()
                    if rot and (r, w, rot) in x:
                        model.AddHint(x[r, w, rot], 1)
                        n_hints += 1
        print(f"  Applied {n_hints} hints.")
    else:
        # greedy hint: template weeks + vacation slots + rotation minimums + OTHER padding
        for r in residents:
            for w, rot in template[r].items():
                if (r, w, rot) in x:
                    model.AddHint(x[r, w, rot], 1)

            remaining = {}
            for rot in rotations:
                need = min_req[r].get(rot, 0)
                if need > 0:
                    remaining[rot] = need

            vac_hint = {}
            for slot in vac_options.get(r, []):
                for date in slot:
                    w2 = date_to_week.get(date)
                    if w2 is not None and w2 not in template[r]:
                        vac_hint[w2] = "VACATION"
                        break

            rot_queue = []
            for rot, cnt in remaining.items():
                rot_queue.extend([rot] * cnt)

            fill_idx = 0
            for w in range(WEEKS):
                if w in template[r]:
                    continue
                if w in vac_hint:
                    model.AddHint(x[r, w, "VACATION"], 1)
                elif fill_idx < len(rot_queue):
                    rot_h = rot_queue[fill_idx]
                    if (r, w, rot_h) in x:
                        model.AddHint(x[r, w, rot_h], 1)
                    fill_idx += 1
                else:
                    if (r, w, "OTHER") in x:
                        model.AddHint(x[r, w, "OTHER"], 1)


    # -----------------------------------------------------
    # NO ADDITIONAL VA WEEKS
    # -----------------------------------------------------
    if USE_NO_EXTRA_VA:
        for r in residents:
            for w in range(WEEKS):
                if w not in template[r]:
                    model.Add(x[r,w,"VA"] == 0)

    # -----------------------------------------------------
    # BLACKOUT CONSTRAINTS
    # -----------------------------------------------------
    for w in range(WEEKS):
        for rot in blackout[w]:
            for r in residents:
                model.Add(x[r,w,rot] == 0)

    # -----------------------------------------------------
    # RESIDENT-SPECIFIC BLACKOUT CONSTRAINTS
    # -----------------------------------------------------
    if USE_RESIDENT_BLACKOUT:
        for rule in resident_blackouts:
            r = rule["resident"]
            rot = rule["rotation"]
            for w in range(rule["start"], rule["end"] + 1):
                if (r,w,rot) in x:
                    model.Add(x[r,w,rot] == 0)

    # -----------------------------------------------------
    # ROTATION TOTALS
    # -----------------------------------------------------
    for r in residents:

        c = cohort[r]

        for rot in rotations:

            base_min, base_max = ranges[rot][c]
            mod = modifiers[r][rot]

            mn = max(0, base_min + mod)
            mx = max(0, base_max + mod)

            # All weeks count, including template weeks
            vars_rot = [x[r,w,rot] for w in range(WEEKS)]

            # If the resident can never take this rotation
            if mx == 0:
                for w in range(WEEKS):
                    if template[r].get(w) != rot:
                        model.Add(x[r,w,rot] == 0)
                continue

            # Enforce final min/max including template placements
            model.Add(sum(vars_rot) >= mn)
            model.Add(sum(vars_rot) <= mx)

    # -----------------------------------------------------
    # VACATION PLACEMENT CONSTRAINTS
    # -----------------------------------------------------
    if USE_VACATION:
        for r in residents:
            options = vac_options.get(r, [])
            if not options:
                continue

            for i, slot in enumerate(options):

                week_vars = []
                slot_already_satisfied = False

                for date in slot:
                    w = date_to_week.get(date)
                    if w is None:
                        continue

                    trot = template[r].get(w)

                    # if template already assigns vacation, slot is satisfied
                    if trot == "VACATION":
                        slot_already_satisfied = True
                        break

                    # if template assigns another rotation, skip
                    if trot:
                        continue

                    week_vars.append(x[r,w,"VACATION"])

                if slot_already_satisfied:
                    continue

                if len(week_vars) == 1:
                    model.Add(week_vars[0] == 1)

                elif len(week_vars) > 1:
                    model.Add(sum(week_vars) == 1)

                else:
                    print("WARNING: no valid weeks for vacation slot:", r, slot)

    # -----------------------------------------------------
    # BLOCK LIMITS
    # -----------------------------------------------------
    if USE_BLOCKS:
        for r in residents:

            for rot in rotations:

                maxb = max_blocks.get(rot)
                if maxb is None:
                    continue

                mb = min_block_size.get(rot,1)

                # count blocks already placed in template so solver limit is correct
                template_block_starts = 0
                for w in sorted(template[r]):
                    if template[r][w] == rot:
                        prev = template[r].get(w - 1) if w > 0 else None
                        if prev != rot:
                            template_block_starts += 1

                effective_maxb = max(0, maxb - template_block_starts)

                block_starts = []

                for w in range(WEEKS):

                    if w in template[r]:
                        continue

                    start = model.NewBoolVar(f"start_{r}_{rot}_{w}")

                    if w == 0:
                        model.Add(start == x[r,w,rot])
                    else:
                        model.Add(start >= x[r,w,rot] - x[r,w-1,rot])
                        model.Add(start <= x[r,w,rot])
                        model.Add(start <= 1 - x[r,w-1,rot])

                    block_starts.append(start)

                    # minimum block size
                    if mb > 1 and w + mb <= WEEKS:
                        for k in range(mb):
                            model.Add(x[r,w+k,rot] == 1).OnlyEnforceIf(start)

                if block_starts:

                    total_blk = model.NewIntVar(0, len(block_starts), f"total_blk_{r}_{rot}")
                    model.Add(total_blk == sum(block_starts))
                    model.Add(total_blk <= effective_maxb)

                    # soft: penalize each block start beyond the first
                    if effective_maxb > 1 and not feasibility_only:
                        extra_blk = model.NewIntVar(0, effective_maxb - 1, f"extra_blk_{r}_{rot}")
                        model.Add(extra_blk >= total_blk - 1)
                        penalties.append(extra_blk * BLOCK_FRAG_WEIGHT)

                    # wrap constraint (hard): gap weeks between blocks must be VACATION
                    if rot in VAC_WRAP_ROTATIONS and maxb > 1:

                        # prefix: has_seen[w] = 1 if rotation appeared in weeks 0..w
                        has_seen = []
                        for w in range(WEEKS):
                            s = model.NewBoolVar(f"seen_{r}_{rot}_{w}")
                            if w == 0:
                                model.Add(s == x[r, 0, rot])
                            else:
                                model.AddMaxEquality(s, [has_seen[w-1], x[r, w, rot]])
                            has_seen.append(s)

                        # suffix: will_appear[w] = 1 if rotation appears in weeks w..WEEKS-1
                        will_appear = [None] * WEEKS
                        for w in range(WEEKS - 1, -1, -1):
                            s = model.NewBoolVar(f"will_{r}_{rot}_{w}")
                            if w == WEEKS - 1:
                                model.Add(s == x[r, WEEKS - 1, rot])
                            else:
                                model.AddMaxEquality(s, [will_appear[w+1], x[r, w, rot]])
                            will_appear[w] = s

                        # any gap week (rotation seen before, not here, seen after) must be VACATION
                        for w in range(1, WEEKS - 1):
                            trot = template[r].get(w)
                            if trot == rot:
                                continue  # part of a block, not a gap
                            if trot == "VACATION":
                                continue  # vacation gap is allowed
                            if trot is not None:
                                continue  # template-fixed week — exempt from gap constraint
                            model.AddBoolOr([
                                has_seen[w-1].Not(),    # no rotation before this week
                                x[r, w, rot],           # rotation is assigned this week
                                will_appear[w+1].Not(), # no rotation after this week
                                x[r, w, "VACATION"],    # gap week is vacation (allowed)
                            ])

    # -----------------------------------------------------
    # CAPACITY
    # -----------------------------------------------------
    for w in range(WEEKS):
        for rot in rotations:

            cap = capacity.get(rot)

            if cap is None:
                continue

            staff_vars = [x[r,w,rot] for r in residents]

            if staff_vars:
                model.Add(sum(staff_vars) <= cap)

    # -----------------------------------------------------
    # REQUIRED MINIMUM STAFFING (soft penalties)
    # GU is most critical, GI second; others default to 2
    # -----------------------------------------------------
    SERVICE_MIN_WEIGHTS = {"GU": 10, "GI": 5}

    if USE_SERVICE_MIN:
        for w in range(WEEKS):
            for rot in rotations:
                rmin = required_min.get(rot, 0)
                if rmin == 0:
                    continue
                staff_vars = [cover[r,w,rot] for r in residents]
                shortage = model.NewIntVar(0, rmin, f"svc_short_{w}_{rot}")
                model.Add(shortage >= rmin - sum(staff_vars))
                weight = SERVICE_MIN_WEIGHTS.get(rot, 2)
                penalties.append(shortage * weight)

    # -----------------------------------------------------
    # STAFFING OBJECTIVE
    # -----------------------------------------------------
    for w in range(WEEKS):
        for rot in rotations:

            pref = preferred.get(rot)

            if pref is None:
                continue

            staff_vars = [
                x[r,w,rot]
                for r in residents
                if (r,w,rot) in x
            ]

            if not staff_vars:
                continue

            staff = model.NewIntVar(0,len(residents),f"staff_{w}_{rot}")

            model.Add(staff == sum(staff_vars))

            diff = model.NewIntVar(0,len(residents),f"diff_{w}_{rot}")

            model.AddAbsEquality(diff, staff - pref)

            penalties.append(diff)

    if feasibility_only:
        model.Minimize(0)
    else:
        model.Minimize(sum(penalties))

    # capture model size before handing off to solver
    n_x_vars      = len(residents) * WEEKS * len(rotations)
    n_total_vars   = len(model.Proto().variables)
    n_constraints  = len(model.Proto().constraints)

    # -----------------------------------------------------
    # SOLVER
    # -----------------------------------------------------

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit if time_limit is not None else 3600
    solver.parameters.num_search_workers = 8
    solver.parameters.log_search_progress = True
    solver.parameters.cp_model_presolve = True
    solver.parameters.stop_after_first_solution = feasibility_only
    solver.parameters.random_seed = seed if seed is not None else 1

    result = solver.Solve(model)

    print("Solver status:", solver.StatusName(result))
    print("Objective value:", solver.ObjectiveValue())

    if result not in (cp_model.FEASIBLE, cp_model.OPTIMAL):
        print(f"\nNO USABLE SOLUTION ({solver.StatusName(result)}) — skipping output.\n")
        return

    # -----------------------------------------------------
    # WRITE OUTPUT
    # -----------------------------------------------------
    stem = os.path.basename(template_file).replace(".csv", "")
    if run_id:
        stem = f"{stem}_{run_id}"
    base_name  = f"{stem}_solution.csv"
    output_file = os.path.join(OUTPUT_DIR, base_name)

    # Fall back to a timestamped filename if the primary file is locked
    try:
        out_handle = open(output_file, "w", newline="")
    except PermissionError:
        from datetime import datetime
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        fallback = base_name.replace("_solution.csv", f"_solution_{stamp}.csv")
        output_file = os.path.join(OUTPUT_DIR, fallback)
        print(f"WARNING: primary output file locked, writing to {output_file}")
        out_handle = open(output_file, "w", newline="")

    with out_handle as out:

        writer = csv.writer(out)

        writer.writerow(["Week","Date"] + residents)

        for w in range(WEEKS):

            row = [w+1,dates[w]]

            for r in residents:
                val = ""
                for rot in rotations:
                    if solver.BooleanValue(x[r,w,rot]):
                        val = rot
                        break
                row.append(val)

            writer.writerow(row)

    print("\nSolution written:", output_file)

    # -----------------------------------------------------
    # METRICS
    # -----------------------------------------------------

    # --- service coverage (per-service and overall) ---
    svc_total = 0
    svc_met   = 0
    per_svc   = {}  # rot -> (met, total)

    for rot in rotations:
        rmin = required_min.get(rot, 0)
        if rmin == 0:
            continue
        rot_met = 0
        for w in range(WEEKS):
            staff = sum(solver.BooleanValue(cover[r, w, rot]) for r in residents)
            if staff >= rmin:
                rot_met += 1
        per_svc[rot] = (rot_met, WEEKS)
        svc_total += WEEKS
        svc_met   += rot_met

    overall_coverage_pct = 100.0 * svc_met / svc_total if svc_total > 0 else 100.0

    # --- block statistics ---
    total_blocks      = 0
    total_block_weeks = 0

    for r in residents:
        for rot in rotations:
            for w in range(WEEKS):
                if not solver.BooleanValue(x[r, w, rot]):
                    continue
                total_block_weeks += 1
                prev = solver.BooleanValue(x[r, w - 1, rot]) if w > 0 else 0
                if not prev:
                    total_blocks += 1

    avg_block_length = total_block_weeks / total_blocks if total_blocks > 0 else 0

    constrained_block_sizes = [v for v in min_block_size.values() if v > 1]
    min_block_len_reported  = min(constrained_block_sizes) if constrained_block_sizes else 1

    # --- vacation satisfaction ---
    vac_total = 0
    vac_met   = 0

    for r in residents:
        for slot in vac_options.get(r, []):
            vac_total += 1
            satisfied = False
            for date in slot:
                w = date_to_week.get(date)
                if w is None:
                    continue
                if template[r].get(w) == "VACATION":
                    satisfied = True
                    break
                if (r, w, "VACATION") in x and solver.BooleanValue(x[r, w, "VACATION"]):
                    satisfied = True
                    break
            if satisfied:
                vac_met += 1

    vac_pct = 100.0 * vac_met / vac_total if vac_total > 0 else 100.0

    # --- rotation minimums satisfied ---
    residents_mins_ok = 0
    for r in residents:
        c = cohort[r]
        ok = True
        for rot in rotations:
            base_min, _ = ranges[rot][c]
            mod = modifiers[r][rot]
            mn = max(0, base_min + mod)
            if mn == 0:
                continue
            total_assigned = sum(solver.BooleanValue(x[r, w, rot]) for w in range(WEEKS))
            if total_assigned < mn:
                ok = False
                break
        if ok:
            residents_mins_ok += 1

    # --- solver status interpretation ---
    status_name = solver.StatusName(result)
    obj_val     = solver.ObjectiveValue()
    bound       = solver.BestObjectiveBound()
    gap_pct     = 100.0 * (obj_val - bound) / abs(obj_val) if obj_val != 0 else 0.0

    STATUS_NOTES = {
        "OPTIMAL": (
            "The solver proved this schedule is mathematically optimal — no better "
            "solution exists within the defined constraints. The objective value is "
            "a global minimum."
        ),
        "FEASIBLE": (
            "The solver found a valid schedule satisfying all hard constraints, but "
            "ran out of time before proving optimality. The schedule is clinically "
            "usable; further runtime could lower the objective value. The optimality "
            "gap below indicates how much improvement remains theoretically possible."
        ),
        "INFEASIBLE": (
            "No valid schedule exists under the current constraints. This indicates "
            "a contradiction in the model inputs — for example, rotation requirements "
            "that cannot all be satisfied simultaneously. Review constraint inputs "
            "before re-running."
        ),
        "UNKNOWN": (
            "The solver ran out of time before finding any valid schedule. Consider "
            "relaxing constraints, increasing the runtime limit, or using "
            "FEASIBILITY_ONLY mode to find an initial solution."
        ),
    }
    status_note = STATUS_NOTES.get(status_name, "Unexpected solver status.")

    # --- build report ---
    lines = [
        "",
        "=" * 60,
        "  PERFORMANCE METRICS",
        "=" * 60,
        "",
        "SCHEDULING INSTANCE",
        f"  Residents          : {len(residents)}",
        f"  Training cohorts   : {len(set(cohort.values()))}",
        f"  Rotations          : {len(rotations)}",
        f"  Scheduling weeks   : {WEEKS}",
        f"  Total assignments  : {len(residents) * WEEKS}",
        "",
        "MODEL COMPLEXITY",
        f"  Assignment variables: {n_x_vars:,}",
        f"  Total variables     : {n_total_vars:,}",
        f"  Total constraints   : {n_constraints:,}",
        "",
        "SOLVER PERFORMANCE",
        f"  Status             : {status_name}",
        f"  Objective value    : {obj_val:.1f}  (weighted penalty total; lower = better)",
        f"  Best bound (lower) : {bound:.1f}",
        f"  Optimality gap     : {gap_pct:.1f}%  (0% = proven optimal)",
        f"  Wall-clock runtime : {solver.WallTime():.1f} s",
        f"  Runtime limit      : {int(solver.parameters.max_time_in_seconds)} s",
        f"  Parallel threads   : {solver.parameters.num_search_workers}",
        "",
        "  Status note:",
    ]
    for wrapped_line in textwrap.wrap(status_note, width=56):
        lines.append(f"    {wrapped_line}")

    lines += [
        "",
        "SCHEDULE QUALITY",
        f"  Overall service coverage      : {overall_coverage_pct:.1f}%",
    ]

    for rot, (met, total) in sorted(per_svc.items()):
        pct = 100.0 * met / total
        weight_label = " [HIGH]" if rot in ("GU", "GI") else ""
        lines.append(f"    {rot:<10}: {met}/{total} weeks ({pct:.1f}%){weight_label}")

    lines += [
        "",
        f"  Rotation blocks (total)       : {total_blocks}",
        f"  Average block length          : {avg_block_length:.1f} weeks",
        f"  Min enforced block length     : {min_block_len_reported} weeks",
        "",
        f"  Vacation requests             : {vac_total}",
        f"  Vacation requests satisfied   : {vac_met} ({vac_pct:.1f}%)",
        "",
        f"  Residents with all mins met   : {residents_mins_ok} / {len(residents)}",
        "",
        "=" * 60,
        "",
    ]

    report = "\n".join(lines)
    print(report)

    metrics_txt  = os.path.join(OUTPUT_DIR, f"{stem}_metrics.txt")
    metrics_csv  = os.path.join(OUTPUT_DIR, f"{stem}_metrics.csv")

    with open(metrics_txt, "w", encoding="utf-8") as mf:
        mf.write(report)
    print("Metrics written:", metrics_txt)

    # compact single-row CSV for grid compilation
    csv_exists = os.path.exists(metrics_csv)
    with open(metrics_csv, "w", newline="", encoding="utf-8") as cf:
        writer = csv.DictWriter(cf, fieldnames=[
            "run_id","seed","time_limit","status",
            "objective","best_bound","gap_pct","runtime_s",
            "overall_coverage_pct","n_blocks","avg_block_len",
            "vac_requests","vac_satisfied_pct","residents_mins_ok",
        ])
        writer.writeheader()
        writer.writerow({
            "run_id":               run_id or "default",
            "seed":                 seed if seed is not None else 1,
            "time_limit":           int(solver.parameters.max_time_in_seconds),
            "status":               status_name,
            "objective":            round(obj_val, 1),
            "best_bound":           round(bound, 1),
            "gap_pct":              round(gap_pct, 1),
            "runtime_s":            round(solver.WallTime(), 1),
            "overall_coverage_pct": round(overall_coverage_pct, 1),
            "n_blocks":             total_blocks,
            "avg_block_len":        round(avg_block_length, 2),
            "vac_requests":         vac_total,
            "vac_satisfied_pct":    round(vac_pct, 1),
            "residents_mins_ok":    residents_mins_ok,
        })
    print("Metrics CSV written:", metrics_csv)

# ---------------------------------------------------------
# RUN TEMPLATES
# ---------------------------------------------------------
import argparse as _ap
_parser = _ap.ArgumentParser(add_help=False)
_parser.add_argument("--seed",             type=int,  default=None)
_parser.add_argument("--time",             type=int,  default=None)
_parser.add_argument("--run-id",           type=str,  default=None, dest="run_id")
_parser.add_argument("--feasibility-only", action="store_true", default=False,
                     dest="feasibility_only")
_parser.add_argument("--hint-file",        type=str,  default=None, dest="hint_file")
_args, _ = _parser.parse_known_args()

for t in TEMPLATES:
    solve_with_template(t, seed=_args.seed, time_limit=_args.time, run_id=_args.run_id,
                        feasibility_only=_args.feasibility_only, hint_file=_args.hint_file)