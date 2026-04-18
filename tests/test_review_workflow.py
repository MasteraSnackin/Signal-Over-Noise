import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from api.main import app
from db import db


def reset_store() -> None:
    for table in db.values():
        table.clear()


class ReviewWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_store()
        self.client_manager = TestClient(app)
        self.client = self.client_manager.__enter__()

    def tearDown(self) -> None:
        self.client_manager.__exit__(None, None, None)
        reset_store()

    def create_case(self, *, customer_id: str, campaign_type: str = "elderly_checkin") -> None:
        response = self.client.post(
            "/api/v1/video/create_job",
            json={
                "customer_id": customer_id,
                "name": "Review Workflow Test",
                "campaign_type": campaign_type,
                "plan": "first-response-reminder",
                "days_to_expiry": 1,
            },
        )
        self.assertEqual(response.status_code, 200)

    def submit_demo_voice_note(self, *, customer_id: str, campaign_type: str = "elderly_checkin") -> None:
        response = self.client.post(
            "/api/v1/voice_note/mock_submit",
            json={
                "customer_id": customer_id,
                "campaign_type": campaign_type,
            },
        )
        self.assertEqual(response.status_code, 200)

    def mark_reviewed(
        self,
        *,
        customer_id: str,
        outcome: str = "routine_followup",
        note: str = "Reviewed in test.",
        campaign_type: str = "elderly_checkin",
    ):
        return self.client.post(
            "/api/v1/video/mark_reviewed",
            json={
                "customer_id": customer_id,
                "campaign_type": campaign_type,
                "reviewed_by": "Test Reviewer",
                "outcome": outcome,
                "source": "unit_test",
                "note": note,
            },
        )

    def test_cannot_review_case_without_voice_note(self) -> None:
        customer_id = f"test_review_guard_{uuid4().hex[:8]}"
        self.create_case(customer_id=customer_id)

        response = self.mark_reviewed(customer_id=customer_id)

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(payload["error"]["code"], "VALIDATION_ERROR")
        self.assertEqual(
            payload["error"]["message"],
            "A voice note must be submitted before this case can be marked reviewed.",
        )

    def test_duplicate_review_is_blocked_until_a_new_voice_note_arrives(self) -> None:
        customer_id = f"test_review_reopen_{uuid4().hex[:8]}"
        self.create_case(customer_id=customer_id)
        self.submit_demo_voice_note(customer_id=customer_id)

        first_review = self.mark_reviewed(customer_id=customer_id)
        self.assertEqual(first_review.status_code, 200)

        duplicate_review = self.mark_reviewed(
            customer_id=customer_id,
            outcome="escalated",
            note="This should be rejected for the same voice note.",
        )
        self.assertEqual(duplicate_review.status_code, 400)
        duplicate_payload = duplicate_review.json()
        self.assertEqual(duplicate_payload["error"]["code"], "VALIDATION_ERROR")
        self.assertEqual(
            duplicate_payload["error"]["message"],
            "This case is already marked reviewed for the latest voice note.",
        )

        self.submit_demo_voice_note(customer_id=customer_id)
        reopened_review = self.mark_reviewed(
            customer_id=customer_id,
            outcome="escalated",
            note="Allowed after a newer voice note.",
        )
        self.assertEqual(reopened_review.status_code, 200)
        self.assertEqual(reopened_review.json()["outcome"], "escalated")

        review_status = self.client.get(
            "/api/v1/video/review_status",
            params={
                "customer_id": customer_id,
                "campaign_type": "elderly_checkin",
            },
        )
        self.assertEqual(review_status.status_code, 200)
        status_payload = review_status.json()
        self.assertEqual(status_payload["status"], "reviewed")
        self.assertEqual(status_payload["review"]["outcome"], "escalated")

    def test_dashboard_review_summary_uses_latest_active_review_per_case(self) -> None:
        customer_id = f"test_review_summary_{uuid4().hex[:8]}"
        self.create_case(customer_id=customer_id)
        self.submit_demo_voice_note(customer_id=customer_id)

        first_review = self.mark_reviewed(
            customer_id=customer_id,
            outcome="routine_followup",
            note="Initial review outcome.",
        )
        self.assertEqual(first_review.status_code, 200)

        self.submit_demo_voice_note(customer_id=customer_id)
        second_review = self.mark_reviewed(
            customer_id=customer_id,
            outcome="closed",
            note="Latest review outcome after a newer signal.",
        )
        self.assertEqual(second_review.status_code, 200)

        overview = self.client.get("/api/v1/video/dashboard_overview")
        self.assertEqual(overview.status_code, 200)
        payload = overview.json()

        self.assertEqual(payload["review_summary"]["total_reviews"], 1)
        self.assertEqual(
            payload["review_summary"]["outcome_counts"],
            {
                "escalated": 0,
                "routine_followup": 0,
                "closed": 1,
            },
        )

        queue_item = next(
            item for item in payload["care_queue"]
            if item["customer_id"] == customer_id and item["campaign_type"] == "elderly_checkin"
        )
        self.assertEqual(queue_item["status"], "reviewed")
        self.assertEqual(queue_item["review_outcome"], "closed")
        self.assertTrue(queue_item["review_active"])

    def test_reset_demo_clears_automation_runs(self) -> None:
        batch_run = self.client.post(
            "/api/v1/automation/demo_batch_outreach",
            json={
                "execution_mode": "local",
                "send_sms": False,
                "source": "unit_test_reset",
            },
        )
        self.assertEqual(batch_run.status_code, 200)
        run_payload = batch_run.json()
        run_id = run_payload["run_id"]

        reset_response = self.client.post("/api/v1/video/reset_demo")
        self.assertEqual(reset_response.status_code, 200)
        reset_payload = reset_response.json()
        self.assertEqual(reset_payload["automation_runs_cleared"], 1)

        cleared_run = self.client.get(f"/api/v1/automation/runs/{run_id}")
        self.assertEqual(cleared_run.status_code, 404)
        cleared_payload = cleared_run.json()
        self.assertEqual(cleared_payload["error"]["code"], "NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
