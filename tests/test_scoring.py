import copy
import csv
import io
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.scoring import score_user, timestamp
from tests.test_explorer import FakeGitHub, execute, fork, owner, response, settings, stored_row, summary

START, END = "2025-09-22T00:00:00Z", "2026-09-22T00:00:00Z"


def profile():
    return {"type": "User", "account_created_at": "2010-01-01T00:00:00Z",
            "top_repository_stars": 1000, "original_repositories": 20}


def activity(**changes):
    return {"status": "completed", "from": START, "to": END,
            "total": 1000, "restricted": 0, "pull_requests": 50, "reviews": 50, "issues": 50,
            "active_weeks": 40, "last_contribution_at": "2026-09-21T00:00:00Z", **changes}


def score(o=None, c=None):
    return score_user(profile() if o is None else o, activity() if c is None else c, START, END)


def test_score_bounds_and_explainable_full_score():
    result = score()
    assert result["total"] == 100
    assert result["professional"] == result["activity"] == 50
    assert result["coverage"] == 100 and result["missing"] == []
    assert result["status"] == "completed"
    assert sum(component["points"] for component in result["components"].values()) == 100
    assert score({**profile(), "top_repository_stars": 10**12}, activity(total=10**12))["total"] == 100
    assert score({**profile(), "top_repository_stars": 10**400}, activity(total=10**400))["total"] == 100
    zero = score({**profile(), "account_created_at": END, "top_repository_stars": 0,
                  "original_repositories": 0}, activity(total=0, restricted=0, pull_requests=0,
                    reviews=0, issues=0, active_weeks=None, last_contribution_at=None))
    assert zero["total"] == 0 and zero["coverage"] == 100


def test_public_contributions_dominate_equal_private_volume():
    public = score(c=activity(restricted=0))
    private = score(c=activity(restricted=1000))
    assert public["total"] - private["total"] == 28
    assert private["components"]["total_contributions"]["points"] == 7
    assert private["public_contributions"] == 0
    scores = [score(c=activity(restricted=r))["total"] for r in range(1000, -1, -100)]
    assert scores == sorted(scores)


def test_recency_consistency_and_missing_are_distinct():
    recent = score(c=activity(last_contribution_at="2026-08-22T00:00:00Z", active_weeks=20))
    assert recent["components"]["recency"]["points"] == 2.5
    assert recent["components"]["consistency"]["points"] == 5
    missing = score(c={"status": "failed"})
    assert missing["total"] == 40 and missing["coverage"] == 40
    assert missing["status"] == "provisional"
    assert missing["components"]["total_contributions"]["points"] is None
    unknown = score({"type": "User"}, {"status": "failed"})
    assert unknown["coverage"] == 0 and unknown["total"] == 0
    assert unknown["status"] == "provisional"


@pytest.mark.parametrize("changes", [
    {"restricted": 1001}, {"from": "2026-09-01T00:00:00Z"},
    {"to": "2026-09-23T00:00:00Z"}, {"status": "pending"}, {"total": -1},
])
def test_invalid_contribution_inputs_do_not_earn_points(changes):
    result = score(c=activity(**changes))
    assert result["activity"] == 0 and result["coverage"] == 40


def test_age_unknown_recent_dates_and_organization():
    assert score({**profile(), "account_created_at": "2027-01-01"})["components"]["account_age"]["points"] is None
    assert score(c=activity(last_contribution_at=END))["components"]["recency"]["points"] is None
    assert score(c=activity(last_contribution_at=START))["components"]["recency"]["points"] < 0.01
    assert score({"type": "Organization"})["status"] == "not_applicable"
    assert score_user(profile(), activity(), START, END, ready=False)["total"] is None


def test_fixed_thresholds_diminish_stars_and_ignore_profile_text():
    results = [score({**profile(), "top_repository_stars": value}) for value in (0, 10, 100, 1000, 10000)]
    assert [r["total"] for r in results] == sorted(r["total"] for r in results)
    assert results[-1]["total"] == results[-2]["total"]
    assert score({**profile(), "bio": "expert", "company": "Famous company", "location": "Toronto"}) == score()


def test_scaling_maximum_covers_filtered_rows_before_pagination(tmp_path):
    with TestClient(create_app(settings(tmp_path), start_worker=False)) as client:
        store = client.app.state.store
        task = store.create("https://github.com/up/repo", "test", 1000)
        rows = []
        for name in ("high", "low", "zero", "org"):
            row = stored_row()
            row["id"] = name
            row["owner"].update(profile(), id=name, login=name)
            row["contribution"].update(activity())
            if name == "low":
                row["owner"]["top_repository_stars"] = 0
            elif name == "zero":
                row["owner"].update(account_created_at=END, top_repository_stars=0, original_repositories=0)
                row["contribution"].update(activity(total=0, restricted=0, pull_requests=0, reviews=0,
                                                    issues=0, active_weeks=0, last_contribution_at=None))
            elif name == "org":
                row["owner"]["type"] = "Organization"
            rows.append(row)
        store.save_page(task["id"], rows, None, True, 4)
        store.update(task["id"], status="completed", **{"from": START, "to": END})
        url = f"/api/tasks/{task['id']}/results"
        page = client.get(url, params={"page": 2, "per_page": 1}).json()
        assert page["items"][0]["score"]["total"] == 75
        assert page["max_score"] == 100
        for search, expected in (("low", 75), ("zero", 0), ("org", None), ("missing", None)):
            assert client.get(url, params={"search": search}).json()["max_score"] == expected
        assert client.get(url, params={"account_created_from": "2026-09-22"}).json()["max_score"] == 0


async def test_scoring_data_collected_with_no_additional_requests(tmp_path):
    raw_owner = owner()
    raw_owner["topRepositories"] = {"totalCount": 12, "nodes": [{"stargazerCount": i} for i in range(1, 11)]}
    fake = FakeGitHub([[fork(1, raw_owner)]])

    def handler(request):
        body = json.loads(request.content)
        if "nodes(ids:" not in body["query"]:
            return fake(request)
        fake.calls.append(body)
        start, end = timestamp(body["variables"]["from"]), timestamp(body["variables"]["to"])
        assert end.hour == 23 and end.minute == 59 and end.second == 59
        days = [{"date": (start + timedelta(days=i)).date().isoformat(), "contributionCount": 3}
                for i in range(365)]
        data = summary(1095)
        data["contributionCalendar"]["weeks"] = [{"contributionDays": days[i:i + 7]} for i in range(0, 365, 7)]
        return response({"nodes": [{"id": "U1", "contributionsCollection": data}]})

    store, task_id, _ = await execute(tmp_path, handler)
    try:
        assert store.get(task_id)["status"] == "completed"
        assert len(fake.calls) == 3  # Same META + FORKS + CONTRIBUTIONS as before scoring.
        item = store.rows(task_id)[0]
        assert item["owner"]["top_repository_stars"] == 55
        assert item["owner"]["original_repositories"] == 12
        assert item["contribution"]["active_weeks"] == 53
        assert "weeks" not in item["contribution"]
        assert item["score"]["coverage"] == 100
        assert item["score"]["activity"] == 50
    finally:
        store.engine.dispose()


def test_import_rescores_old_and_tampered_snapshots_and_sorts(tmp_path):
    with TestClient(create_app(settings(tmp_path), start_worker=False)) as client:
        store = client.app.state.store
        task = store.create("https://github.com/up/repo", "test", 1000)
        rows = [stored_row(), copy.deepcopy(stored_row()), copy.deepcopy(stored_row())]
        rows[0]["owner"].update(profile())
        rows[0]["contribution"] = {**rows[0]["contribution"], **activity()}
        rows[1]["id"], rows[1]["owner"]["id"] = "old", "old-user"
        rows[2]["id"], rows[2]["owner"]["id"] = "org", "org-user"
        rows[2]["owner"]["type"] = "Organization"
        rows[2]["contribution"] = {"status": "not_applicable"}
        store.update(task["id"], **{"from": START, "to": END})
        store.save_page(task["id"], rows, None, True, 3)
        assert all(row["score"]["total"] is None for row in store.rows(task["id"]))
        store.update(task["id"], status="completed")
        url = f"/api/tasks/{task['id']}"
        snapshot = client.get(url + "/export").json()
        for row in snapshot["results"]:
            row["score"] = {"total": 999, "version": "untrusted"}
        imported = client.post("/api/imports", json=snapshot)
        assert imported.status_code == 201
        imported_url = f"/api/tasks/{imported.json()['id']}"
        results = client.get(imported_url + "/results").json()["items"]
        assert [r["id"] for r in results] == ["F-export", "old", "org"]
        assert results[0]["score"]["total"] == 100
        assert results[1]["score"]["status"] == "provisional"
        assert results[1]["score"]["coverage"] == 55
        assert results[2]["score"]["total"] is None
        assert store.get(imported.json()["id"])["requests"] == 0
        for field in ("score", "professional", "activity"):
            asc = client.get(imported_url + f"/results?sort={field}&direction=asc").json()["items"]
            assert [r["id"] for r in asc] == ["old", "F-export", "org"]
        filtered = client.get(imported_url + "/results?search=user").json()["items"]
        assert filtered[0]["score"] == results[0]["score"]
        csv_rows = list(csv.DictReader(io.StringIO(client.get(imported_url + "/export?format=csv").content.decode("utf-8-sig"))))
        assert next(row for row in csv_rows if row["fork_id"] == "F-export")["score"] == "100"
        # Genuine older snapshots have none of the new fields and still import offline.
        snapshot["task"]["data_version"] = 2
        for row in snapshot["results"]:
            row.pop("score", None)
            for key in ("top_repository_stars", "original_repositories"):
                row["owner"].pop(key, None)
            for key in ("active_weeks", "last_contribution_at"):
                row["contribution"].pop(key, None)
        legacy = client.post("/api/imports", json=snapshot)
        assert legacy.status_code == 201
        assert client.get(f"/api/tasks/{legacy.json()['id']}/results").json()["items"][0]["score"]["status"] == "provisional"
