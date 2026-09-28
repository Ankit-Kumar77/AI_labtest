import httpx

from app.core.config import settings


class GitHubClient:
    def __init__(self):
        self.token = settings.GITHUB_TOKEN
        self.repo = settings.GITHUB_REPO
        self.api_url = settings.GITHUB_API_URL.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        } if self.token else {}

    def _get(self, endpoint: str, params: dict | None = None):
        if not self.token or not self.repo:
            return {"success": False, "error": "GitHub not configured (missing GITHUB_TOKEN or GITHUB_REPO)"}

        url = f"{self.api_url}/repos/{self.repo}{endpoint}"
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.get(url, headers=self.headers, params=params)
                if resp.status_code == 401:
                    return {"success": False, "error": "Invalid GitHub token"}
                if resp.status_code == 404:
                    return {"success": False, "error": f"Repository not found: {self.repo}"}
                resp.raise_for_status()
                return {"success": True, "data": resp.json()}
        except httpx.HTTPStatusError as e:
            return {"success": False, "error": f"GitHub API error: {e.response.status_code} - {e.response.text}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def health(self):
        result = self._get("")
        if result["success"]:
            return {"success": True, "status": "connected", "repo": result["data"].get("full_name")}
        return result

    def get_branches(self):
        result = self._get("/branches", {"per_page": 100})
        if result["success"]:
            result["data"] = [b["name"] for b in result["data"]]
        return result

    def get_commits(self, sha: str | None = None, since: str | None = None, until: str | None = None, limit: int = 30):
        params = {"per_page": min(limit, 100), "page": 1}
        if sha:
            params["sha"] = sha
        if since:
            params["since"] = since
        if until:
            params["until"] = until
        return self._get("/commits", params)

    def get_workflow_runs(self, limit: int = 20):
        params = {"per_page": min(limit, 100), "page": 1}
        result = self._get("/actions/runs", params)
        if result["success"] and "workflow_runs" in result["data"]:
            result["data"] = result["data"]["workflow_runs"]
        return result

    def get_workflow_run(self, run_id: int):
        return self._get(f"/actions/runs/{run_id}")

    def get_workflow_run_jobs(self, run_id: int):
        result = self._get(f"/actions/runs/{run_id}/jobs", {"per_page": 100})
        if result["success"] and "jobs" in result["data"]:
            result["data"] = result["data"]["jobs"]
        return result

    def get_workflow_run_logs(self, run_id: int, job_id: int):
        return self._get(f"/actions/runs/{run_id}/jobs/{job_id}/logs")

    def get_workflow_run_jobs_with_logs(self, run_id: int):
        """Fetch jobs and attach log snippets for failed steps."""
        jobs_result = self.get_workflow_run_jobs(run_id)
        if not jobs_result.get("success"):
            return jobs_result

        enriched_jobs = []
        for job in jobs_result.get("data") or []:
            job_data = dict(job)
            failed_steps = [
                s for s in (job.get("steps") or [])
                if s.get("conclusion") == "failure"
            ]

            if failed_steps:
                logs_result = self.get_workflow_run_logs(run_id, job["id"])
                if logs_result.get("success"):
                    raw_logs = logs_result.get("data") or ""
                    job_data["logs_snippet"] = raw_logs[-3000:]
                else:
                    job_data["logs_snippet"] = (
                        f"[logs unavailable: {logs_result.get('error', 'unknown')}]"
                    )
            else:
                job_data["logs_snippet"] = None

            enriched_jobs.append(job_data)

        jobs_result["data"] = enriched_jobs
        return jobs_result

    def get_issues(self, state: str = "open", limit: int = 30):
        params = {"state": state, "per_page": min(limit, 100), "page": 1, "sort": "updated", "direction": "desc"}
        result = self._get("/issues", params)
        if result["success"]:
            result["data"] = [i for i in result["data"] if "pull_request" not in i]
        return result

    def get_repo_info(self):
        return self._get("")


github = GitHubClient()


def health():
    return github.health()


def get_branches():
    return github.get_branches()


def get_commits(sha: str | None = None, since: str | None = None, until: str | None = None, limit: int = 30):
    return github.get_commits(sha, since, until, limit)


def get_workflow_runs(limit: int = 20):
    return github.get_workflow_runs(limit)


def get_workflow_run(run_id: int):
    return github.get_workflow_run(run_id)


def get_workflow_run_jobs_with_logs(run_id: int):
    return github.get_workflow_run_jobs_with_logs(run_id)


def get_issues(state: str = "open", limit: int = 30):
    return github.get_issues(state, limit)


def get_repo_info():
    return github.get_repo_info()