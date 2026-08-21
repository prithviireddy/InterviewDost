"""GitHub service — fetch public repos for a user."""
import httpx


async def scrape_github(username: str) -> list[dict]:
    """
    Return a list of simplified repo objects for *username*.
    Returns an empty list if the request fails or the user has no public repos.
    """
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"https://api.github.com/users/{username}/repos")

    if not resp.is_success:
        return []

    data = resp.json()
    if not isinstance(data, list):
        return []

    return [
        {
            "description": repo.get("description"),
            "name": repo.get("name"),
            "fullName": repo.get("full_name"),
            "starCount": repo.get("stargazers_count"),
        }
        for repo in data
    ]
