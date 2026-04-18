import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from api.main import app
from api.services.tuner import EVENT_LOGS
from db import db


def reset_store() -> None:
    for table in db.values():
        table.clear()
    EVENT_LOGS.clear()


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

    def send_outreach(
        self,
        *,
        customer_id: str,
        phone_number: str = "+447700900123",
        campaign_type: str = "elderly_checkin",
    ):
        return self.client.post(
            "/api/v1/video/send_outreach",
            json={
                "customer_id": customer_id,
                "campaign_type": campaign_type,
                "phone_number": phone_number,
            },
        )

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

    def test_reopened_cases_clear_inactive_review_metadata_from_queue(self) -> None:
        customer_id = f"test_reopened_queue_{uuid4().hex[:8]}"
        self.create_case(customer_id=customer_id)
        self.submit_demo_voice_note(customer_id=customer_id)

        review = self.mark_reviewed(
            customer_id=customer_id,
            outcome="closed",
            note="Original closed review.",
        )
        self.assertEqual(review.status_code, 200)

        self.submit_demo_voice_note(customer_id=customer_id)

        queue = self.client.get("/api/v1/video/care_queue", params={"limit": 20})
        self.assertEqual(queue.status_code, 200)
        queue_item = next(
            item
            for item in queue.json()
            if item["customer_id"] == customer_id and item["campaign_type"] == "elderly_checkin"
        )

        self.assertEqual(queue_item["status"], "monitor")
        self.assertFalse(queue_item["review_active"])
        self.assertIsNone(queue_item["reviewed_at"])
        self.assertIsNone(queue_item["reviewed_by"])
        self.assertIsNone(queue_item["review_outcome"])
        self.assertIsNone(queue_item["review_note"])

        overview = self.client.get("/api/v1/video/dashboard_overview")
        self.assertEqual(overview.status_code, 200)
        overview_queue_item = next(
            item
            for item in overview.json()["care_queue"]
            if item["customer_id"] == customer_id and item["campaign_type"] == "elderly_checkin"
        )
        self.assertEqual(overview_queue_item["status"], "monitor")
        self.assertFalse(overview_queue_item["review_active"])
        self.assertIsNone(overview_queue_item["reviewed_at"])
        self.assertIsNone(overview_queue_item["reviewed_by"])
        self.assertIsNone(overview_queue_item["review_outcome"])
        self.assertIsNone(overview_queue_item["review_note"])

    def test_failed_twilio_delivery_can_only_be_retried_once(self) -> None:
        customer_id = f"test_twilio_retry_{uuid4().hex[:8]}"
        self.create_case(customer_id=customer_id)

        send = self.send_outreach(customer_id=customer_id)
        self.assertEqual(send.status_code, 200)
        original_delivery = send.json()["delivery"]
        original_sid = original_delivery["provider_message_id"]

        simulate_failure = self.client.post(
            "/api/v1/video/twilio_simulate_status",
            json={"message_sid": original_sid, "status": "failed"},
        )
        self.assertEqual(simulate_failure.status_code, 200)

        first_retry = self.client.post(
            "/api/v1/video/retry_outreach",
            json={"message_sid": original_sid},
        )
        self.assertEqual(first_retry.status_code, 200)
        retry_payload = first_retry.json()
        self.assertEqual(retry_payload["original_delivery"]["status"], "retried")
        self.assertEqual(retry_payload["retried_delivery"]["status"], "queued")

        second_retry = self.client.post(
            "/api/v1/video/retry_outreach",
            json={"message_sid": original_sid},
        )
        self.assertEqual(second_retry.status_code, 400)
        second_payload = second_retry.json()
        self.assertEqual(second_payload["error"]["code"], "VALIDATION_ERROR")
        self.assertEqual(
            second_payload["error"]["message"],
            "Only failed or undelivered Twilio demo messages can be retried.",
        )
        self.assertEqual(second_payload["error"]["details"]["status"], "retried")

        deliveries = self.client.get(
            "/api/v1/video/outreach_deliveries",
            params={
                "customer_id": customer_id,
                "campaign_type": "elderly_checkin",
                "limit": 10,
            },
        )
        self.assertEqual(deliveries.status_code, 200)
        history = deliveries.json()["deliveries"]
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["status"], "queued")
        self.assertEqual(history[1]["status"], "retried")

    def test_failed_twilio_message_reuses_existing_fallback_handoff(self) -> None:
        customer_id = f"test_twilio_fallback_{uuid4().hex[:8]}"
        self.create_case(customer_id=customer_id)

        send = self.send_outreach(customer_id=customer_id)
        self.assertEqual(send.status_code, 200)
        original_sid = send.json()["delivery"]["provider_message_id"]

        simulate_failure = self.client.post(
            "/api/v1/video/twilio_simulate_status",
            json={"message_sid": original_sid, "status": "failed"},
        )
        self.assertEqual(simulate_failure.status_code, 200)

        first_fallback = self.client.post(
            "/api/v1/video/prepare_fallback_link",
            json={
                "customer_id": customer_id,
                "campaign_type": "elderly_checkin",
                "message_sid": original_sid,
                "source": "unit_test",
            },
        )
        self.assertEqual(first_fallback.status_code, 200)
        first_payload = first_fallback.json()

        second_fallback = self.client.post(
            "/api/v1/video/prepare_fallback_link",
            json={
                "customer_id": customer_id,
                "campaign_type": "elderly_checkin",
                "message_sid": original_sid,
                "source": "unit_test_repeat",
            },
        )
        self.assertEqual(second_fallback.status_code, 200)
        second_payload = second_fallback.json()
        self.assertEqual(second_payload, first_payload)

        handoffs = self.client.get(
            "/api/v1/video/fallback_handoffs",
            params={
                "customer_id": customer_id,
                "campaign_type": "elderly_checkin",
                "limit": 10,
            },
        )
        self.assertEqual(handoffs.status_code, 200)
        history = handoffs.json()["handoffs"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["message_sid"], original_sid)
        self.assertEqual(history[0]["delivery_status"], "failed")

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

        cleared_runs = self.client.get("/api/v1/automation/runs", params={"limit": 10})
        self.assertEqual(cleared_runs.status_code, 200)
        self.assertEqual(cleared_runs.json()["runs"], [])

    def test_automation_runs_endpoint_lists_latest_runs_first(self) -> None:
        first_run = self.client.post(
            "/api/v1/automation/demo_batch_outreach",
            json={
                "execution_mode": "local",
                "send_sms": False,
                "source": "unit_test_list_first",
            },
        )
        self.assertEqual(first_run.status_code, 200)
        first_run_id = first_run.json()["run_id"]

        second_run = self.client.post(
            "/api/v1/automation/demo_batch_outreach",
            json={
                "execution_mode": "local",
                "send_sms": False,
                "source": "unit_test_list_second",
            },
        )
        self.assertEqual(second_run.status_code, 200)
        second_run_id = second_run.json()["run_id"]

        runs_response = self.client.get("/api/v1/automation/runs", params={"limit": 10})
        self.assertEqual(runs_response.status_code, 200)
        payload = runs_response.json()

        self.assertEqual(len(payload["runs"]), 2)
        self.assertEqual(payload["runs"][0]["run_id"], second_run_id)
        self.assertEqual(payload["runs"][1]["run_id"], first_run_id)

    def test_batch_outreach_processed_recipients_counts_all_attempts(self) -> None:
        response = self.client.post(
            "/api/v1/automation/batch_outreach",
            json={
                "execution_mode": "local",
                "send_sms": False,
                "source": "unit_test_processed_counts",
                "recipients": [
                    {
                        "customer_id": "processed_ok_001",
                        "name": "Okay Recipient",
                        "campaign_type": "elderly_checkin",
                    },
                    {
                        "customer_id": "processed_bad_001",
                        "name": "   ",
                        "campaign_type": "elderly_checkin",
                    },
                ],
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload["total_recipients"], 2)
        self.assertEqual(payload["processed_recipients"], 2)
        self.assertEqual(payload["error_count"], 1)
        self.assertEqual(payload["status"], "completed_with_errors")
        self.assertEqual(payload["results"][0]["status"], "completed")
        self.assertEqual(payload["results"][1]["status"], "failed")

    def test_batch_outreach_is_failed_when_every_recipient_fails(self) -> None:
        response = self.client.post(
            "/api/v1/automation/batch_outreach",
            json={
                "execution_mode": "local",
                "send_sms": False,
                "source": "unit_test_all_failed",
                "recipients": [
                    {
                        "customer_id": "failed_bad_001",
                        "name": "   ",
                        "campaign_type": "elderly_checkin",
                    },
                    {
                        "customer_id": "failed_bad_002",
                        "name": "   ",
                        "campaign_type": "primary_care",
                    },
                ],
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload["total_recipients"], 2)
        self.assertEqual(payload["processed_recipients"], 2)
        self.assertEqual(payload["error_count"], 2)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["results"][0]["status"], "failed")
        self.assertEqual(payload["results"][1]["status"], "failed")

    def test_batch_outreach_missing_phone_does_not_create_video_job(self) -> None:
        customer_id = f"missing_phone_{uuid4().hex[:8]}"
        response = self.client.post(
            "/api/v1/automation/batch_outreach",
            json={
                "execution_mode": "local",
                "send_sms": True,
                "source": "unit_test_missing_phone",
                "recipients": [
                    {
                        "customer_id": customer_id,
                        "name": "Missing Phone Recipient",
                        "campaign_type": "elderly_checkin",
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload["created_jobs"], 0)
        self.assertEqual(payload["created_deliveries"], 0)
        self.assertEqual(payload["error_count"], 1)
        self.assertEqual(payload["status"], "failed")
        self.assertIsNone(payload["results"][0]["video_job_id"])
        self.assertEqual(
            payload["results"][0]["error_message"],
            "phone_number is required when send_sms is enabled.",
        )

        history = self.client.get(
            "/api/v1/video/history",
            params={
                "customer_id": customer_id,
                "campaign_type": "elderly_checkin",
                "limit": 10,
            },
        )
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json(), [])

    def test_batch_outreach_rejects_duplicate_patient_journeys(self) -> None:
        customer_id = f"duplicate_recipient_{uuid4().hex[:8]}"
        response = self.client.post(
            "/api/v1/automation/batch_outreach",
            json={
                "execution_mode": "local",
                "send_sms": False,
                "source": "unit_test_duplicate_journeys",
                "recipients": [
                    {
                        "customer_id": customer_id,
                        "name": "Duplicate Journey One",
                        "campaign_type": "elderly_checkin",
                    },
                    {
                        "customer_id": customer_id,
                        "name": "Duplicate Journey Two",
                        "campaign_type": "elderly_checkin",
                    },
                ],
            },
        )
        self.assertEqual(response.status_code, 400)
        payload = response.json()

        self.assertEqual(payload["error"]["code"], "VALIDATION_ERROR")
        self.assertEqual(
            payload["error"]["message"],
            "Each batch recipient must target a unique customer_id and campaign_type pair.",
        )

        history = self.client.get(
            "/api/v1/video/history",
            params={
                "customer_id": customer_id,
                "campaign_type": "elderly_checkin",
                "limit": 10,
            },
        )
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json(), [])

        runs = self.client.get("/api/v1/automation/runs", params={"limit": 10})
        self.assertEqual(runs.status_code, 200)
        self.assertEqual(runs.json()["runs"], [])


if __name__ == "__main__":
    unittest.main()
