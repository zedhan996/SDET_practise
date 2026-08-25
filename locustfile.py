import os
import random

from locust import HttpUser, between, task


class ItemSearchUser(HttpUser):
    """Simulate a user browsing and searching the read-only item API."""

    # Can be overridden with: locust --host http://127.0.0.1:8000
    host = os.getenv("LOCUST_HOST", "http://127.0.0.1:8000")
    wait_time = between(1, 3)

    @task(3)
    def browse_items(self):
        """Load the complete item list."""
        self._get_items()

    @task(1)
    def search_items(self):
        """Search for a commonly used item keyword."""
        keyword = random.choice(("iPhone", "Keyboard"))
        self._get_items(params={"keyword": keyword})

    def _get_items(self, params=None):
        """Validate both HTTP-level and business-level success."""
        with self.client.get(
            "/items/search",
            params=params,
            name="GET /items/search",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"unexpected status code: {response.status_code}")
                return

            try:
                body = response.json()
            except ValueError:
                response.failure("response body is not valid JSON")
                return

            if body.get("status") != "success":
                response.failure("business status is not success")
                return

            if not isinstance(body.get("total"), int):
                response.failure("total is not an integer")
                return

            if not isinstance(body.get("data"), list):
                response.failure("data is not a list")
