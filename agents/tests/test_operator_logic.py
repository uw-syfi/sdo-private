
import unittest
from unittest.mock import MagicMock
from app_operator.apps.hotel import HotelApplication
from app_operator.operator import SummarizeAndRecommendNode, ApplicationOperator, AppStatus
from app_operator.application import HealthCheckResult


class TestRefactoring(unittest.TestCase):
    def test_hotel_application_path_resolution(self):
        app = HotelApplication()
        # Ensure path is resolved relative to the file, and points to something reasonable
        # We know it should be .../sds/deploy/deathstarbench/hotel/deploy.sh
        self.assertTrue(app.deploy_script.exists(),
                        f"Deploy script not found at {app.deploy_script}")
        self.assertTrue(
            app.health_check_script.exists(),
            f"Health check script not found at {app.health_check_script}")

    def test_compare_results_deterministic(self):
        # Mock operator
        op = MagicMock(spec=ApplicationOperator)
        node = SummarizeAndRecommendNode(op)

        # Case 1: Identical
        res1 = {"healthy": True, "message": "OK"}
        res2 = {"healthy": True, "message": "OK"}
        self.assertFalse(
            node._llm_compare_results(
                previous_result=res1,
                current_result=res2))

        # Case 2: Status change
        res3 = {"healthy": False, "message": "Error"}
        self.assertTrue(
            node._llm_compare_results(
                previous_result=res1,
                current_result=res3))

        # Case 3: Message change
        res4 = {"healthy": True, "message": "OK 2"}
        self.assertTrue(
            node._llm_compare_results(
                previous_result=res1,
                current_result=res4))

    def test_determine_app_status_deterministic(self):
        op = MagicMock(spec=ApplicationOperator)
        node = SummarizeAndRecommendNode(op)

        # Case 1: Healthy -> HEALTHY
        res_healthy = {"healthy": True}
        self.assertEqual(
            node._determine_app_status(
                result=res_healthy,
                is_deployed=True),
            AppStatus.HEALTHY)

        # Case 2: Not Deployed -> DOWN
        res_unhealthy = {"healthy": False}
        self.assertEqual(
            node._determine_app_status(
                result=res_unhealthy,
                is_deployed=False),
            AppStatus.DOWN)

        # Case 3: Unhealthy, critical keyword -> DOWN
        res_crit = {
            "healthy": False,
            "message": "Connection refused",
            "details": {
                "exit_code": 1}}
        self.assertEqual(
            node._determine_app_status(
                result=res_crit,
                is_deployed=True),
            AppStatus.DOWN)

        # Case 4: Unhealthy, no critical keyword -> WARNING
        res_warn = {
            "healthy": False,
            "message": "High latency",
            "details": {
                "exit_code": 1}}
        self.assertEqual(
            node._determine_app_status(
                result=res_warn,
                is_deployed=True),
            AppStatus.WARNING)


if __name__ == '__main__':
    unittest.main()
