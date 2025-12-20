"""Main operator logic for managing application lifecycle."""

import signal
import sys
import time
from datetime import datetime
from typing import Any, Optional, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

from app_operator.application import Application


class _HealthMonitorState(TypedDict, total=False):
    check_count: int
    timestamp: str
    result: dict[str, Any]
    summary: str
    shutdown_now: bool


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
        # Sleep first before running the first health check (gives app time to start).
        graph.invoke({"check_count": 0})

    def _build_healthcheck_graph(self):
        """Build a LangGraph that periodically checks health and prints an LLM summary."""

        def wait_interval(state: _HealthMonitorState) -> _HealthMonitorState:
            # Wait in 1s increments so SIGINT/SIGTERM can stop quickly.
            for _ in range(self.check_interval):
                if self._shutdown_requested:
                    return {**state, "shutdown_now": True}
                time.sleep(1)
            return {**state, "shutdown_now": False}

        def route_after_wait(state: _HealthMonitorState) -> str:
            if state.get("shutdown_now") or self._shutdown_requested:
                return END
            return "do_health_check"

        def do_health_check(state: _HealthMonitorState) -> _HealthMonitorState:
            check_count = int(state.get("check_count", 0)) + 1
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            try:
                result_obj = self.app.health_check()
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
                "result": result,
            }

        def summarize_and_print(state: _HealthMonitorState) -> _HealthMonitorState:
            timestamp = state.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            check_count = int(state.get("check_count", 0))
            result = state.get("result") or {}

            summary = self._llm_summarize_health(result=result)
            print(f"[{timestamp}] Health Check #{check_count}: {summary}")

            return {**state, "summary": summary}

        def route_next(state: _HealthMonitorState) -> str:
            return END if self._shutdown_requested else "wait_interval"

        workflow = StateGraph(_HealthMonitorState)
        workflow.add_node("wait_interval", wait_interval)
        workflow.add_node("do_health_check", do_health_check)
        workflow.add_node("summarize_and_print", summarize_and_print)

        workflow.add_edge(START, "wait_interval")
        workflow.add_conditional_edges("wait_interval", route_after_wait, {"do_health_check": "do_health_check", END: END})
        workflow.add_edge("do_health_check", "summarize_and_print")
        workflow.add_conditional_edges("summarize_and_print", route_next, {"wait_interval": "wait_interval", END: END})

        return workflow.compile()

    def _get_llm(self) -> ChatOpenAI:
        if self._llm is None:
            # Default model can be overridden by environment variables supported by langchain-openai.
            # Also allow explicit override via APP_OPERATOR_LLM_MODEL.
            import os

            model = os.getenv("APP_OPERATOR_LLM_MODEL", "gpt-4o-mini")
            self._llm = ChatOpenAI(model=model, temperature=0, timeout=30, max_retries=2)
        return self._llm

    @staticmethod
    def _truncate_text(text: str, *, max_chars: int = 4000, head_chars: int = 2200, tail_chars: int = 1400) -> str:
        if len(text) <= max_chars:
            return text
        head = text[:head_chars]
        tail = text[-tail_chars:]
        return f"{head}\n...\n{tail}"

    def _llm_summarize_health(self, *, result: dict[str, Any]) -> str:
        """Return a concise, operator-friendly health summary string."""
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
            llm = self._get_llm()
            system = SystemMessage(
                content=(
                    "You summarize health-check results for an operator log.\n"
                    "Return 1-2 concise sentences.\n"
                    "Must start with either 'HEALTHY' or 'UNHEALTHY'.\n"
                    "If unhealthy, name the failing component(s) / symptom(s) if present.\n"
                    "Do not use markdown, bullets, or extra formatting."
                )
            )
            human = HumanMessage(
                content=(
                    f"app={self.app.name}\n"
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
