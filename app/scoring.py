"""Deterministic, absolute scoring of observable GitHub signals (not skill certification)."""

from datetime import datetime, timezone
from math import log1p

VERSION = "github-signals-v1"
WEIGHTS = {
    "stars": 25, "collaboration": 10, "account_age": 10, "original_repositories": 5,
    "public_contributions": 28, "total_contributions": 7,
    "consistency": 10, "recency": 5,
}
PROFESSIONAL = ("stars", "collaboration", "account_age", "original_repositories")


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        return None


def count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def scaled(value, cap):
    return log1p(min(value, cap)) / log1p(cap)


def score_user(owner, contribution, start, end, ready=True):
    result = {"version": VERSION, "as_of": end, "status": "pending", "total": None,
              "professional": None, "activity": None, "coverage": 0,
              "components": {}, "missing": [], "public_contributions": None}
    if owner.get("type") == "Organization":
        return {**result, "status": "not_applicable"}
    if not ready:
        return result
    points = dict.fromkeys(WEIGHTS)
    stars = count(owner.get("top_repository_stars"))
    repos = count(owner.get("original_repositories"))
    if stars is not None:
        points["stars"] = 25 * scaled(stars, 1000)
    if repos is not None:
        points["original_repositories"] = 5 * scaled(repos, 20)
    reference, began, created = timestamp(end), timestamp(start), timestamp(owner.get("account_created_at"))
    if reference and created and created <= reference:
        points["account_age"] = 10 * scaled((reference - created).total_seconds() / (365.25 * 86400), 8)

    c = contribution
    # Different imported windows are not comparable to a full year; never annualize short bursts.
    valid_window = (reference and began and (reference - began).total_seconds() == 365 * 86400
                    and timestamp(c.get("from")) == began and timestamp(c.get("to")) == reference)
    total, restricted = count(c.get("total")), count(c.get("restricted"))
    valid = (c.get("status") == "completed" and valid_window and total is not None
             and restricted is not None and restricted <= total)
    if valid:
        public = total - restricted
        result["public_contributions"] = public
        points["public_contributions"] = 28 * scaled(public, 1000)
        points["total_contributions"] = 7 * scaled(total, 1000)
        pr, reviews, issues = (count(c.get(key)) for key in ("pull_requests", "reviews", "issues"))
        if all(value is not None for value in (pr, reviews, issues)):
            points["collaboration"] = 6 * scaled(pr, 50) + 3 * scaled(reviews, 50) + scaled(issues, 50)
        weeks = count(c.get("active_weeks"))
        if weeks is not None and weeks <= 53 and weeks <= total and (weeks > 0 or total == 0):
            points["consistency"] = 10 * min(1, weeks / 40)
        last = timestamp(c.get("last_contribution_at"))
        if total == 0:
            points["consistency"] = points["recency"] = 0.0
        elif last and began <= last < reference:
            # The most recent completed UTC day has age zero.
            days = max(0, (reference.date() - last.date()).days - 1)
            points["recency"] = 5 * 2 ** (-days / 30)

    missing = [key for key, value in points.items() if value is None]
    professional = sum(points[key] or 0 for key in PROFESSIONAL)
    activity = sum(value or 0 for key, value in points.items() if key not in PROFESSIONAL)
    return {**result, "status": "provisional" if missing else "completed",
            "total": round(min(100, professional + activity), 1),
            "professional": round(professional, 1), "activity": round(activity, 1),
            "coverage": sum(weight for key, weight in WEIGHTS.items() if points[key] is not None),
            "components": {key: {"points": round(value, 2) if value is not None else None,
                                 "maximum": WEIGHTS[key]} for key, value in points.items()},
            "missing": missing}
