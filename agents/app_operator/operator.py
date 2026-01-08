"""Main operator logic for managing application lifecycle."""

import signal
import sys
import time
from datetime import datetime
from enum import Enum, auto
from typing import Any, Optional, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app_operator.application import Application


class AppStatus(Enum):
    """Application health status."""
    DOWN = auto()
    WARNING = auto()
    HEALTHY = auto()


class _HealthMonitorState(TypedDict, total=False):
    check_count: int
    timestamp: str
    result: dict[str, Any]
    previous_result: dict[str, Any]
    summary: str
    shutdown_now: bool
    deployed: bool
    deployment_message: str
    needs_redeploy: bool
    app_down: bool


class GraphNode:
    """Base class for LangGraph nodes."""

    def __init__(self, operator: "ApplicationOperator"):
        """Initialize the node with a reference to the operator.

        Args:
            operator: The ApplicationOperator instance that owns this node.
        """
        self.operator = operator

    def __call__(self, state: _HealthMonitorState) -> _HealthMonitorState:
        """Execute the node logic.

        Args:
            state: The current graph state.

        Returns:
            The updated graph state.
        """
        raise NotImplementedError


class GraphRouter:
    """Base class for LangGraph routing functions."""

    def __init__(self, operator: "ApplicationOperator"):
        """Initialize the router with a reference to the operator.

        Args:
            operator: The ApplicationOperator instance that owns this router.
        """
        self.operator = operator

    def __call__(self, state: _HealthMonitorState) -> str:
        """Determine the next node to route to.

        Args:
            state: The current graph state.

        Returns:
            The name of the next node or END.
        """
        raise NotImplementedError


class DeployNode(GraphNode):
    """Node that checks if the app is deployed and deploys if needed."""

    def __call__(self, state: _HealthMonitorState) -> _HealthMonitorState:
        """Check deployment status and deploy if necessary.

        This node handles both initial deployment and redeployment scenarios.
        If the app is already deployed and healthy, it skips deployment.
        If redeployment is needed (app_down flag), it will redeploy even if
        containers exist.

        Args:
            state: The current graph state.

        Returns:
            Updated state with deployment status.
        """
        needs_redeploy = state.get(
            "needs_redeploy",
            False) or state.get(
            "app_down",
            False)
        is_deployed = self.operator.app.is_deployed()

        # If already deployed and not flagged for redeployment, skip
        if is_deployed and not needs_redeploy:
            return {
                **state,
                "deployed": True,
                "deployment_message": "Application is already deployed",
                "needs_redeploy": False,
                "app_down": False
            }

        # If redeployment is needed, we may need to stop first
        if needs_redeploy and is_deployed:
            print(f"\n{'='*70}")
            print(f"  Redeploying Application: {self.operator.app.name}")
            print(f"  Application appears to be down - redeploying...")
            print(f"{'='*70}\n")
        else:
            # Deploy the application
            print(f"\n{'='*70}")
            print(f"  Deploying Application: {self.operator.app.name}")
            print(f"  Description: {self.operator.app.description}")
            print(f"{'='*70}\n")

        print(f"Starting deployment...")

        result = self.operator.app.deploy()

        if result.success:
            self.operator._deployed = True
            print(f"✓ {result.message}\n")
            if not state.get("deployed", False):
                # Only print the monitoring message on initial deployment
                print(f"{'='*70}")
                print(f"  Application is now running")
                print(
                    f"  Health checks will run every {self.operator.check_interval} seconds")
                print(f"  Press Ctrl+C to stop")
                print(f"{'='*70}\n")
            return {
                **state,
                "deployed": True,
                "deployment_message": result.message,
                "needs_redeploy": False,
                "app_down": False
            }
        else:
            print(f"✗ {result.message}", file=sys.stderr)
            return {
                **state,
                "deployed": False,
                "deployment_message": result.message,
                "needs_redeploy": True,
                "app_down": True
            }


class WaitIntervalNode(GraphNode):
    """Node that waits for the check interval before proceeding."""

    def __call__(self, state: _HealthMonitorState) -> _HealthMonitorState:
        """Wait in 1s increments so SIGINT/SIGTERM can stop quickly.

        Args:
            state: The current graph state.

        Returns:
            Updated state with shutdown_now flag set.
        """
        for _ in range(self.operator.check_interval):
            if self.operator._shutdown_requested:
                return {**state, "shutdown_now": True}
            time.sleep(1)
        return {**state, "shutdown_now": False}


class DoHealthCheckNode(GraphNode):
    """Node that performs the health check on the application."""

    def __call__(self, state: _HealthMonitorState) -> _HealthMonitorState:
        """Execute a health check and update the state.

        Args:
            state: The current graph state.

        Returns:
            Updated state with health check results.
        """
        check_count = int(state.get("check_count", 0)) + 1
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Save previous result before updating
        previous_result = state.get("result")

        try:
            result_obj = self.operator.app.health_check()
            result: dict[str, Any] = {
                "healthy": bool(result_obj.healthy),
                "message": str(result_obj.message),
                "details": result_obj.details or {},
            }
        except Exception as e:
            result = {
                "healthy": False,
                "message": f"Health check exception: {e}",
                "details": {"error": str(e)},
            }

        return {
            **state,
            "check_count": check_count,
            "timestamp": timestamp,
            "previous_result": previous_result,
            "result": result,
        }


class SummarizeAndRecommendNode(GraphNode):
    """Node that summarizes health check results using LLM and prints them."""

    @staticmethod
    def _truncate_text(text: str, *, max_chars: int = 4000,
                       head_chars: int = 2200, tail_chars: int = 1400) -> str:
        """Truncate text to fit within character limits, keeping head and tail.

        Args:
            text: The text to truncate.
            max_chars: Maximum total characters allowed.
            head_chars: Number of characters to keep from the start.
            tail_chars: Number of characters to keep from the end.

        Returns:
            Truncated text with ellipsis in the middle if needed.
        """
        if len(text) <= max_chars:
            return text
        head = text[:head_chars]
        tail = text[-tail_chars:]
        return f"{head}\n...\n{tail}"

    def _llm_compare_results(
            self, *, previous_result: dict[str, Any], current_result: dict[str, Any]) -> bool:
        """Determine if current result is meaningfully different from previous result.

        Args:
            previous_result: The previous health check result.
            current_result: The current health check result.

        Returns:
            True if results are meaningfully different, False otherwise.
        """
        prev_healthy = bool(previous_result.get("healthy", False))
        curr_healthy = bool(current_result.get("healthy", False))
        prev_message = str(previous_result.get("message", "")).strip()
        curr_message = str(current_result.get("message", "")).strip()

        # If boolean status changed, definitely different
        if prev_healthy != curr_healthy:
            return True

        # If message changed, consider it different
        if prev_message != curr_message:
            return True

        return False

    def _determine_app_status(
            self, *, result: dict[str, Any], is_deployed: bool) -> AppStatus:
        """Determine the application status (DOWN vs WARNING vs HEALTHY).

        Args:
            result: The health check result dictionary.
            is_deployed: Whether the app is currently deployed (containers running).

        Returns:
            AppStatus: The determined status of the application.
        """
        healthy = bool(result.get("healthy", False))
        message = str(result.get("message", "")).strip()
        details = result.get("details") or {}

        # If healthy, definitely not down
        if healthy:
            return AppStatus.HEALTHY

        # If not deployed, app is definitely down
        if not is_deployed:
            return AppStatus.DOWN

        # App is deployed but unhealthy - check if it's a critical failure
        exit_code = details.get("exit_code") if isinstance(
            details, dict) else None

        # If exit code is non-zero, check for critical failure keywords
        if exit_code is not None and exit_code != 0:
            message_lower = message.lower()
            down_keywords = [
                "not running",
                "crashed",
                "failed to start",
                "connection refused",
                "timeout",
                "not found",
                "no response"]
            if any(keyword in message_lower for keyword in down_keywords):
                return AppStatus.DOWN

        # Default to warning if we can't determine it's a critical failure
        return AppStatus.WARNING

    def _llm_summarize_health(self, *, result: dict[str, Any]) -> str:
        """Return a concise, operator-friendly health summary string.

        Args:
            result: The health check result dictionary.

        Returns:
            A summary string describing the health status.
        """
        healthy = bool(result.get("healthy", False))
        message = str(result.get("message", "")).strip()
        details = result.get("details") or {}

        # Make the prompt small and stable (avoid dumping huge outputs).
        output = ""
        if isinstance(details, dict):
            output = str(details.get("output", "") or "")
        output = self._truncate_text(output.strip()) if output else ""

        # Fast heuristic fallback (also used if LLM invocation fails).
        exit_code = details.get("exit_code") if isinstance(
            details, dict) else None
        fallback = f"{'HEALTHY' if healthy else 'UNHEALTHY'} - {message}"
        if exit_code is not None and not healthy:
            fallback += f" (exit_code={exit_code})"
        if output and not healthy:
            last_lines = "\n".join(
                [ln for ln in output.splitlines() if ln.strip()][-6:])
            if last_lines:
                fallback += f" | last_lines: {last_lines}"

        try:
            llm = self.operator._get_llm()
            system = SystemMessage(
                content="""
You summarize health-check results for an operator log.
Return 1-2 concise sentences.
Must start with either 'HEALTHY' or 'UNHEALTHY'.
If unhealthy, name the failing component(s) / symptom(s) if present.
If there are issues to fix, include actionable fixes or recommendations (e.g., 'check X configuration', 'restart Y service', 'verify Z is running').
Do not use markdown, bullets, or extra formatting."""
            )
            human = HumanMessage(
                content=(
                    f"app={self.operator.app.name}\n"
                    f"healthy={healthy}\n"
                    f"message={message}\n"
                    f"details_keys={list(details.keys()) if isinstance(details, dict) else []}\n"
                    + (f"exit_code={exit_code}\n" if exit_code is not None else "")
                    + (f"output_snippet:\n{output}\n" if output else "")
                )
            )
            resp = llm.invoke([system, human])
            text = (resp.content or "").strip()
            return text if text else fallback
        except Exception as e:
            # Keep the operator running, but log why the LLM summary failed.
            print(
                f"LLM summary failed: {type(e).__name__}: {e}",
                file=sys.stderr)
            # Keep output log-friendly, but make it clear this is a fallback.
            return f"{fallback} (LLM unavailable)"

    def __call__(self, state: _HealthMonitorState) -> _HealthMonitorState:
        """Summarize health check results, check deployment status, and determine if redeployment is needed.

        Args:
            state: The current graph state.

        Returns:
            Updated state with summary and deployment status.
        """
        timestamp = state.get("timestamp",
                              datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        check_count = int(state.get("check_count", 0))
        result = state.get("result") or {}
        previous_result = state.get("previous_result")

        # Check if application is actually deployed
        is_deployed = self.operator.app.is_deployed()

        # Determine if app is down (needs redeployment) vs just warnings
        app_status = self._determine_app_status(
            result=result, is_deployed=is_deployed)

        app_down = app_status == AppStatus.DOWN
        is_warning = app_status == AppStatus.WARNING

        # Use LLM to determine if result is meaningfully different from
        # previous
        result_changed = False

        if previous_result:
            result_changed = self._llm_compare_results(
                previous_result=previous_result,
                current_result=result
            )

        # Always generate a summary
        summary = self._llm_summarize_health(result=result)

        # If app is down, we need to redeploy (regardless of whether containers
        # exist)
        needs_redeploy = app_down

        if result_changed:
            # Result changed - provide detailed report
            if app_down:
                print(f"[{timestamp}] Health Check #{check_count}: {summary}")
                print(
                    f"[{timestamp}] ⚠ Application appears to be DOWN - will trigger redeployment")
            elif is_warning:
                print(f"[{timestamp}] Health Check #{check_count}: {summary}")
                print(
                    f"[{timestamp}] ⚠ Application has warnings but is still running")
            else:
                print(f"[{timestamp}] Health Check #{check_count}: {summary}")
        else:
            # Result unchanged - show status message and summary
            healthy = bool(result.get("healthy", False))
            status_str = "HEALTHY" if healthy else "UNHEALTHY"
            if previous_result:
                if app_down:
                    print(
                        f"[{timestamp}] Health Check #{check_count}: Status same as before - {status_str}. {summary}")
                    print(
                        f"[{timestamp}] ⚠ Application still DOWN - will trigger redeployment")
                else:
                    print(
                        f"[{timestamp}] Health Check #{check_count}: Status same as before - {status_str}. {summary}")
            else:
                # First check - provide full summary
                if app_down:
                    print(f"[{timestamp}] Health Check #{check_count}: {summary}")
                    print(
                        f"[{timestamp}] ⚠ Application appears to be DOWN - will trigger redeployment")
                else:
                    print(f"[{timestamp}] Health Check #{check_count}: {summary}")

        return {
            **state,
            "summary": summary,
            "deployed": is_deployed,
            "app_down": app_down,
            "needs_redeploy": needs_redeploy
        }


class RouteAfterDeploy(GraphRouter):
    """Router that determines next step after deployment."""

    def __call__(self, state: _HealthMonitorState) -> str:
        """Route to wait interval if deployment succeeded, or END if failed.

        Args:
            state: The current graph state.

        Returns:
            Next node name or END.
        """
        if state.get("shutdown_now") or self.operator._shutdown_requested:
            return END
        if state.get("deployed", False):
            return "wait_interval"
        else:
            # Deployment failed, exit
            return END


class RouteAfterWait(GraphRouter):
    """Router that determines next step after waiting interval."""

    def __call__(self, state: _HealthMonitorState) -> str:
        """Route to health check or END based on shutdown status.

        Args:
            state: The current graph state.

        Returns:
            Next node name or END.
        """
        if state.get("shutdown_now") or self.operator._shutdown_requested:
            return END
        return "do_health_check"


class RouteNext(GraphRouter):
    """Router that determines next step after summarizing."""

    def __call__(self, state: _HealthMonitorState) -> str:
        """Route based on shutdown status and deployment needs.

        Routes to:
        - END if shutdown requested
        - deploy if app is down and needs redeployment
        - wait_interval otherwise

        Args:
            state: The current graph state.

        Returns:
            Next node name or END.
        """
        if self.operator._shutdown_requested:
            return END

        # If app is down and needs redeployment, route to deploy node
        if state.get("needs_redeploy", False) or state.get("app_down", False):
            # Reset deployment status to trigger redeployment check
            return "deploy"

        return "wait_interval"


class ApplicationOperator:
    """Manages application deployment and monitoring.

    The operator handles:
    - Deploying applications
    - Running periodic health checks
    - Graceful shutdown on SIGINT/SIGTERM
    - Cleanup of resources
    """

    def __init__(self, app: Application, check_interval: int = 30):
        """Initialize the application operator.

        Args:
            app: The application to manage.
            check_interval: Seconds between health checks (default: 30).
        """
        self.app = app
        self.check_interval = check_interval
        self._shutdown_requested = False
        self._deployed = False
        self._llm: Optional[ChatOpenAI] = None
        self._checkpointer = MemorySaver()

    def run(self) -> int:
        """Deploy and monitor the application.

        This is the main entry point that:
        1. Sets up signal handlers
        2. Runs the graph which handles deployment and monitoring
        3. Handles graceful shutdown

        Returns:
            int: Exit code (0 for success, 1 for failure).
        """
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._handle_shutdown_signal)
        signal.signal(signal.SIGTERM, self._handle_shutdown_signal)

        try:
            # Run the graph which handles deployment and monitoring
            self._monitor_loop()

            # Check if deployment succeeded
            if not self._deployed:
                return 1

            return 0

        except Exception as e:
            print(f"\n✗ Unexpected error: {e}", file=sys.stderr)
            return 1
        finally:
            # Always try to cleanup
            self._cleanup()

    def _monitor_loop(self):
        """Run deployment and health checks using a LangGraph loop until shutdown is requested."""
        graph = self._build_healthcheck_graph()
        config = {"configurable": {"thread_id": f"{self.app.name}_monitor"}}
        # Start the graph which will deploy first, then monitor
        initial_state = {
            "check_count": 0,
            "deployed": False,
            "deployment_message": ""
        }
        # The graph will run until END is reached (either on shutdown or
        # deployment failure)
        graph.invoke(initial_state, config)

    def _build_healthcheck_graph(self):
        """Build a LangGraph that deploys the app, then periodically checks health and prints an LLM summary."""
        # Create node instances
        deploy_node = DeployNode(self)
        wait_interval_node = WaitIntervalNode(self)
        do_health_check_node = DoHealthCheckNode(self)
        summarize_and_recommend_node = SummarizeAndRecommendNode(self)

        # Create router instances
        route_after_deploy = RouteAfterDeploy(self)
        route_after_wait = RouteAfterWait(self)
        route_next = RouteNext(self)

        # Build the graph
        workflow = StateGraph(_HealthMonitorState)
        workflow.add_node("deploy", deploy_node)
        workflow.add_node("wait_interval", wait_interval_node)
        workflow.add_node("do_health_check", do_health_check_node)
        workflow.add_node(
            "summarize_and_recommend",
            summarize_and_recommend_node)

        workflow.add_edge(START, "deploy")
        workflow.add_conditional_edges(
            "deploy", route_after_deploy, {
                "wait_interval": "wait_interval", END: END})
        workflow.add_conditional_edges(
            "wait_interval", route_after_wait, {
                "do_health_check": "do_health_check", END: END})
        workflow.add_edge("do_health_check", "summarize_and_recommend")
        workflow.add_conditional_edges(
            "summarize_and_recommend", route_next, {
                "wait_interval": "wait_interval", END: END})

        return workflow.compile(checkpointer=self._checkpointer)

    def _get_llm(self) -> ChatOpenAI:
        """Get or create the LLM instance (lazy initialization).

        The LLM is shared across all nodes and initialized on first use.
        Model can be overridden via APP_OPERATOR_LLM_MODEL environment variable.

        Returns:
            ChatOpenAI: The LLM instance.
        """
        if self._llm is None:
            # Default model can be overridden by environment variables supported by langchain-openai.
            # Also allow explicit override via APP_OPERATOR_LLM_MODEL.
            import os

            model = os.getenv("APP_OPERATOR_LLM_MODEL", "gpt-4o-mini")
            self._llm = ChatOpenAI(
                model=model,
                temperature=0,
                timeout=30,
                max_retries=2)
        return self._llm

    def _handle_shutdown_signal(self, signum: int, frame) -> None:
        """Handle shutdown signals (SIGINT, SIGTERM).

        Args:
            signum: The signal number.
            frame: The current stack frame.
        """
        if not self._shutdown_requested:
            self._shutdown_requested = True
            signal_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
            print(
                f"\n\nReceived {signal_name} signal. Initiating graceful shutdown...")

    def _cleanup(self):
        """Shutdown the application and cleanup resources.

        This method is called during normal shutdown or after an error
        to ensure the application is properly stopped.
        """
        if not self._deployed:
            # Nothing to cleanup if we never deployed
            return

        print(f"\n{'='*70}")
        print(f"  Shutting Down: {self.app.name}")
        print(f"{'='*70}\n")

        print(f"Stopping application...")

        try:
            result = self.app.shutdown()

            if result.success:
                print(f"✓ {result.message}")
            else:
                print(f"⚠ {result.message}", file=sys.stderr)

        except Exception as e:
            print(f"✗ Error during shutdown: {e}", file=sys.stderr)

        print(f"\n{'='*70}")
        print(f"  Shutdown Complete")
        print(f"{'='*70}\n")
