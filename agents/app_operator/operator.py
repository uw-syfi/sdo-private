"""Main operator logic for managing application lifecycle."""

import signal
import sys
import time
from datetime import datetime
from typing import Optional

from app_operator.application import Application


class ApplicationOperator:
    """Manages application deployment and monitoring.
    
    The operator handles:
    - Deploying applications
    - Running periodic health checks
    - Graceful shutdown on SIGINT/SIGTERM
    - Cleanup of resources
    """
    
    def __init__(self, app: Application, check_interval: int = 10):
        """Initialize the application operator.
        
        Args:
            app: The application to manage.
            check_interval: Seconds between health checks (default: 10).
        """
        self.app = app
        self.check_interval = check_interval
        self._shutdown_requested = False
        self._deployed = False
    
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
        """Run health checks in a loop until shutdown is requested.
        
        This method runs health checks at regular intervals and logs
        the results. It continues until a shutdown signal is received.
        """
        check_count = 0
        
        while not self._shutdown_requested:
            # Sleep first before running the first health check
            # This gives the application time to fully start
            for _ in range(self.check_interval):
                if self._shutdown_requested:
                    return
                time.sleep(1)
            
            if self._shutdown_requested:
                return
            
            # Run health check
            check_count += 1
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            try:
                result = self.app.health_check()
                
                if result.healthy:
                    print(f"[{timestamp}] Health Check #{check_count}: ✓ {result.message}")
                else:
                    print(f"[{timestamp}] Health Check #{check_count}: ✗ {result.message}")
                    if result.details:
                        print(f"  Details: {result.details}")
                        
            except Exception as e:
                print(f"[{timestamp}] Health Check #{check_count}: ✗ Error - {e}")
    
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
