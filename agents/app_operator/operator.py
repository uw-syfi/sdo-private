"""Main operator logic for managing application lifecycle."""

import signal
import sys
import time
from datetime import datetime
from typing import Any, Optional, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app_operator.application import Application


class _HealthMonitorState(TypedDict, total=False):
    check_count: int
    timestamp: str
    result: dict[str, Any]
    previous_result: dict[str, Any]
    summary: str
    shutdown_now: bool


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
    def _truncate_text(text: str, *, max_chars: int = 4000, head_chars: int = 2200, tail_chars: int = 1400) -> str:
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
    
    def _llm_compare_results(self, *, previous_result: dict[str, Any], current_result: dict[str, Any]) -> bool:
        """Use LLM to determine if current result is meaningfully different from previous result.
        
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
        prev_details = previous_result.get("details") or {}
        curr_details = current_result.get("details") or {}
        
        # Fast heuristic: if boolean status changed, definitely different
        if prev_healthy != curr_healthy:
            return True
        
        # If messages are identical and both healthy, likely the same
        if prev_healthy and curr_healthy and prev_message == curr_message:
            return False
        
        # Use LLM to compare if there are meaningful differences
        try:
            llm = self.operator._get_llm()
            system = SystemMessage(
                content="""
You compare two health check results to determine if they are meaningfully different.
Consider:
- Different error messages or symptoms indicate different issues
- Same status but different underlying problems are different
- Minor variations in messages that mean the same thing are NOT different
- Changes in exit codes or error details indicate differences

Respond with ONLY "DIFFERENT" or "SAME" (no other text)."""
            )
            
            # Prepare details comparison
            prev_output = ""
            curr_output = ""
            if isinstance(prev_details, dict):
                prev_output = str(prev_details.get("output", "") or "")
                prev_output = self._truncate_text(prev_output.strip(), max_chars=1000) if prev_output else ""
            if isinstance(curr_details, dict):
                curr_output = str(curr_details.get("output", "") or "")
                curr_output = self._truncate_text(curr_output.strip(), max_chars=1000) if curr_output else ""
            
            prev_exit_code = prev_details.get("exit_code") if isinstance(prev_details, dict) else None
            curr_exit_code = curr_details.get("exit_code") if isinstance(curr_details, dict) else None
            
            human = HumanMessage(
                content=(
                    f"Previous result:\n"
                    f"  healthy={prev_healthy}\n"
                    f"  message={prev_message}\n"
                    + (f"  exit_code={prev_exit_code}\n" if prev_exit_code is not None else "")
                    + (f"  output_snippet:\n{prev_output}\n" if prev_output else "")
                    + f"\nCurrent result:\n"
                    f"  healthy={curr_healthy}\n"
                    f"  message={curr_message}\n"
                    + (f"  exit_code={curr_exit_code}\n" if curr_exit_code is not None else "")
                    + (f"  output_snippet:\n{curr_output}\n" if curr_output else "")
                )
            )
            resp = llm.invoke([system, human])
            text = (resp.content or "").strip().upper()
            return "DIFFERENT" in text
        except Exception as e:
            # On LLM failure, fall back to comparing messages
            print(f"LLM comparison failed: {type(e).__name__}: {e}", file=sys.stderr)
            return prev_message != curr_message
    
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
        exit_code = details.get("exit_code") if isinstance(details, dict) else None
        fallback = f"{'HEALTHY' if healthy else 'UNHEALTHY'} - {message}"
        if exit_code is not None and not healthy:
            fallback += f" (exit_code={exit_code})"
        if output and not healthy:
            last_lines = "\n".join([ln for ln in output.splitlines() if ln.strip()][-6:])
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
            print(f"LLM summary failed: {type(e).__name__}: {e}", file=sys.stderr)
            # Keep output log-friendly, but make it clear this is a fallback.
            return f"{fallback} (LLM unavailable)"
    
    def __call__(self, state: _HealthMonitorState) -> _HealthMonitorState:
        """Summarize health check results and print them.
        
        Args:
            state: The current graph state.
            
        Returns:
            Updated state with summary added.
        """
        timestamp = state.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        check_count = int(state.get("check_count", 0))
        result = state.get("result") or {}
        previous_result = state.get("previous_result")

        # Use LLM to determine if result is meaningfully different from previous
        result_changed = False
        
        if previous_result:
            result_changed = self._llm_compare_results(
                previous_result=previous_result,
                current_result=result
            )

        # Always generate a summary
        summary = self._llm_summarize_health(result=result)
        
        if result_changed:
            # Result changed - provide detailed report
            print(f"[{timestamp}] Health Check #{check_count}: {summary}")
        else:
            # Result unchanged - show status message and summary
            healthy = bool(result.get("healthy", False))
            status_str = "HEALTHY" if healthy else "UNHEALTHY"
            if previous_result:
                print(f"[{timestamp}] Health Check #{check_count}: Status same as before - {status_str}. {summary}")
            else:
                # First check - provide full summary
                print(f"[{timestamp}] Health Check #{check_count}: {summary}")

        return {**state, "summary": summary}


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
        """Route back to wait interval or END based on shutdown status.
        
        Args:
            state: The current graph state.
            
        Returns:
            Next node name or END.
        """
        return END if self.operator._shutdown_requested else "wait_interval"


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
        2. Deploys the application
        3. Runs the health check monitoring loop
        4. Handles graceful shutdown
        
        Returns:
            int: Exit code (0 for success, 1 for failure).
        """
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._handle_shutdown_signal)
        signal.signal(signal.SIGTERM, self._handle_shutdown_signal)
        
        try:
            # Deploy the application
            if not self._deploy():
                return 1
            
            # Monitor the application
            self._monitor_loop()
            
            return 0
            
        except Exception as e:
            print(f"\n✗ Unexpected error: {e}", file=sys.stderr)
            return 1
        finally:
            # Always try to cleanup
            self._cleanup()
    
    def _deploy(self) -> bool:
        """Deploy the application.
        
        Returns:
            bool: True if deployment succeeded, False otherwise.
        """
        print(f"\n{'='*70}")
        print(f"  Deploying Application: {self.app.name}")
        print(f"  Description: {self.app.description}")
        print(f"{'='*70}\n")
        
        print(f"Starting deployment...")
        
        result = self.app.deploy()
        
        if result.success:
            self._deployed = True
            print(f"✓ {result.message}\n")
            print(f"{'='*70}")
            print(f"  Application is now running")
            print(f"  Health checks will run every {self.check_interval} seconds")
            print(f"  Press Ctrl+C to stop")
            print(f"{'='*70}\n")
            return True
        else:
            print(f"✗ {result.message}", file=sys.stderr)
            return False
    
    def _monitor_loop(self):
        """Run health checks using a LangGraph loop until shutdown is requested."""
        graph = self._build_healthcheck_graph()
        config = {"configurable": {"thread_id": f"{self.app.name}_monitor"}}
        # Sleep first before running the first health check (gives app time to start).
        graph.invoke({"check_count": 0}, config)

    def _build_healthcheck_graph(self):
        """Build a LangGraph that periodically checks health and prints an LLM summary."""
        # Create node instances
        wait_interval_node = WaitIntervalNode(self)
        do_health_check_node = DoHealthCheckNode(self)
        summarize_and_recommend_node = SummarizeAndRecommendNode(self)
        
        # Create router instances
        route_after_wait = RouteAfterWait(self)
        route_next = RouteNext(self)

        # Build the graph
        workflow = StateGraph(_HealthMonitorState)
        workflow.add_node("wait_interval", wait_interval_node)
        workflow.add_node("do_health_check", do_health_check_node)
        workflow.add_node("summarize_and_recommend", summarize_and_recommend_node)

        workflow.add_edge(START, "wait_interval")
        workflow.add_conditional_edges("wait_interval", route_after_wait, {"do_health_check": "do_health_check", END: END})
        workflow.add_edge("do_health_check", "summarize_and_recommend")
        workflow.add_conditional_edges("summarize_and_recommend", route_next, {"wait_interval": "wait_interval", END: END})

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
            self._llm = ChatOpenAI(model=model, temperature=0, timeout=30, max_retries=2)
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
            print(f"\n\nReceived {signal_name} signal. Initiating graceful shutdown...")
    
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
