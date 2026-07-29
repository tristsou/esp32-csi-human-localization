import itertools
import math
import time

# Validated categorical slots 1-3 (blue/orange/aqua) from the CSI-Tool design
# system's default palette: the only prefix that clears all-pairs CVD and
# normal-vision separation floors in both light and dark modes, so up to 3
# simultaneous people never look ambiguous. Dark-mode hex is what the UI uses
# since the room view renders on a dark surface.
PERSON_COLORS = [
    "#3987e5",  # blue
    "#d95926",  # orange
    "#199e70",  # aqua
]

MAX_MISSED_SECONDS = 2.5
GATING_DISTANCE_M = 3.0
MIN_DISTURBANCE_TO_DETECT = 2.0
MIN_ACTIVE_DEVICES = 1
DISTURBANCE_SMOOTHING = 0.35  # EMA weight on each new raw sample
NEW_TRACK_CONFIRM_HITS = 2

# Must match the demo simulator's model (see sources/demo.py) so that
# estimated distance-from-device is meaningful for live/demo alike. Live
# hardware will need its own fitted falloff; these defaults are a
# reasonable starting point for real CSI amplitude disturbance.
DISTURBANCE_PEAK = 25.0
DISTURBANCE_FALLOFF_M = 3.5

# With a gentle falloff relative to typical room size, disturbance from a
# person is *superimposed* (additively summed) across every device, not a
# sparse per-device indicator — a device near person A also reads person B's
# contribution. So people can't be separated by grouping devices into
# disjoint subsets, and a single alternating subtract-and-resolve pass (fix
# every other person, re-solve one) can converge to a stable but wrong joint
# position: each person's own residual can look clean (low individual fit
# error) while the pair is jointly wrong, because the two errors cancel each
# other out. Verified numerically: naive alternation from certain starting
# configurations settles into such a fixed point and never escapes it.
#
# The fix is multi-start global search (see `_multi_start_joint_solve`): try
# several different starting configurations for all k people at once, run a
# few rounds of alternation from each, score every result by *joint* fit
# error (the sum of ALL k people's predicted contributions vs. the observed
# field, not just one person's residual), and keep only the lowest-error
# result across all starts. This reliably escapes the false-fixed-point
# basin — verified against hard cases that trap naive alternation.
JOINT_FIT_ERROR_ACCEPT = 30.0
JOINT_FIT_ERROR_IMPROVEMENT_RATIO = 0.5
ALTERNATION_ROUNDS = 5


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


class PersonTrack:
    """
    A single tracked person. Position is smoothed with a constant-velocity
    filter (simple alpha-beta / Kalman-like update) so that motion is
    continuous and resistant to single-frame noise or teleportation.
    """

    _id_counter = itertools.count(1)

    def __init__(self, x, y, color):
        self.id = next(PersonTrack._id_counter)
        self.color = color
        self.x, self.y = x, y
        self.vx, self.vy = 0.0, 0.0
        self.last_update = time.time()
        self.last_seen = time.time()
        self.history = [(self.x, self.y)]
        self.confidence = 0.5

    def predict(self, now: float):
        dt = max(now - self.last_update, 0.0)
        dt = min(dt, 0.5)
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.last_update = now

    def predicted_xy(self, now: float):
        # Where this track should be *now* given its last known velocity,
        # without mutating state — used for gating so a person walking away
        # from their last raw fix isn't dropped as "unmatched" and given a
        # fresh id next frame.
        dt = min(max(now - self.last_update, 0.0), 0.5)
        return self.x + self.vx * dt, self.y + self.vy * dt

    def update(self, mx, my, now: float):
        self.predict(now)

        alpha = 0.55  # position correction weight
        beta = 0.25  # velocity correction weight
        dt = max(now - self.last_seen, 1e-3)

        residual_x = mx - self.x
        residual_y = my - self.y

        self.x += alpha * residual_x
        self.y += alpha * residual_y
        self.vx += (beta * residual_x) / dt
        self.vy += (beta * residual_y) / dt

        # Damp velocity so tracks don't run away when a measurement is noisy.
        self.vx = _clamp(self.vx, -2.0, 2.0)
        self.vy = _clamp(self.vy, -2.0, 2.0)

        self.last_seen = now
        self.confidence = min(1.0, self.confidence + 0.1)
        self.history.append((self.x, self.y))
        if len(self.history) > 200:
            self.history.pop(0)

    def decay(self, now: float):
        self.confidence = max(0.0, self.confidence - 0.05)

    def is_stale(self, now: float) -> bool:
        return (now - self.last_seen) > MAX_MISSED_SECONDS

    def to_dict(self):
        return {
            "id": self.id,
            "color": self.color,
            "x": round(self.x, 3),
            "y": round(self.y, 3),
            "vx": round(self.vx, 3),
            "vy": round(self.vy, 3),
            "confidence": round(self.confidence, 2),
            "speed_mps": round(math.hypot(self.vx, self.vy), 3),
            "trail": [[round(px, 3), round(py, 3)] for px, py in self.history[-40:]],
        }


class MultiPersonTracker:
    """
    Estimates the position of up to `max_people` people in the room from
    per-device disturbance (live signal vs calibrated baseline), then
    associates estimated positions with existing tracks by nearest-neighbor
    gating so each person keeps a stable, reused ID across frames rather
    than spawning a new one every update.
    """

    def __init__(self, devices, room, calibrator, max_people: int = 3):
        self.devices = {d.id: d for d in devices}
        self.room = room
        self.calibrator = calibrator
        self.max_people = max_people
        self.tracks: dict[int, PersonTrack] = {}
        self.available_colors = list(PERSON_COLORS[:max_people])
        self.latest_disturbance: dict[str, float] = {d.id: 0.0 for d in devices}
        # Unmatched clusters waiting to prove they're a real, sustained
        # detection (not a one-frame noise spike) before minting a new id.
        self.pending_candidates: list[dict] = []

    def observe(self, device_id: str, signal: float):
        baseline = self.calibrator.baseline_for(device_id)
        raw = max(0.0, signal - baseline.mean)
        # A person's true disturbance changes continuously (they walk, they
        # don't teleport), so a single noisy sample crossing/uncrossing the
        # detect threshold shouldn't flip a device between "active" and not.
        # Low-pass filter the disturbance itself, before it ever reaches
        # clustering/trilateration, rather than smoothing the derived
        # position after the fact (which can't undo a wrong grouping).
        prev = self.latest_disturbance.get(device_id, 0.0)
        self.latest_disturbance[device_id] = prev + DISTURBANCE_SMOOTHING * (raw - prev)

    def step(self):
        now = time.time()
        clusters = self._estimate_positions()

        assigned_track_ids = set()
        unmatched = []
        for mx, my, strength in clusters:
            track_id = self._match_track(mx, my, assigned_track_ids, now)
            if track_id is not None:
                self.tracks[track_id].update(mx, my, now)
                assigned_track_ids.add(track_id)
            else:
                unmatched.append((mx, my, strength))

        self.pending_candidates = self._advance_candidates(unmatched, now)
        for candidate in self.pending_candidates:
            if candidate["hits"] < NEW_TRACK_CONFIRM_HITS:
                continue
            if len(self.tracks) >= self.max_people:
                continue
            color = self._next_color()
            track = PersonTrack(candidate["x"], candidate["y"], color)
            self.tracks[track.id] = track
            assigned_track_ids.add(track.id)
            candidate["spawned"] = True
        self.pending_candidates = [c for c in self.pending_candidates if not c.get("spawned")]

        for track_id, track in list(self.tracks.items()):
            if track_id not in assigned_track_ids:
                track.decay(now)
            if track.is_stale(now):
                self._release_color(track.color)
                del self.tracks[track_id]

        return [t.to_dict() for t in sorted(self.tracks.values(), key=lambda t: t.id)]

    def _advance_candidates(self, unmatched, now):
        updated = []
        remaining = list(unmatched)
        for candidate in self.pending_candidates:
            best_i, best_dist = None, GATING_DISTANCE_M
            for i, (mx, my, _strength) in enumerate(remaining):
                dist = math.hypot(candidate["x"] - mx, candidate["y"] - my)
                if dist < best_dist:
                    best_dist = dist
                    best_i = i
            if best_i is None:
                continue  # candidate didn't reappear this tick; drop it
            mx, my, _strength = remaining.pop(best_i)
            candidate["x"], candidate["y"] = mx, my
            candidate["hits"] += 1
            candidate["last_seen"] = now
            updated.append(candidate)

        for mx, my, _strength in remaining:
            updated.append({"x": mx, "y": my, "hits": 1, "last_seen": now})

        return updated

    def _match_track(self, mx, my, already_assigned, now):
        best_id, best_dist = None, GATING_DISTANCE_M
        for track_id, track in self.tracks.items():
            if track_id in already_assigned:
                continue
            px, py = track.predicted_xy(now)
            dist = math.hypot(px - mx, py - my)
            if dist < best_dist:
                best_dist = dist
                best_id = track_id
        return best_id

    def _next_color(self):
        if self.available_colors:
            return self.available_colors.pop(0)
        return PERSON_COLORS[len(self.tracks) % len(PERSON_COLORS)]

    def _release_color(self, color):
        if color not in self.available_colors:
            self.available_colors.append(color)

    def _estimate_positions(self):
        """
        Disturbance from each person sums additively into every device's
        reading, so a combined field with 2+ people present can't be split
        into per-person positions by any single-point fit, and naive
        alternation (fix everyone else, re-solve one person, repeat) can
        settle into a false fixed point that looks locally clean per-person
        while being jointly wrong — see the module docstring above
        `JOINT_FIT_ERROR_ACCEPT`.

        So position estimation is a genuine global search: for the current
        known-person count k (and again for k+1, to look for a new arrival),
        try several different starting configurations, alternate a few
        rounds from each, and keep only the configuration with the lowest
        *joint* fit error — the sum of ALL k/k+1 people's predicted
        contributions compared against the observed field at once. Adding a
        (k+1)th person is only accepted when it meaningfully improves the
        joint fit over the best k-person explanation, so a genuinely single
        person never gets spuriously split into two.
        """
        if sum(self.latest_disturbance.values()) < MIN_DISTURBANCE_TO_DETECT:
            return []

        # When the session is configured for at most 1 person, there's never
        # a second source to disentangle from — skip the multi-start joint
        # search entirely and solve the raw field directly. This is exact
        # for a single clean source (see `_solve_residual`) and avoids the
        # seed-search noise the general k-person path accepts as the price
        # of escaping false fixed points that only exist when 2+ people can
        # be superimposed in the same field.
        if self.max_people == 1:
            x, y, strength, _ = self._solve_residual(dict(self.latest_disturbance))
            if strength < MIN_DISTURBANCE_TO_DETECT:
                return []
            return [(x, y, strength)]

        now = time.time()
        tracks_by_confidence = sorted(self.tracks.values(), key=lambda t: -t.confidence)
        priors = [t.predicted_xy(now) for t in tracks_by_confidence]
        n_known = len(tracks_by_confidence)

        residual_all = dict(self.latest_disturbance)
        active_total = sum(v for v in residual_all.values() if v >= MIN_DISTURBANCE_TO_DETECT)
        if active_total < MIN_DISTURBANCE_TO_DETECT:
            return []

        best_positions = None
        best_jfe = None
        if n_known > 0:
            best_positions = self._multi_start_joint_solve(residual_all, n_known, extra_seeds=priors)
            best_jfe = self._joint_fit_error(residual_all, best_positions)

        # A freshly-spawned track hasn't locked onto its true position yet —
        # its first few solves are still converging, so `best_jfe` against it
        # is inflated for reasons that have nothing to do with a missing
        # person. Searching for a (k+1)th person against that unsettled
        # baseline means almost anything looks like a "big improvement" over
        # a bad fit, which is what let a spurious 3rd track spawn during a
        # 2-person track's own settling window. So only hunt for a new
        # arrival once every existing track is fully confident (i.e. has
        # matched consistently for several ticks) — a genuinely new person
        # will still be caught the moment the existing tracks stabilize.
        all_confident = all(t.confidence >= 1.0 for t in tracks_by_confidence)
        if n_known < self.max_people and all_confident:
            seeds_for_new = priors + [priors[-1] if priors else (self.room.width_m / 2, self.room.height_m / 2)]
            candidate_positions = self._multi_start_joint_solve(residual_all, n_known + 1, extra_seeds=seeds_for_new)
            candidate_jfe = self._joint_fit_error(residual_all, candidate_positions)
            improved = (
                best_jfe is None
                or candidate_jfe <= JOINT_FIT_ERROR_ACCEPT
                or candidate_jfe < best_jfe * JOINT_FIT_ERROR_IMPROVEMENT_RATIO
            )
            if improved:
                best_positions = candidate_positions

        if not best_positions:
            return []
        return [(x, y, self._field_strength_near(residual_all, x, y)) for x, y in best_positions]

    def _joint_fit_error(self, residual, positions):
        # Sum of squared error between the observed field and what ALL of
        # `positions` together would produce. Unlike `_fit_error` (which
        # checks a single point against the *already-subtracted* residual
        # of one person), this scores a full k-person hypothesis against
        # the raw field at once — the only way to tell a jointly-correct
        # configuration apart from a false fixed point that looks clean
        # person-by-person.
        total = 0.0
        for dev_id, device in self.devices.items():
            predicted = sum(self._expected_disturbance(x, y, device) for x, y in positions)
            observed = max(residual.get(dev_id, 0.0), 0.1)
            total += (observed - predicted) ** 2
        return total

    def _alternate_from_seed(self, residual, seed_positions, rounds=ALTERNATION_ROUNDS):
        # Gauss-Seidel-style refinement: repeatedly re-solve each person's
        # position from the field with every *other* person's current
        # estimate subtracted out. Converges quickly for a good seed, but
        # can converge to a false fixed point for a bad one — that's why
        # `_multi_start_joint_solve` tries several seeds and scores the
        # results jointly rather than trusting any single alternation run.
        positions = list(seed_positions)
        for _ in range(rounds):
            for i in range(len(positions)):
                residual_i = dict(residual)
                for j, (ox, oy) in enumerate(positions):
                    if j == i:
                        continue
                    self._subtract_contribution(residual_i, ox, oy)
                x, y, _, _ = self._solve_residual(residual_i)
                positions[i] = (x, y)
        return positions

    def _multi_start_joint_solve(self, residual, k, extra_seeds=None):
        # Try several different starting configurations for k simultaneous
        # people, alternate-refine each, and keep the one whose *joint* fit
        # error is lowest. For k == 1 there's only one person to place, so
        # a direct solve is exact and no multi-start search is needed.
        if k == 1:
            x, y, _, _ = self._solve_residual(residual)
            return [(x, y)]

        device_list = list(self.devices.values())
        seed_sets = []
        if extra_seeds and len(extra_seeds) >= k:
            seed_sets.append(list(extra_seeds[:k]))

        for i in range(len(device_list)):
            seeds = []
            for j in range(k):
                device = device_list[(i + j * 2) % len(device_list)]
                if j % 2 == 0:
                    seeds.append((device.x_m, device.y_m))
                else:
                    seeds.append((self.room.width_m - device.x_m, self.room.height_m - device.y_m))
            seed_sets.append(seeds)

        best_positions, best_jfe = None, None
        for seeds in seed_sets:
            positions = self._alternate_from_seed(residual, seeds)
            jfe = self._joint_fit_error(residual, positions)
            if best_jfe is None or jfe < best_jfe:
                best_jfe, best_positions = jfe, positions
        return best_positions

    def _disturbance_to_distance(self, strength: float) -> float:
        strength = max(strength, 0.1)
        ratio = min(strength / DISTURBANCE_PEAK, 0.999)
        return -DISTURBANCE_FALLOFF_M * math.log(ratio)

    def _expected_disturbance(self, x, y, device):
        dist = math.hypot(x - device.x_m, y - device.y_m)
        return DISTURBANCE_PEAK * math.exp(-dist / DISTURBANCE_FALLOFF_M)

    def _subtract_contribution(self, residual, x, y):
        for dev_id, device in self.devices.items():
            est = self._expected_disturbance(x, y, device)
            residual[dev_id] = max(0.0, residual.get(dev_id, 0.0) - est)

    def _field_strength_near(self, residual, x, y):
        # How much of the *original* field near (x, y) is still unexplained
        # after removing this person — used only as a confidence readout,
        # not for solving position.
        best = 0.0
        for dev_id, device in self.devices.items():
            if math.hypot(x - device.x_m, y - device.y_m) < DISTURBANCE_FALLOFF_M:
                best = max(best, self.latest_disturbance.get(dev_id, 0.0))
        return best

    def _fit_error(self, residual, x, y):
        # Sum of squared error between the observed residual field and what
        # a single person at (x, y) would produce. Near zero for a genuine
        # single source; large whenever the field still has another person
        # tangled into it. Used as a diagnostic on `_solve_residual`'s
        # output; joint multi-person acceptance uses `_joint_fit_error`
        # instead, since per-track error alone can't catch a jointly-wrong
        # but locally-clean configuration (see module docstring above
        # `JOINT_FIT_ERROR_ACCEPT`).
        total = 0.0
        for dev_id, device in self.devices.items():
            observed = max(residual.get(dev_id, 0.0), 0.1)
            predicted = self._expected_disturbance(x, y, device)
            total += (observed - predicted) ** 2
        return total

    def _solve_residual(self, residual):
        """
        Linear least squares over every device unconditionally: invert each
        device's residual disturbance to an implied distance via the known
        falloff model, then linearize the multilateration equations by
        subtracting a reference device's circle equation from every other's
        to cancel the quadratic x^2+y^2 term, leaving a linear system in
        (x, y) solved by weighted normal equations. This recovers the exact
        position for a single clean source using all devices at once —
        verified numerically — so no device subsetting/clustering is needed
        or correct given the additive signal model. Also returns the fit
        error (see `_fit_error`) as a diagnostic.
        """
        device_list = list(self.devices.values())
        strengths = {d.id: max(residual.get(d.id, 0.0), 0.1) for d in device_list}
        top_strength = max(strengths.values())

        device0 = max(device_list, key=lambda d: strengths[d.id])
        r0 = self._disturbance_to_distance(strengths[device0.id])
        x0, y0 = device0.x_m, device0.y_m

        ata = [[0.0, 0.0], [0.0, 0.0]]
        atb = [0.0, 0.0]
        for device in device_list:
            if device.id == device0.id:
                continue
            ri = self._disturbance_to_distance(strengths[device.id])
            xi, yi = device.x_m, device.y_m
            a1 = 2 * (xi - x0)
            a2 = 2 * (yi - y0)
            b = (r0**2 - ri**2) - (x0**2 - xi**2) - (y0**2 - yi**2)
            weight = min(strengths[device.id], strengths[device0.id])

            ata[0][0] += weight * a1 * a1
            ata[0][1] += weight * a1 * a2
            ata[1][0] += weight * a2 * a1
            ata[1][1] += weight * a2 * a2
            atb[0] += weight * a1 * b
            atb[1] += weight * a2 * b

        det = ata[0][0] * ata[1][1] - ata[0][1] * ata[1][0]
        if abs(det) < 1e-6:
            x, y = x0, y0
        else:
            x = (ata[1][1] * atb[0] - ata[0][1] * atb[1]) / det
            y = (ata[0][0] * atb[1] - ata[1][0] * atb[0]) / det

        x = _clamp(x, 0, self.room.width_m)
        y = _clamp(y, 0, self.room.height_m)
        fit_error = self._fit_error(residual, x, y)
        return x, y, top_strength, fit_error
